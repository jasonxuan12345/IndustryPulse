from datetime import date, datetime
from zoneinfo import ZoneInfo

from industrypulse.features import Release, build_t1, build_t2


def test_t1_calendar_horizon_and_release_clock() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    decision = datetime(2026, 1, 30, 17, tzinfo=tz)
    published = datetime(2026, 5, 20, 10, tzinfo=tz)
    releases = [
        Release(date(2025, 12, 1), decision, value=2.0),
        Release(date(2026, 4, 1), published, value=3.0),
    ]
    target = build_t1("CN_NBS_C31", decision, releases)
    assert target.target_period == "2026-04"
    assert target.value == 1.0
    assert target.label_available_at == published
    assert build_t1("CN_NBS_C31", decision, releases[:1]).value is None


def test_margin_cross_year_and_zero_revenue() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    decision = datetime(2025, 11, 30, 17, tzinfo=tz)
    releases = [Release(date(2025, 10, 1), decision, profit=1.0, revenue=10.0)]
    for year, month in [(2025, 11), (2025, 12), (2026, 1)]:
        releases.append(
            Release(
                date(year, month, 1), datetime(2026, 2, month, tzinfo=tz), profit=2.0, revenue=10.0
            )
        )
    assert build_t2("CN_NBS_C31", decision, releases).unscorable_reason == "CROSS_YEAR"
