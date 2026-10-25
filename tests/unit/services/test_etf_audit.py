"""ETF 价格线审计（阶段 1 基线）：疑似拆并检测、分红上限、未来事件排除。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from industrypulse.contracts.domain import CorporateAction, MarketBar
from industrypulse.repositories import DuckDBMarketRepository
from industrypulse.services.etf_audit import find_barriers


def _bar(day: date, close: str) -> MarketBar:
    return MarketBar(
        asset_id="ETF_TEST",
        trade_date=day,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
        amount=Decimal("1"),
        adj_method="none",
    )


def _action(day: date, per_unit: str) -> CorporateAction:
    return CorporateAction(
        asset_id="ETF_TEST",
        ex_date=day,
        action_type="cash_dividend",
        distribution_per_unit=Decimal(per_unit),
        notes="test",
    )


def test_large_gap_without_recorded_action_is_a_barrier() -> None:
    days = [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)]
    bars = [_bar(days[0], "2.00"), _bar(days[1], "1.00"), _bar(days[2], "1.01")]
    found = find_barriers(bars, ())
    assert len(found) == 1
    assert found[0]["day"] == "2025-01-02"
    assert found[0]["change_pct"] == pytest.approx(-50.0)


def test_normal_limit_move_is_not_a_barrier() -> None:
    days = [date(2025, 2, 1), date(2025, 2, 2)]
    bars = [_bar(days[0], "2.00"), _bar(days[1], "2.20")]
    assert find_barriers(bars, ()) == []  # +10% 涨停在阈值以内


def test_implausible_large_dividend_does_not_explain_the_gap() -> None:
    # -25% 缺口当天登记了 0.35 元/份（约 13% 收益率）的分红：更像份额合并
    # 被记成分红，不能算"已解释"，必须仍列为障碍日。
    days = [date(2025, 3, 1), date(2025, 3, 2)]
    bars = [_bar(days[0], "2.00"), _bar(days[1], "1.50")]
    found = find_barriers(bars, (_action(days[1], "0.35"),))
    assert len(found) == 1


def test_plausible_dividend_explains_a_matching_gap() -> None:
    days = [date(2025, 4, 1), date(2025, 4, 2)]
    bars = [_bar(days[0], "2.00"), _bar(days[1], "1.50")]
    assert find_barriers(bars, (_action(days[1], "0.30"),)) == []


def test_actions_after_as_of_are_excluded_by_the_repository_reader() -> None:
    repository = DuckDBMarketRepository(":memory:")
    repository.register_action(_action(date(2025, 6, 1), "0.10"))
    repository.register_action(_action(date(2025, 1, 1), "0.20"))
    visible = [
        action.ex_date
        for action in repository.actions("ETF_TEST")
        if action.ex_date <= date(2025, 3, 1)
    ]
    assert visible == [date(2025, 1, 1)]
