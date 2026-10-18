"""Explicit economic target clocks and UNSCORABLE handling."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from industrypulse.contracts.model import TargetRow

from .pipeline import add_months


@dataclass(frozen=True)
class Release:
    period: date
    available_at: datetime
    value: float | None = None
    profit: float | None = None
    revenue: float | None = None
    basis: str = "same_basis"


def _row(
    industry: str,
    decision: datetime,
    period: date,
    value: float | None,
    available: datetime | None,
    reason: str | None = None,
) -> TargetRow:
    return TargetRow(
        industry_id=industry,
        decision_as_of=decision,
        target_period=period.strftime("%Y-%m"),
        value=value,
        label_available_at=available,
        unscorable_reason=reason,
    )


def _visible(releases: Sequence[Release], decision: datetime) -> list[Release]:
    return sorted(
        (r for r in releases if r.available_at <= decision),
        key=lambda r: (r.period, r.available_at),
    )


def build_t1(
    industry: str, decision: datetime, releases: Sequence[Release], delta: float = 0.0
) -> TargetRow:
    period = add_months(decision.date().replace(day=1), 3)
    visible = _visible(releases, decision)
    future = sorted(
        (r for r in releases if (r.period.year, r.period.month) == (period.year, period.month)),
        key=lambda r: r.available_at,
    )
    if not visible or not future or visible[-1].value is None or future[0].value is None:
        return _row(industry, decision, period, None, None, "MISSING_TARGET")
    base, label = visible[-1], future[0]
    assert base.value is not None and label.value is not None
    return _row(
        industry,
        decision,
        period,
        float(label.value - base.value > delta),
        max(base.available_at, label.available_at),
    )


def build_t2(
    industry: str, decision: datetime, releases: Sequence[Release], delta: float = 0.0
) -> TargetRow:
    visible = _visible(releases, decision)
    if not visible:
        return _row(industry, decision, decision.date(), None, None, "MISSING_BASE")
    base = visible[-1]
    # Count economic periods once: subsequent vintage revisions are not new announcements.
    periods: dict[date, Release] = {}
    for release in sorted(releases, key=lambda r: r.available_at):
        if release.available_at > decision and release.period > base.period:
            periods.setdefault(release.period, release)
    valid = [
        r
        for _, r in sorted(periods.items())
        if r.profit is not None and r.profit >= 0 and r.revenue is not None and r.revenue > 0
    ]
    if len(valid) < 3:
        return _row(
            industry,
            decision,
            add_months(base.period, 3),
            None,
            None,
            "MISSING_THIRD_VALID_RELEASE",
        )
    label = valid[2]
    if label.period.year != base.period.year:
        reason = "CROSS_YEAR"
    elif base.basis != label.basis:
        reason = "BASIS_CHANGED"
    elif base.profit is None or base.profit < 0 or base.revenue is None or base.revenue <= 0:
        reason = "INVALID_BASE_DENOMINATOR_OR_PROFIT"
    else:
        assert label.profit is not None and label.revenue is not None
        return _row(
            industry,
            decision,
            label.period,
            float(label.profit / label.revenue - base.profit / base.revenue > delta),
            max(base.available_at, label.available_at),
        )
    return _row(industry, decision, label.period, None, None, reason)
