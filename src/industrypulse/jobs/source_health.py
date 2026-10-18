"""Source health observations; unknown update expectations remain explicit."""

from __future__ import annotations

from datetime import datetime, timedelta

from industrypulse.contracts import ContractBaseModel


class SourceHealth(ContractBaseModel):
    source_id: str
    state: str
    age_days: float | None
    last_success_at: datetime | None
    error_type: str | None


def assess_source(
    source_id: str,
    at: datetime,
    last_success: datetime | None,
    expected_window: timedelta | None,
    error_type: str | None = None,
) -> SourceHealth:
    if at.tzinfo is None or (last_success and last_success.tzinfo is None):
        raise ValueError("Timezone required")
    if expected_window is not None and expected_window.total_seconds() <= 0:
        raise ValueError("Update window must be positive")
    if last_success and last_success > at:
        raise ValueError("Future successful update")
    age = (at - last_success).total_seconds() / 86400 if last_success else None
    state = "unavailable" if last_success is None else "healthy"
    if last_success and expected_window is None:
        state = "unknown_update_window"
    elif last_success and expected_window and at - last_success > expected_window:
        state = "stale"
    if error_type:
        state = "failed_with_snapshot" if last_success else "unavailable"
    return SourceHealth(
        source_id=source_id,
        state=state,
        age_days=age,
        last_success_at=last_success,
        error_type=error_type,
    )
