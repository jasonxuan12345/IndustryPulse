"""交易日历推导：以日行情文件存在性为证据，异常即中止。"""

from __future__ import annotations

from datetime import date

import pytest

from industrypulse.datahub.exchange_calendar import (
    ProbeError,
    build_calendar,
    derive_trading_days,
)


def _probe_factory(closed: set[date]):
    def probe(day: date) -> bool:
        return day not in closed

    return probe


def test_skips_weekends_without_probing() -> None:
    calls: list[date] = []

    def probe(day: date) -> bool:
        calls.append(day)
        return True

    days, evidence, report = derive_trading_days(
        probe, start=date(2026, 9, 28), end=date(2026, 10, 4), today=date(2026, 10, 19)
    )
    # 2026-10-03/04 是周末，不应触发探测
    assert all(day.weekday() < 5 for day in calls)
    assert calls == [
        date(2026, 9, 28),
        date(2026, 9, 29),
        date(2026, 9, 30),
        date(2026, 10, 1),
        date(2026, 10, 2),
    ]
    assert days == [
        date(2026, 9, 28),
        date(2026, 9, 29),
        date(2026, 9, 30),
        date(2026, 10, 1),
        date(2026, 10, 2),
    ]
    assert any(line.endswith("weekend") for line in evidence)
    assert report.probed == 5 and report.skipped_weekends == 2


def test_holidays_are_excluded_by_evidence() -> None:
    closed = {
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 5),
        date(2026, 10, 6),
        date(2026, 10, 7),
    }
    days, evidence, report = derive_trading_days(
        _probe_factory(closed),
        start=date(2026, 9, 30),
        end=date(2026, 10, 8),
        today=date(2026, 10, 19),
    )
    assert days == [date(2026, 9, 30), date(2026, 10, 8)]
    assert "2026-10-01 404" in evidence
    assert report.trading_days == 2


def test_future_range_is_refused() -> None:
    with pytest.raises(ProbeError, match="尚未发布|拒绝"):
        derive_trading_days(
            _probe_factory(set()),
            start=date(2026, 10, 1),
            end=date(2026, 10, 30),
            today=date(2026, 10, 19),
        )


def test_empty_result_is_refused() -> None:
    with pytest.raises(ProbeError, match="没有任何交易日"):
        derive_trading_days(
            lambda _day: False,
            start=date(2026, 10, 1),
            end=date(2026, 10, 9),
            today=date(2026, 10, 19),
        )


def test_build_calendar_is_valid_and_hashed() -> None:
    days, _evidence, report = derive_trading_days(
        _probe_factory({date(2026, 9, 25)}),
        start=date(2026, 9, 24),
        end=date(2026, 9, 30),
        today=date(2026, 10, 19),
    )
    calendar = build_calendar(days, report=report, source_ref="test-endpoint", version="test-v1")
    assert calendar.exchange == "SHFE"
    assert calendar.source_sha256 == report.evidence_sha256
    assert date(2026, 9, 25) not in calendar.days
    assert calendar.covers(days[0])


def test_invalid_ranges_are_rejected() -> None:
    with pytest.raises(ValueError, match="起始日"):
        derive_trading_days(
            _probe_factory(set()),
            start=date(2026, 9, 30),
            end=date(2026, 9, 1),
            today=date(2026, 10, 19),
        )
