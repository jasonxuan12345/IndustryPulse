from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from industrypulse.ai_research.model_gateway import ModelConnection
from industrypulse.contracts import TZ_SHANGHAI
from industrypulse.contracts.domain import MarketBar
from industrypulse.contracts.observation import ObservationVintage
from industrypulse.services import hypothesis_research as ai
from industrypulse.services import ledger
from industrypulse.services import opportunity_risk as risk
from industrypulse.services import research_forecast as rf
from industrypulse.services import research_loop as loop
from industrypulse.services import trusted_baseline as baseline
from industrypulse.services.chain_demo import EvidenceReplay
from industrypulse.services.workbench import Workbench

NOW = datetime(2026, 10, 27, 18, tzinfo=TZ_SHANGHAI)
ROOT = Path(__file__).resolve().parents[3]


def vintage(period, value, *, kind="profit", published="2026-10-26T09:30:00+08:00", version=1):
    stamp = datetime.fromisoformat(published)
    day = date.fromisoformat(period)
    return ObservationVintage(
        series_id=f"cn.nbs.test.{kind}_ytd_yoy",
        observation_start=day,
        observation_end=day.replace(day=28),
        value=Decimal(str(value)),
        unit="%",
        publication_at=stamp,
        retrieved_at=stamp,
        available_at=stamp,
        vintage_valid_from=stamp,
        vintage_id=version,
        availability_basis="observed_publication",
        release_evidence="A",
        value_evidence="C",
        source_url="https://www.stats.gov.cn/evidence",
        artifact_id="original",
    )


def bar(aid, day, close=1, opening=None, volume=2000000):
    return MarketBar(
        asset_id=aid,
        trade_date=day,
        open=Decimal(str(opening or close)),
        high=Decimal(str(max(close, opening or close))),
        low=Decimal(str(min(close, opening or close))),
        close=Decimal(str(close)),
        volume=Decimal(volume),
        adj_method="none",
    )


@pytest.fixture
def bundle():
    policy = yaml.safe_load(
        (ROOT / "config/research/opportunity-policy.yaml").read_text(encoding="utf-8")
    )
    rows = [
        vintage("2026-08-01", 1),
        vintage("2026-09-01", 5),
        vintage("2026-09-01", 3, kind="revenue"),
        vintage("2026-09-01", 2, kind="cost"),
    ]
    days = [NOW.date() - timedelta(days=i) for i in range(100, -1, -1)]
    days = [d for d in days if d.weekday() < 5]
    bars = [
        bar(aid, day, 1 + i * step).model_dump(mode="json")
        for aid, step in (("ETF_159611", 0.001), ("ETF_510300", 0.0001))
        for i, day in enumerate(days)
    ]
    spec = {
        "industry_id": "CN_NBS_D44",
        "name": "电力",
        "profit_id": rows[0].series_id,
        "revenue_id": rows[2].series_id,
        "cost_id": rows[3].series_id,
        "iva_id": None,
        "series": sorted({r.series_id for r in rows}),
        "asset_id": "ETF_159611",
        "fit": "对口",
    }
    return {
        "spec": spec,
        "policy": {**policy, "lag_days": 5, "series_lag_days": {}, "trading_days": []},
        "vintages": [r.model_dump(mode="json") for r in rows],
        "bars": bars,
        "actions": [],
    }


def test_gate_distinguishes_economic_opportunity_from_unsupported_return_claim(bundle):
    item = risk.screen(bundle, bundle["spec"], NOW, {"status": "NOT_VALIDATED"}, True, "verified")
    assert item["economic_signal"] and item["evidence_score"] == 6
    assert not item["admitted"] and item["target_weight"] == 0
    assert item["expected_net_return"] is None
    assert "模型样本外准入" in item["blocking_reasons"]


@pytest.mark.parametrize(
    "change,blocked",
    [
        ("future_revision", False),
        ("missing_month", True),
        ("weak_proxy", True),
        ("missing_source", True),
        ("stale", True),
        ("zero_volume", True),
    ],
)
def test_gate_failures_and_future_revision(bundle, change, blocked):
    if change == "future_revision":
        bundle["vintages"].append(
            vintage("2026-09-01", -99, published="2026-11-01T09:30:00+08:00", version=2).model_dump(
                mode="json"
            )
        )
    elif change == "missing_month":
        bundle["vintages"][0]["observation_start"] = "2026-06-01"
    elif change == "weak_proxy":
        bundle["spec"]["fit"] = "弱"
    elif change == "stale":
        bundle["bars"] = [b for b in bundle["bars"] if b["trade_date"] < "2026-10-01"]
    elif change == "zero_volume":
        bundle["bars"][-1]["volume"] = "0"
        for b in bundle["bars"]:
            if b["asset_id"] == "ETF_159611":
                b["volume"] = "0"
    item = risk.screen(
        bundle, bundle["spec"], NOW, {"status": "VALIDATED"}, change != "missing_source", "proof"
    )
    assert item["admitted"] is (not blocked)


def test_price_features_ignore_future_and_intraday_quotes(bundle):
    original = rf.price_features(bundle, "ETF_159611", NOW)
    changed = copy.deepcopy(bundle)
    changed["bars"].append(bar("ETF_159611", date(2026, 10, 28), 100).model_dump(mode="json"))
    assert rf.price_features(changed, "ETF_159611", NOW) == original
    morning = NOW.replace(hour=10)
    expected = copy.deepcopy(bundle)
    expected["bars"] = [b for b in expected["bars"] if b["trade_date"] != "2026-10-27"]
    assert rf.price_features(bundle, "ETF_159611", morning) == rf.price_features(
        expected, "ETF_159611", morning
    )


def test_first_release_label_is_not_rewritten_by_later_revision(bundle):
    rows = [
        vintage("2026-08-01", 1, published="2026-09-10T09:30:00+08:00"),
        vintage("2026-10-01", 5, published="2026-10-20T09:30:00+08:00"),
        vintage("2026-10-01", -50, published="2026-10-25T09:30:00+08:00", version=2),
    ]
    bundle["vintages"] = [r.model_dump(mode="json") for r in rows]
    replay = EvidenceReplay(bundle)
    decision = rf.feature(replay, bundle, bundle["spec"], NOW.replace(month=9, day=30))
    value, released = rf.released_label(replay, bundle["spec"], decision, "T2", NOW, bundle)
    assert value == 1 and released.day == 20
    assert (
        rf.released_label(replay, bundle["spec"], decision, "T2", NOW.replace(day=19), bundle)
        is None
    )


def test_t3_counts_63_holding_days_and_requires_released_exit(bundle):
    replay = EvidenceReplay(bundle)
    first = date.fromisoformat(bundle["bars"][0]["trade_date"])
    decision = SimpleNamespace(
        decision_as_of=datetime.combine(first - timedelta(days=1), datetime.min.time(), TZ_SHANGHAI)
    )
    label = rf.released_label(replay, bundle["spec"], decision, "T3", NOW, bundle)
    assert label and label[0] == 1
    common = sorted({b["trade_date"] for b in bundle["bars"]})
    assert label[1].date().isoformat() == common[62]
    assert rf.released_label(replay, bundle["spec"], decision, "T3", label[1], bundle) is None


def test_allocation_caps_duplicate_asset_and_prioritizes_risk_exits(bundle):
    policy = bundle["policy"]
    items = [
        {
            "industry_id": f"CN_NBS_C{i}",
            "asset_id": f"ETF_00000{i}",
            "admitted": True,
            "evidence_score": 6 - i,
        }
        for i in range(4)
    ]
    result = risk.allocate(items, policy, {})
    assert sum(result["weights"].values()) <= 0.20 + 1e-9
    assert max(result["weights"].values()) <= 0.10
    exited = risk.allocate(items, policy, {"ETF_OLD": 0.3}, paused=True)
    assert not exited["weights"] and exited["exit_overrides_turnover"]


def test_shadow_fills_next_open_and_latches_portfolio_drawdown(bundle):
    days = [date(2026, 10, 27) + timedelta(days=i) for i in range(4)]
    bars = [
        bar(aid, d, 1 if i < 2 or aid == "ETF_510300" else 0.8 ** (i - 1))
        for aid in ("ETF_159611", "ETF_510300")
        for i, d in enumerate(days)
    ]
    market = SimpleNamespace(
        bars=lambda aid, start=None, end=None: [
            b
            for b in bars
            if b.asset_id == aid
            and (start is None or b.trade_date >= start)
            and (end is None or b.trade_date <= end)
        ],
        actions=lambda _: [],
    )
    state = risk.shadow_account(market, None, NOW, bundle["policy"])
    state["pending"] = {"signal_day": "2026-10-27", "weights": {"ETF_159611": 0.3}}
    assert risk.shadow_account(market, state, NOW, bundle["policy"])["units"] == {}
    filled = risk.shadow_account(market, state, NOW + timedelta(days=1), bundle["policy"])
    assert filled["units"]["ETF_159611"] > 0 and filled["nav"] < 1
    hit = risk.shadow_account(market, filled, NOW + timedelta(days=3), bundle["policy"])
    assert hit["paused"] and hit["max_drawdown"] < -0.1
    assert hit["pending"]["weights"] == {}


def test_sina_parser_keeps_table_rows_separate_and_excludes_split_rows():
    text = (
        "515220 历史分红<table><tr><td>2024/4/11</td><td>1970/1/1</td><td></td></tr>"
        "<tr><td>2023/12/27</td><td>2024/1/3</td><td>0.05</td></tr></table>合计"
    )
    records = baseline.parse_sina_dividends(text, "515220")
    assert records == [{"record_day": "2023-12-27", "pay_day": "2024-01-03", "per_unit": "0.05"}]
    with pytest.raises(ValueError):
        baseline.parse_sina_dividends("159611 历史分红<table></table>", "159611")


def test_decoder_uses_first_declared_charset_and_not_an_embedded_widget():
    content = '<meta charset="utf-8">累计分红：0元<script>charset=gb2312</script>'.encode()
    assert "累计分红" in baseline.decode(httpx.Response(200, content=content))


def test_empty_page_is_not_no_dividend_evidence():
    with pytest.raises(ValueError):
        baseline.parse_eastmoney("159611 空页", "159611")
    assert baseline.parse_eastmoney("159611 分红送配详情 暂无分红信息", "159611")["explicit_empty"]


def test_failed_calls_count_toward_budget_and_are_bounded(tmp_path):
    def broken(*args, **kwargs):
        raise ValueError("unavailable")

    result = ai.run_hypothesis_research(
        broken,
        None,
        {},
        events=[{"event_id": str(i)} for i in range(10)],
        max_events=2,
        now=NOW,
        budget_file=tmp_path / "budget",
        cache_file=tmp_path / "cache",
        hypotheses_path=tmp_path / "hyp",
        journal_path=tmp_path / "journal",
    )
    assert result["attempted"] == 2 and result["budget"]["used"] == 2
    assert len(result["failures"]) == 10


def test_pipeline_preserves_events_reuses_models_and_only_ai_processes_changed_inputs(
    tmp_path, bundle, monkeypatch
):
    service = Workbench(tmp_path)
    loop.configure(service, True)
    connection = ModelConnection(base_url="https://example.invalid/v1", model="test")
    monkeypatch.setattr(loop, "universe", lambda _: ([bundle["spec"]], bundle["policy"]))
    monkeypatch.setattr(loop, "freeze", lambda *_: copy.deepcopy(bundle))
    model_calls = []
    monkeypatch.setattr(
        rf,
        "forecast",
        lambda *args: model_calls.append(1) or {"status": "NOT_VALIDATED", "targets": []},
    )
    monkeypatch.setattr(baseline, "asset_ready", lambda *_: (True, "proof"))
    # Work only against this test's isolated book and empty temporary market.
    calls = []

    def complete(conn, prompt, *, system):
        calls.append(system)
        if "反证审查员" in system:
            return json.dumps(
                {
                    "counter_evidence": [
                        {
                            "fact_ref": "F2",
                            "observation": "仍需核对",
                            "note": "存在成本约束",
                            "weight": "contradicts",
                        }
                    ],
                    "verdict": "weakened",
                }
            )
        return json.dumps(
            {
                "channel": "demand",
                "statement": "需求改善需要下一期数据验证",
                "transmission": "营收传导至利润",
                "supporting": [{"fact_ref": "F2", "note": "当期数据提供线索"}],
                "invalidation_conditions": ["营收同比转负"],
                "observation_months": 3,
                "next_verification": "下一次利润公告",
            }
        )

    first = loop.run(service, now=NOW, complete=complete, connection=connection)
    assert first["checked"] == 1 and first["events_created"] == 1 and len(calls) == 2
    event_id = first["items"][0]["event_id"]
    original = (tmp_path / f"data/research-loop/events/{event_id}.json").read_bytes()
    history = ledger.load_ledger(tmp_path / "data/ledger/recommendations.jsonl")
    assert any(r["category"] == "economic_hypothesis" for r in history)
    repeated = loop.run(
        service, now=NOW + timedelta(minutes=15), complete=complete, connection=connection
    )
    assert repeated["events_created"] == 0 and len(model_calls) == 1 and len(calls) == 2
    assert len(ledger.load_ledger(tmp_path / "data/ledger/recommendations.jsonl")) == len(history)
    bundle["vintages"][1]["value"] = "6"
    changed = loop.run(
        service, now=NOW + timedelta(minutes=30), complete=complete, connection=connection
    )
    assert changed["events_created"] == 1 and len(calls) == 4
    newer = loop.read(
        tmp_path / f"data/research-loop/events/{changed['items'][0]['event_id']}.json"
    )
    assert newer["previous_event_id"] == event_id and newer["previous_hypothesis"]
    assert (tmp_path / f"data/research-loop/events/{event_id}.json").read_bytes() == original
    service.pool.shutdown()
    service.runner.close()


def test_news_routes_only_real_past_leads_and_never_demo(tmp_path):
    from industrypulse.services.research_news import leads
    from industrypulse.services.workbench import write_json

    service = Workbench(tmp_path)
    folder = tmp_path / "config/research"
    folder.mkdir(parents=True)
    (folder / "news-routing.yaml").write_text(
        "version: v1\nmax_age_days: 7\nmax_items_per_industry: 12\n"
        "keywords:\n  CN_NBS_D44: [电力]\n",
        encoding="utf-8",
    )
    item = {
        "id": "one",
        "title": "电力数据发布",
        "summary": "需回到原件核验",
        "url": "https://www.stats.gov.cn/article",
        "published_at": NOW.isoformat(),
        "time_kind": "published",
    }
    write_json(
        tmp_path / "data/news/cache.json",
        {
            "official": {
                "last_success": NOW.isoformat(),
                "items": [
                    item,
                    {**item, "id": "future", "published_at": (NOW + timedelta(days=1)).isoformat()},
                ],
            }
        },
    )
    assert len(leads(service, "CN_NBS_D44", NOW)) == 1
    write_json(tmp_path / "data/news/settings.json", {"mode": "demo"})
    assert leads(service, "CN_NBS_D44", NOW) == []
    service.pool.shutdown()
    service.runner.close()


def test_missing_economic_data_suspends_instead_of_falsely_revoking(tmp_path, bundle):
    service = Workbench(tmp_path)
    path = tmp_path / "data/ledger/recommendations.jsonl"
    original = ledger.register(
        source_kind="test",
        source_id="risk",
        category="asset_candidate",
        subject="power",
        statement="improving",
        asset="ETF_159611",
        horizon_months=3,
        evidence=["source"],
        invalidation_conditions=["profit no longer improves"],
        path=path,
        now=NOW,
    )
    item = {
        "asset_id": "ETF_159611",
        "economic_observed": False,
        "economic_signal": False,
        "data_risk_passed": False,
        "blocking_reasons": ["missing period"],
    }
    review = risk.review_registered(service, [item], NOW, bundle["policy"])
    assert review["actions"][0]["action"] == "suspend_new_exposure"
    assert (
        ledger.latest_versions(path)[original["record"]["recommendation_id"]]["status"]
        == "watching"
    )
    item.update(economic_observed=True)
    risk.review_registered(service, [item], NOW, bundle["policy"])
    assert (
        ledger.latest_versions(path)[original["record"]["recommendation_id"]]["status"]
        == "withdrawn"
    )
    service.pool.shutdown()
    service.runner.close()


def test_validation_accepts_explicit_comparisons_but_rejects_uncited_numbers():
    packet = {
        "facts": [
            {"ref": "F1", "value": 5, "period": "2026-09-01"},
            {"ref": "F2", "value": 2, "period": "2026-08-01"},
        ]
    }
    hypothesis = {
        "channel": "demand",
        "statement": "行业有所改善",
        "transmission": "需求传导到利润",
        "supporting": [{"fact_ref": "F1", "note": "2026年9月的5高于F2的2"}],
        "invalidation_conditions": ["下期下降"],
        "observation_months": 3,
        "next_verification": "下期公告",
    }
    counter = {
        "counter_evidence": [
            {
                "fact_ref": "F2",
                "weight": "contradicts",
                "observation": "仍需核验",
                "note": "另有约束",
            }
        ],
        "verdict": "weakened",
    }
    assert ai.validate_hypothesis(hypothesis, counter, packet)["status"] == "validated"
    hypothesis["supporting"][0]["note"] += "但盈利为999"
    assert ai.validate_hypothesis(hypothesis, counter, packet)["status"] == "rejected"
