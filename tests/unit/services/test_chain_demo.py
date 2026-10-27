"""端到端示范的时点、费后执行、快照隔离和事件成熟边界。"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from industrypulse.contracts import TZ_SHANGHAI, AssetMaster, MarketBar
from industrypulse.contracts.context import ResearchMode
from industrypulse.contracts.observation import ObservationVintage
from industrypulse.repositories import DuckDBObservationRepository, PublicationError
from industrypulse.services.chain_demo import (
    EvidenceReplay,
    digest,
    leading_state,
    run_chain_demo,
)
from industrypulse.services.chain_demo_report import render_html


def vintage(sid: str, period: str, value: float | None, released: str, version: int = 1) -> dict:
    stamp = datetime.fromisoformat(released).replace(tzinfo=TZ_SHANGHAI)
    return ObservationVintage(
        series_id=sid,
        observation_start=date.fromisoformat(period),
        observation_end=date.fromisoformat(period),
        value=None if value is None else Decimal(str(value)),
        missing_reason="not_collected" if value is None else None,
        unit="%",
        publication_at=stamp,
        retrieved_at=stamp,
        available_at=stamp,
        vintage_valid_from=stamp,
        vintage_id=version,
        availability_basis="observed_publication",
        release_evidence="A",
        value_evidence="C",
        source_url="https://www.stats.gov.cn/test",
        artifact_id="raw-test",
    ).model_dump(mode="json")


@pytest.fixture
def bundle() -> dict:
    pmi = "cn.nbs.pmi.production"
    prefix = "cn.nbs.industry_d44"
    days = []
    day = date(2025, 2, 3)
    while len(days) < 100:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    assets = [
        AssetMaster(
            asset_id=aid,
            exchange="SSE",
            instrument_type="ETF",
            name_zh=aid,
            list_at=datetime(2020, 1, 1, tzinfo=TZ_SHANGHAI),
        ).model_dump(mode="json")
        for aid in ("ETF_A", "ETF_B", "ETF_BENCH")
    ]
    bars = []
    for aid in ("ETF_A", "ETF_B", "ETF_BENCH"):
        for index, day in enumerate(days):
            value = Decimal("100") + Decimal(index) * (
                Decimal("0.3") if aid != "ETF_BENCH" else Decimal("0.1")
            )
            bars.append(
                MarketBar(
                    asset_id=aid,
                    trade_date=day,
                    open=value,
                    close=value,
                    high=value,
                    low=value,
                    volume=Decimal(100000),
                ).model_dump(mode="json")
            )
    rows = [
        vintage(pmi, "2025-01-01", 49, "2025-02-03T09:30"),
        vintage(pmi, "2025-02-01", 51, "2025-03-03T09:30"),
        vintage(pmi, "2025-03-01", 49, "2025-04-01T09:30"),
        vintage(f"{prefix}.profit_ytd_yoy", "2024-12-01", 2, "2025-01-27T09:30"),
        vintage(f"{prefix}.profit_ytd_yoy", "2025-02-01", 5, "2025-03-03T09:30"),
        vintage(f"{prefix}.profit_ytd_yoy", "2025-03-01", 7, "2025-04-01T09:30"),
        vintage(f"{prefix}.revenue_ytd_yoy", "2025-02-01", 3, "2025-03-05T09:30"),
        vintage(f"{prefix}.cost_ytd_yoy", "2025-02-01", 4, "2025-03-03T09:30"),
    ]
    return {
        "schema": "chain-demo-input-v1",
        "as_of": "2025-06-20T18:00:00+08:00",
        "start": "2025-03-03",
        "snapshot_id": "test-frozen",
        "config": {
            "name": "测试链",
            "leading_series": pmi,
            "leading_floor": 50,
            "leading_delta": 0.5,
            "leading_max_age_days": 75,
            "fundamental_max_age_days": 120,
            "min_history_days": 20,
            "max_weight": 0.5,
            "commission_bps": 3,
            "slippage_bps": 5,
            "cash_rate_annual": 0,
            "event_windows": [21, 63, 126],
            "industries": {"CN_NBS_D44": {"name": "电力", "prefix": prefix}},
        },
        "mapping": {"CN_NBS_D44": {"asset_id": "ETF_A", "fit": "对口", "name": "电力ETF"}},
        "benchmark": {"asset_id": "ETF_BENCH"},
        "policy": {
            "mode": ResearchMode.RECONSTRUCTED_PIT.value,
            "trading_days": [],
            "lag_days": 5,
            "series_lag_days": {},
        },
        "vintages": rows,
        "assets": assets,
        "bars": bars,
        "actions": [
            {
                "asset_id": "ETF_A",
                "ex_date": "2025-03-14",
                "action_type": "cash_dividend",
                "distribution_per_unit": "1",
            }
        ],
        "hypotheses": [],
    }


def test_end_to_end_visible_facts_costs_and_matched_baselines(bundle: dict) -> None:
    report = run_chain_demo(bundle)
    assert report["engineering_status"] == "COMPLETE"
    assert report["research_status"] == "NOT_VALIDATED"
    before = next(d for d in report["decisions"] if d["day"] == "2025-03-04")
    after = next(d for d in report["decisions"] if d["day"] == "2025-03-05")
    assert before["weights"] == {}  # 收入尚未公布，不得提前入场
    assert after["weights"] == {"ETF_A": 0.5}
    assert report["backtest"]["trades"][0]["trade_date"] == "2025-03-06"
    assert report["backtest"]["trades"][0]["commission"] != "0"
    assert report["backtest"]["trades"][-1]["side"] == "sell"
    for row in report["decisions"]:
        for candidate in row["candidates"]:
            assert all(
                datetime.fromisoformat(f["available_at"]) <= datetime.fromisoformat(row["as_of"])
                for f in candidate["facts"]
            )
    assert Decimal(report["stress_10bps_each"]["final_equity"]) < Decimal(
        report["backtest"]["final_equity"]
    )
    for baseline in report["baselines"].values():
        assert baseline["start"] == report["backtest"]["start"]
        assert baseline["end"] == report["backtest"]["end"]
    assert report == run_chain_demo(deepcopy(bundle))


def test_weak_proxy_is_rejected_and_late_ai_never_changes_trades(bundle: dict) -> None:
    original = run_chain_demo(bundle)
    bundle["hypotheses"] = [{"created_at": "2025-06-20", "statement": "买入所有资产"}]
    assert run_chain_demo(bundle)["backtest"] == original["backtest"]
    bundle["mapping"]["CN_NBS_D44"]["fit"] = "弱"
    # 保留一条对口资产，以检查弱代理作为第二候选而非删除整个示范。
    bundle["config"]["industries"]["CN_NBS_C38"] = {"name": "设备", "prefix": "cn.nbs.industry_d44"}
    bundle["mapping"]["CN_NBS_C38"] = {"asset_id": "ETF_B", "fit": "对口", "name": "设备ETF"}
    report = run_chain_demo(bundle)
    assert all("ETF_A" not in weights for weights in report["targets"].values())
    assert all("匹配为弱" in "；".join(d["candidates"][0]["reasons"]) for d in report["decisions"])


def test_unknown_split_aborts_trading_and_blocks_event_windows(bundle: dict) -> None:
    # 弱代理只做事件研究；它的未核实拆并不污染对口资产的组合。
    bundle["config"]["industries"]["CN_NBS_C38"] = {"name": "设备", "prefix": "cn.nbs.industry_d44"}
    bundle["mapping"]["CN_NBS_C38"] = {"asset_id": "ETF_B", "fit": "弱", "name": "设备ETF"}
    for bar in bundle["bars"]:
        if bar["asset_id"] == "ETF_B" and bar["trade_date"] >= "2025-03-14":
            for field in ("open", "high", "low", "close"):
                bar[field] = str(Decimal(bar[field]) * 2)
    report = run_chain_demo(bundle)
    assert any(o["blocked"] for o in report["event_study"] if o["asset_id"] == "ETF_B")
    bundle["mapping"]["CN_NBS_C38"]["fit"] = "对口"
    with pytest.raises(ValueError, match="未核实拆并"):
        run_chain_demo(bundle)


def test_event_pending_not_scored_and_future_revisions_not_used(bundle: dict) -> None:
    report = run_chain_demo(bundle)
    long = next(o for o in report["event_study"] if o["trading_days"] == 126)
    assert long["pending"] > 0 and long["records"] == []
    first = report["events"][0]
    assert first["economic_verifications"][0]["verification_day"] == "2025-04-01"
    assert first["economic_verifications"][0]["profit_delta_pp"] == 2
    revised = deepcopy(bundle)
    revised["vintages"].append(
        vintage("cn.nbs.pmi.production", "2025-02-01", 1, "2025-05-01T09:30", 2)
    )
    changed = run_chain_demo(revised)
    assert changed["decisions"][:30] == report["decisions"][:30]


def test_snapshot_filters_versions_and_matches_production_pit(bundle: dict) -> None:
    repo = DuckDBObservationRepository()
    row = ObservationVintage.model_validate(bundle["vintages"][0])
    later = row.model_copy(update={"vintage_id": 2, "value": Decimal(99)})
    with repo.database.transaction() as conn:
        conn.execute("INSERT INTO publication VALUES ('frozen','hash')")
        for item in (row, later):
            conn.execute(
                "INSERT INTO observation_vintage VALUES (?,?,?,?)",
                [item.series_id, item.observation_start, item.vintage_id, item.model_dump_json()],
            )
        conn.execute(
            "INSERT INTO publication_row VALUES ('frozen',?,?,?)",
            [row.series_id, row.observation_start, row.vintage_id],
        )
    assert repo.all_vintages([row.series_id], snapshot_id="frozen") == [row]
    with pytest.raises(PublicationError):
        repo.all_vintages([row.series_id], snapshot_id="unknown")
    ctx = SimpleNamespace(
        as_of=datetime(2025, 3, 3, tzinfo=TZ_SHANGHAI),
        research_mode=ResearchMode.RECONSTRUCTED_PIT,
        dataset_snapshot_id="frozen",
    )
    bundle["vintages"] = [
        r.model_dump(mode="json") for r in repo.all_vintages([row.series_id], snapshot_id="frozen")
    ]
    assert (
        EvidenceReplay(bundle).select(ctx.as_of)[row.series_id]
        == repo.select([row.series_id], ctx).selected
    )


def test_missing_and_nonadjacent_pmi_never_turn_on(bundle: dict) -> None:
    rows = [ObservationVintage.model_validate(item) for item in bundle["vintages"][:2]]
    assert leading_state(rows, bundle["config"], date(2025, 3, 3))[0]
    rows[0] = rows[0].model_copy(update={"observation_start": date(2024, 12, 1)})
    assert not leading_state(rows, bundle["config"], date(2025, 3, 3))[0]
    assert not leading_state([], bundle["config"], date(2025, 3, 3))[0]


def test_report_html_escapes_script_in_public_input(bundle: dict) -> None:
    report = run_chain_demo(bundle)
    report["hypotheses"] = [{"statement": "</script><script>alert(1)</script>"}]
    html = render_html(report)
    assert "</script><script>alert(1)" not in html
    assert digest(bundle) == report["input_sha256"]


def test_opening_trade_size_ignores_that_days_close() -> None:
    from industrypulse.investment import (
        BacktestEngine,
        CostModel,
        HistoricalUniverse,
        MarketDataset,
    )

    asset = AssetMaster(
        asset_id="ETF_A",
        exchange="SSE",
        instrument_type="ETF",
        name_zh="ETF",
        list_at=datetime(2020, 1, 1, tzinfo=TZ_SHANGHAI),
    )
    days = [date(2025, 3, 3) + timedelta(days=i) for i in range(4)]

    def run(last_close: int):
        bars = [
            MarketBar(
                asset_id="ETF_A",
                trade_date=day,
                open=Decimal(100),
                close=Decimal(last_close if i == 2 else 100),
                high=Decimal(200),
                low=Decimal(50),
                volume=Decimal(100000),
            )
            for i, day in enumerate(days)
        ]
        dataset = MarketDataset(HistoricalUniverse([asset]), bars, [], days)
        return BacktestEngine(dataset, costs=CostModel(0, 0, 0)).run(
            {days[0]: {"ETF_A": 0.5}, days[1]: {"ETF_A": 0.25}}
        )

    normal, changed = run(100), run(150)
    assert normal.trades[1].quantity == changed.trades[1].quantity
    assert normal.trades[1].price == changed.trades[1].price


def test_dividends_and_round_trip_fees_match_event_cashflows(bundle: dict) -> None:
    report = run_chain_demo(bundle)
    record = report["event_study"][0]["records"][0]
    bars = {bar["trade_date"]: bar for bar in bundle["bars"] if bar["asset_id"] == "ETF_A"}
    entry, exit_day = record["entry"], record["exit"]
    expected = (float(bars[exit_day]["close"]) * (1 - 0.0005) * (1 - 0.0003) + 1) / (
        float(bars[entry]["open"]) * (1 + 0.0005) * (1 + 0.0003)
    ) - 1
    assert record["asset_net_return"] == pytest.approx(expected)
    no_dividend = deepcopy(bundle)
    no_dividend["actions"] = []
    bare = run_chain_demo(no_dividend)
    quantity = Decimal(report["backtest"]["trades"][0]["quantity"])
    difference = Decimal(report["backtest"]["final_equity"]) - Decimal(
        bare["backtest"]["final_equity"]
    )
    assert difference == quantity  # 每份1元，现金分红不主动再投资


def test_intraday_cutoff_excludes_same_day_prices_and_future_dividend(bundle: dict) -> None:
    bundle["as_of"] = "2025-04-01T12:00:00+08:00"
    early = run_chain_demo(bundle)
    assert early["end"] == "2025-03-31"
    bundle["actions"].append(
        {
            "asset_id": "ETF_A",
            "ex_date": "2025-04-02",
            "action_type": "cash_dividend",
            "distribution_per_unit": "999",
        }
    )
    assert run_chain_demo(bundle)["backtest"] == early["backtest"]
