"""前瞻账本：原始定义、现金流、成熟边界、撤销留痕与并发写入。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from industrypulse.contracts import TZ_SHANGHAI, MarketBar
from industrypulse.services import ledger

NOW = datetime(2026, 1, 4, 20, tzinfo=TZ_SHANGHAI)


def candidate(path: Path, **overrides) -> dict:
    values = dict(
        source_kind="test",
        source_id="one",
        category="asset_candidate",
        subject="电力",
        statement="需求改善候选",
        asset="ETF_T",
        horizon_months=3,
        evidence=["https://www.stats.gov.cn/original"],
        invalidation_conditions=["利润同比下行"],
        now=NOW,
        path=path,
    )
    values.update(overrides)
    return ledger.register(**values)["record"]


class Market:
    def __init__(self, asset=None, benchmark=None, actions=()):
        days = []
        day = date(2026, 1, 5)
        while len(days) < 160:
            if day.weekday() < 5:
                days.append(day)
            day += timedelta(days=1)
        self.days = days
        self.data = {}
        for aid, values in (("ETF_T", asset), ("ETF_510300", benchmark)):
            values = values if values is not None else [100.0] * len(days)
            self.data[aid] = [
                MarketBar(
                    asset_id=aid,
                    trade_date=day,
                    open=Decimal(str(value)),
                    high=Decimal(str(value)),
                    low=Decimal(str(value)),
                    close=Decimal(str(value)),
                    volume=Decimal(100000),
                )
                for day, value in zip(days, values, strict=True)
            ]
        self.dividends = actions

    def bars(self, aid):
        return self.data.get(aid, [])

    def actions(self, aid):
        return [a for a in self.dividends if a.asset_id == aid]


def test_seed_keeps_real_capture_clock_and_never_infers_buy(tmp_path: Path):
    hypotheses, events = tmp_path / "hyp.jsonl", tmp_path / "events.jsonl"
    old = NOW - timedelta(days=3)
    hypotheses.write_text(
        json.dumps(
            {
                "hypothesis_id": "hyp-1",
                "created_at": old.isoformat(),
                "statement": "电力需求上升",
                "chain": "电力",
                "observation_months": 3,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    events.write_text(
        json.dumps(
            {
                "event_id": "evt-1",
                "first_seen_at": old.isoformat(),
                "what_changed": "PMI上升",
                "ai_status": "queued",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    path = tmp_path / "book.jsonl"
    first = ledger.seed(now=NOW, path=path, hypotheses_path=hypotheses, events_path=events)
    assert first["seeded"] == 2
    assert (
        ledger.seed(now=NOW, path=path, hypotheses_path=hypotheses, events_path=events)["seeded"]
        == 0
    )
    entries = ledger.load_ledger(path)
    assert all(
        e["created_at"] == NOW.isoformat() and e["source_at"] == old.isoformat() for e in entries
    )
    assert all(e["asset"] == "" for e in entries)
    report = ledger.daily_report(path=path, now=NOW)
    assert report["asset_evaluation"]["total"] == 0
    assert report["asset_evaluation"]["false_positive_rate"] is None


def test_versions_retain_original_definition_and_entry_never_moves(tmp_path: Path):
    path, market = tmp_path / "book.jsonl", Market()
    original = candidate(path)
    initial_bytes = path.read_bytes()
    first = NOW + timedelta(days=3)
    ledger.update(market, now=first, path=path)
    assert path.read_bytes().startswith(initial_bytes)
    latest = ledger.latest_versions(path)[original["recommendation_id"]]
    assert latest["metrics"]["entry_day"] == "2026-01-05"
    assert latest["created_at"] == original["created_at"]
    assert all(latest[key] == original[key] for key in ledger.IMMUTABLE)
    assert ledger.update(market, now=first + timedelta(minutes=1), path=path)["appended"] == 0
    ledger.update(market, now=first + timedelta(days=2), path=path)
    assert (
        ledger.latest_versions(path)[original["recommendation_id"]]["metrics"]["entry_day"]
        == "2026-01-05"
    )


def test_fee_dividend_and_drawdown_are_distinct_from_mae():
    market = Market()
    # 回撤10%，但净值始终高于入场资金：不能用MAE代替峰谷回撤。
    for i, value in enumerate((100, 119, 107.1, 110)):
        bar = market.data["ETF_T"][i]
        number = Decimal(str(value))
        market.data["ETF_T"][i] = bar.model_copy(
            update={"open": number, "high": number, "low": number, "close": number}
        )
    market.dividends = [
        SimpleNamespace(
            asset_id="ETF_T",
            ex_date=market.days[3],
            action_type="cash_dividend",
            distribution_per_unit=Decimal(1),
        )
    ]
    result = ledger.recommendation_metrics(
        market, "ETF_T", "ETF_510300", from_day=NOW.date(), to_day=market.days[3]
    )
    expected = (110 * (1 - 0.0005) * (1 - 0.0003) + 1) / (100 * (1 + 0.0005) * (1 + 0.0003)) - 1
    assert result["net_return_pct"] == pytest.approx(expected * 100)
    assert result["max_drawdown_pct"] == pytest.approx(-10)
    assert result["mae_pct"] > -0.2
    assert result["benchmark_return_pct"] < 0  # 平价行情也扣双边费
    assert not result["mature"]


def test_entry_exdate_does_not_pay_dividend_and_future_is_excluded():
    market = Market()
    base = ledger.recommendation_metrics(
        market, "ETF_T", "ETF_510300", from_day=NOW.date(), to_day=market.days[3]
    )
    market.dividends = [
        SimpleNamespace(
            asset_id="ETF_T",
            ex_date=day,
            action_type="cash_dividend",
            distribution_per_unit=Decimal(1),
        )
        for day in (market.days[0], market.days[-1])
    ]
    changed = ledger.recommendation_metrics(
        market, "ETF_T", "ETF_510300", from_day=NOW.date(), to_day=market.days[3]
    )
    assert base["net_return_pct"] == changed["net_return_pct"]


def test_calendar_window_weekend_exit_and_terminal_result_freeze(tmp_path: Path):
    path, market = tmp_path / "book.jsonl", Market()
    record = candidate(path)
    assert ledger.add_months(date(2026, 1, 31), 3) == date(2026, 4, 30)
    before = ledger.recommendation_metrics(
        market, "ETF_T", "ETF_510300", from_day=NOW.date(), to_day=date(2026, 4, 3)
    )
    assert not before["mature"]
    final = ledger.recommendation_metrics(
        market, "ETF_T", "ETF_510300", from_day=NOW.date(), to_day=date(2026, 4, 6)
    )
    assert final["review_due"] == "2026-04-05" and final["exit_day"] == "2026-04-06"
    assert final["mature"]
    assert final == ledger.recommendation_metrics(
        market, "ETF_T", "ETF_510300", from_day=NOW.date(), to_day=date(2026, 8, 1)
    )
    ledger.update(market, path=path, now=datetime(2026, 4, 6, 18, tzinfo=TZ_SHANGHAI))
    latest = ledger.latest_versions(path)[record["recommendation_id"]]
    assert latest["evaluation"] == "false_positive"
    assert (
        ledger.update(market, path=path, now=datetime(2026, 5, 6, 18, tzinfo=TZ_SHANGHAI))[
            "appended"
        ]
        == 0
    )


def test_missing_data_at_due_never_becomes_false_positive(tmp_path: Path):
    path = tmp_path / "book.jsonl"
    candidate(path)
    ledger.update(
        Market(), path=path, now=NOW + timedelta(days=1, hours=-8)
    )  # 次日12点，不能读当天收盘
    assert next(iter(ledger.latest_versions(path).values()))["metrics"]["status"] == "pending_entry"
    ledger.update(
        SimpleNamespace(bars=lambda _: [], actions=lambda _: []),
        path=path,
        now=NOW + timedelta(days=120),
    )
    report = ledger.daily_report(path=path, now=NOW + timedelta(days=120))
    assert report["items"][0]["status"] == "awaiting_data"
    assert report["asset_evaluation"]["false_positives"] == []
    assert report["asset_evaluation"]["false_positive_rate"] is None


def test_withdrawn_windows_remain_in_evaluation_without_survivorship(tmp_path: Path):
    path = tmp_path / "book.jsonl"
    record = candidate(path)
    ledger.mark(
        record["recommendation_id"],
        "withdrawn",
        reason="原条件消失",
        path=path,
        now=NOW + timedelta(days=3),
    )
    ledger.update(Market(), path=path, now=NOW + timedelta(days=120))
    latest = ledger.latest_versions(path)[record["recommendation_id"]]
    assert latest["status"] == "closed_withdrawn" and latest["metrics"]["mature"]
    assert latest["evaluation"] == "false_positive"
    report = ledger.weekly_report(
        path=path, now=NOW + timedelta(days=120), tool_journal=tmp_path / "empty"
    )
    assert len(report["asset_evaluation"]["false_positives"]) == 1
    assert report["reversals"] == []  # 不把本周到期当作本周判断反转


def test_economic_confirmation_needs_real_forward_event_and_evidence(tmp_path: Path):
    path = tmp_path / "book.jsonl"
    record = candidate(path, category="economic_hypothesis", asset="")
    for event_at, evidence in (
        (NOW.isoformat(), "https://source"),
        ((NOW + timedelta(days=3)).isoformat(), ""),
    ):
        with pytest.raises(ValueError):
            ledger.mark(
                record["recommendation_id"],
                "confirmed",
                reason="验证",
                evidence=evidence,
                event_at=event_at,
                now=NOW + timedelta(days=4),
                path=path,
            )
    confirmed = ledger.mark(
        record["recommendation_id"],
        "confirmed",
        reason="发布的营收符合方向",
        evidence="https://source",
        event_at=(NOW + timedelta(days=3)).isoformat(),
        now=NOW + timedelta(days=4),
        path=path,
    )
    assert confirmed["verification"]["lead_hours"] == 72
    assert confirmed["status"] == "watching"
    ledger.update(Market(), path=path, now=datetime(2026, 4, 4, 21, tzinfo=TZ_SHANGHAI))
    assert ledger.latest_versions(path)[record["recommendation_id"]]["status"] == "confirmed"
    with pytest.raises(ValueError):
        ledger.mark(
            record["recommendation_id"],
            "refuted",
            reason="事后反改",
            path=path,
            now=datetime(2026, 4, 5, tzinfo=TZ_SHANGHAI),
        )


@pytest.mark.parametrize("mutation", ["value", "tail", "partial"])
def test_tampering_and_truncation_stop_reads_and_writes(tmp_path: Path, mutation: str):
    path = tmp_path / "book.jsonl"
    candidate(path)
    candidate(path, source_id="two")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if mutation == "value":
        lines[0] = lines[0].replace("需求改善候选", "改写判断")
        altered = "".join(lines)
    elif mutation == "tail":
        altered = lines[0]
    else:
        altered = "".join(lines)[:-2]
    path.write_text(altered, encoding="utf-8")
    with pytest.raises(ledger.LedgerIntegrityError):
        ledger.load_ledger(path)
    with pytest.raises(ledger.LedgerIntegrityError):
        candidate(path, source_id="three")
    assert path.read_text(encoding="utf-8") == altered


def test_parallel_registration_is_idempotent_and_definitions_cannot_change(tmp_path: Path):
    path = tmp_path / "book.jsonl"
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: candidate(path), range(8)))
    assert len(ledger.load_ledger(path)) == 1
    assert len({r["recommendation_id"] for r in results}) == 1
    with pytest.raises(ValueError):
        candidate(path, statement="同ID改写")
    again = candidate(path, policy={**deepcopy(ledger.DEFAULT_POLICY), "commission_bps": 50})
    assert again["policy"]["commission_bps"] == 3  # 重试不得改首次口径


def test_missing_quotes_and_corporate_actions_are_not_scored():
    market = Market()
    market.data["ETF_T"].pop(1)
    blocked = ledger.recommendation_metrics(
        market, "ETF_T", "ETF_510300", from_day=NOW.date(), to_day=market.days[4]
    )
    assert blocked["status"] == "blocked" and not blocked["mature"]
    market = Market()
    market.dividends = [
        SimpleNamespace(
            asset_id="ETF_T",
            ex_date=market.days[2],
            action_type="share_split",
            distribution_per_unit=Decimal(0),
        )
    ]
    assert (
        ledger.recommendation_metrics(
            market, "ETF_T", "ETF_510300", from_day=NOW.date(), to_day=market.days[4]
        )["status"]
        == "blocked"
    )
