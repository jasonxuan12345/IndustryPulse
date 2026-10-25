from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from industrypulse.contracts import TZ_SHANGHAI
from industrypulse.economy.price_check import (
    PriceLine,
    Release,
    adjacent_periods,
    benjamini_hochberg,
    compare,
    forward_excess,
    signal_history,
)
from industrypulse.services.workbench import Workbench

ROOT = Path(__file__).resolve().parents[2]


def _at(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time().replace(hour=9, minute=30), TZ_SHANGHAI)


def test_signal_needs_rising_profit_growth_and_positive_revenue() -> None:
    periods = [date(2025, month, 1) for month in (3, 4, 5, 6)]
    values = [1, 3, 2, 5]
    profit = [(p, _at(p + timedelta(days=50)), v) for p, v in zip(periods, values, strict=True)]
    revenue = {periods[1]: 2.0, periods[2]: 2.0, periods[3]: -1.0}
    signals = [item.signal for item in signal_history(profit, revenue)]
    assert signals == [None, True, False, False]


def test_signal_never_compares_across_missing_periods() -> None:
    assert adjacent_periods(date(2024, 12, 1), date(2025, 2, 1))
    assert not adjacent_periods(date(2024, 11, 1), date(2025, 3, 1))
    periods = [date(2024, 10, 1), date(2025, 3, 1), date(2025, 4, 1)]
    values = [-90, 5, 8]
    profit = [(p, _at(p + timedelta(days=50)), v) for p, v in zip(periods, values, strict=True)]
    revenue = {period: 1.0 for period in periods}
    assert [item.signal for item in signal_history(profit, revenue)] == [None, None, True]


def test_entry_is_the_day_after_release_and_dividends_are_added_back() -> None:
    days = tuple(date(2025, 1, 1) + timedelta(days=i) for i in range(6))
    asset = PriceLine(days, (1.0, 1.0, 1.1, 1.2, 1.3, 1.4), ((days[3], 0.1),))
    benchmark = PriceLine(days, (1.0,) * 6)
    release = Release(date(2024, 11, 1), _at(days[0]), True)
    outcomes, pending, blocked = forward_excess([release], asset, benchmark, trading_days=2)
    assert pending == 0 and blocked == 0
    assert outcomes == [(True, pytest.approx((1.2 + 0.1) / 1.0 - 1))]


def test_windows_crossing_a_split_barrier_are_dropped() -> None:
    days = tuple(date(2025, 1, 1) + timedelta(days=i) for i in range(6))
    line = PriceLine(days, (1.0,) * 6)
    release = Release(date(2024, 11, 1), _at(days[0]), True)
    barriers = (days[2],)
    outcomes, pending, blocked = forward_excess(
        [release], line, line, trading_days=3, barriers=barriers
    )
    assert outcomes == [] and blocked == 1 and pending == 0
    # 障碍日等于入场日：入场价已是拆并后价格，窗口保留。
    outcomes, _pending, blocked = forward_excess(
        [release], line, line, trading_days=3, barriers=(days[1],)
    )
    assert len(outcomes) == 1 and blocked == 0


def test_releases_before_listing_or_without_a_full_window_are_not_scored() -> None:
    days = tuple(date(2025, 6, 1) + timedelta(days=i) for i in range(5))
    line = PriceLine(days, (1.0,) * 5)
    early = Release(date(2025, 3, 1), _at(date(2025, 4, 1)), True)
    late = Release(date(2025, 4, 1), _at(days[3]), False)
    outcomes, pending, _blocked = forward_excess([early, late], line, line, trading_days=3)
    assert outcomes == [] and pending == 1


def test_small_samples_are_not_tested() -> None:
    result = compare([(True, 0.1)] * 5 + [(False, 0.0)] * 10)
    assert result["status"] == "INSUFFICIENT_SAMPLE" and result["p_value"] is None


def test_circular_shift_p_value_rewards_a_consistent_gap() -> None:
    outcomes = [(i % 2 == 0, 0.05 if i % 2 == 0 else -0.05) for i in range(20)]
    result = compare(outcomes)
    assert result["status"] == "TESTED"
    assert result["difference"] == pytest.approx(0.1)
    assert result["p_value"] < 0.6


def test_benjamini_hochberg_matches_textbook_values() -> None:
    assert benjamini_hochberg([0.01, 0.04, 0.03, 0.2]) == pytest.approx(
        [0.04, 0.0533333, 0.0533333, 0.2]
    )


def test_every_price_line_points_at_a_real_industry() -> None:
    document = yaml.safe_load(
        (ROOT / "config/market/industry_price_lines.yaml").read_text(encoding="utf-8")
    )
    service = Workbench(ROOT)
    real = {
        service.taxonomy(path.stem).nodes[0].industry_id
        for path in (ROOT / "config/industries").glob("*.yaml")
        if service.taxonomy(path.stem).series and not service.taxonomy(path.stem).synthetic
    }
    assert set(document["industries"]) <= real
    assert "CN_NBS_C40" not in document["industries"]
    for spec in document["industries"].values():
        assert spec["fit"] in {"对口", "近似", "弱"}
        assert spec["asset_id"] == "ETF_" + spec["symbol"][2:]
        assert spec["fit"] == "对口" or spec.get("note")
