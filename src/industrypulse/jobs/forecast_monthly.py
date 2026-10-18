"""Monthly batch adapter. Calendar decisions are passed in by the feature clock."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol, TypeVar

from industrypulse.contracts import ContractBaseModel, JobStatus, PredictionFrame
from industrypulse.core.settings import Settings

T = TypeVar("T")


class Runner(Protocol):
    def run(
        self,
        name: str,
        key: str,
        action: Callable[[], T],
        *,
        retry_on: tuple[type[Exception], ...] = ...,
    ) -> tuple[JobStatus, T | None]: ...


class SnapshotPublisher(Protocol):
    def monthly(
        self, at: datetime, *, inputs_available: bool, produce: Callable[[], PredictionFrame]
    ) -> ContractBaseModel: ...


class MonthlyForecastFailed(RuntimeError):
    """Old snapshot is safe, but the job must remain retryable."""


def forecast_monthly(
    runner: Runner,
    store: SnapshotPublisher,
    at: datetime,
    snapshot_id: str,
    inputs_available: bool,
    produce: Callable[[], PredictionFrame],
    settings: Settings | None = None,
) -> tuple[JobStatus, ContractBaseModel | None]:
    _ = settings
    # Include frozen input ID so a newly published release can rerun the same month.
    key = f"forecast:{at.isoformat()}:{snapshot_id}:{inputs_available}"

    def execute() -> ContractBaseModel:
        view = store.monthly(at, inputs_available=inputs_available, produce=produce)
        reason = getattr(view, "reason", None)
        if reason is not None and reason != "NOT_YET_PUBLISHED":
            raise MonthlyForecastFailed("Prediction production failed; old snapshot retained")
        return view

    return runner.run("forecast_monthly", key, execute)
