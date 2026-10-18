"""Evidence-preserving six-dimension economy engine (E-001..006)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from statistics import median

from pydantic import Field

from industrypulse.contracts import (
    AnalysisContext,
    ContractBaseModel,
    EconomicDimension,
    EconomicDirection,
    IndicatorSpec,
    ObservationRepository,
    ObservationVintage,
    ResearchMode,
)


@dataclass
class _QueryContext:
    as_of: datetime
    research_mode: ResearchMode
    dataset_snapshot_id: str
    industry_id: str
    mapping_version: str


def transform(value: float, previous: float | None, kind: str) -> float | None:
    if kind == "none":
        return value
    if kind == "pmi_gap":
        return value - 50
    if previous is None:
        return None
    if kind in {"difference", "percentage_point"}:
        return value - previous
    if kind == "percent_change":
        return None if previous <= 0 else 100 * (value / previous - 1)
    raise ValueError(f"Unsupported transform: {kind}")


def ytd_increment(current: ObservationVintage, previous: ObservationVintage) -> float:
    """Explicit approximation, allowed only within one year and unchanged unit/series."""
    if (
        current.series_id != previous.series_id
        or current.unit != previous.unit
        or current.unit_scale != previous.unit_scale
        or current.observation_end.year != previous.observation_end.year
        or current.observation_end <= previous.observation_end
        or current.value is None
        or previous.value is None
    ):
        raise ValueError("Incompatible YTD observations")
    return float(current.value - previous.value)


class RobustScaler:
    def __init__(self) -> None:
        self.center: float | None = None
        self.scale = 1.0
        self.fitted_until: datetime | None = None

    def fit(self, history: Sequence[tuple[datetime, float]], cutoff: datetime) -> RobustScaler:
        if (
            cutoff.tzinfo is None
            or not history
            or any(t.tzinfo is None or t > cutoff or not isfinite(v) for t, v in history)
        ):
            raise ValueError("Empty or future training history")
        values = [v for _, v in history]
        self.center = median(values)
        self.scale = max(1.4826 * median([abs(v - self.center) for v in values]), 1e-9)
        self.fitted_until = cutoff
        return self

    def score(self, value: float) -> float:
        if self.center is None or not isfinite(value):
            raise ValueError("Scaler is not fitted")
        return max(-3.0, min(3.0, (value - self.center) / self.scale))


class IndicatorValue(ContractBaseModel):
    indicator_id: str
    dimension: EconomicDimension
    direction: EconomicDirection
    role: str
    value: float | None
    score: float | None
    ancestor_ids: list[str]
    source_urls: list[str]
    available_at: datetime | None
    missing_reason: str | None


class DimensionState(ContractBaseModel):
    level: float | None
    coverage: float
    missing_indicators: list[str]


class EconomyReport(ContractBaseModel):
    industry_id: str
    as_of: datetime
    research_mode: str
    snapshot_id: str
    dimensions: dict[str, DimensionState]
    level: float | None
    momentum: float | None
    breadth: float | None
    data_coverage: float
    release_recency_days: float | None
    drivers: list[IndicatorValue]
    risk_flags: list[str] = Field(default_factory=list)
    historical_reference: str = "training-window robust median/MAD; equal dimension weights"


class EconomyEngine:
    def __init__(self, repository: ObservationRepository) -> None:
        self.repository = repository

    def calculate(
        self,
        specs: Sequence[IndicatorSpec],
        ctx: AnalysisContext,
        scalers: dict[str, RobustScaler],
        previous_scores: dict[str, float] | None = None,
        level_history: Sequence[tuple[datetime, float]] = (),
    ) -> EconomyReport:
        active = [
            s
            for s in specs
            if ctx.industry_id in s.industry_ids and s.valid_from <= ctx.as_of.date()
        ]
        series = sorted({x for s in active for x in s.series_ids})
        if any(s.fitted_until is None or s.fitted_until > ctx.as_of for s in scalers.values()):
            raise ValueError("Scaler training cutoff exceeds decision clock or is not fitted")
        query = _QueryContext(
            ctx.as_of,
            ctx.research_mode,
            ctx.dataset_snapshot_id,
            ctx.industry_id,
            ctx.mapping_version,
        )
        rows = self.repository.fetch(series, query).vintages if series else []
        if any(v.available_at > ctx.as_of for v in rows):
            raise ValueError("Repository returned a future observation")
        values: list[IndicatorValue] = []
        for spec in active:
            inputs = [v for v in rows if v.series_id in spec.series_ids]
            # Multi-input formulas need explicit implementations; never average raw units.
            relevant = sorted(inputs, key=lambda v: (v.observation_end, v.vintage_id))
            latest = relevant[-1] if relevant and len(spec.series_ids) == 1 else None
            raw = float(latest.value) if latest and latest.value is not None else None
            prior = relevant[-2] if len(relevant) > 1 else None
            previous = float(prior.value) if prior and prior.value is not None else None
            if spec.transform == "yoy":
                comparison = next(
                    (
                        v
                        for v in reversed(relevant[:-1])
                        if latest
                        and v.observation_end.year == latest.observation_end.year - 1
                        and v.observation_end.month == latest.observation_end.month
                    ),
                    None,
                )
                previous = (
                    float(comparison.value) if comparison and comparison.value is not None else None
                )
                kind = "percent_change"
            else:
                kind = spec.transform
            converted = transform(raw, previous, kind) if raw is not None else None
            score = None
            if (
                converted is not None
                and spec.indicator_id in scalers
                and spec.economic_direction != EconomicDirection.UNKNOWN
            ):
                score = scalers[spec.indicator_id].score(converted)
                if spec.economic_direction == EconomicDirection.NEGATIVE:
                    score = -score
            values.append(
                IndicatorValue(
                    indicator_id=spec.indicator_id,
                    dimension=spec.dimension,
                    direction=spec.economic_direction,
                    role=spec.role.value,
                    value=converted,
                    score=score,
                    ancestor_ids=[
                        f"{v.series_id}:{v.period_label}:v{v.vintage_id}" for v in relevant
                    ],
                    source_urls=sorted({str(v.source_url) for v in relevant if v.source_url}),
                    available_at=max((v.available_at for v in relevant), default=None),
                    missing_reason=None
                    if score is not None
                    else "missing_input_or_training_reference",
                )
            )
        dimensions: dict[str, DimensionState] = {}
        for dim in EconomicDimension:
            members = [v for v in values if v.dimension == dim]
            scores = [v.score for v in members if v.score is not None]
            dimensions[dim.value] = DimensionState(
                level=median(scores) if scores else None,
                coverage=len(scores) / len(members) if members else 0,
                missing_indicators=[v.indicator_id for v in members if v.score is None],
            )
        levels = [d.level for d in dimensions.values() if d.level is not None]
        level = median(levels) if levels else None
        # Last three monthly observations, causal least-squares slope (units/month).
        history = sorted([(t, v) for t, v in level_history if t < ctx.as_of])[-2:]
        momentum = None
        if level is not None and len(history) == 2:
            samples = [*history, (ctx.as_of, level)]
            x = [(t - samples[0][0]).total_seconds() / (86400 * 30.4375) for t, _ in samples]
            y = [v for _, v in samples]
            xm, ym = sum(x) / 3, sum(y) / 3
            denominator = sum((v - xm) ** 2 for v in x)
            momentum = sum((a - xm) * (b - ym) for a, b in zip(x, y, strict=True)) / denominator
        comparable = [(v.score, (previous_scores or {}).get(v.indicator_id)) for v in values]
        changes = [a - b for a, b in comparable if a is not None and b is not None]
        releases = [v.available_at for v in values if v.available_at]
        coverage = sum(v.score is not None for v in values) / len(values) if values else 0
        return EconomyReport(
            industry_id=ctx.industry_id,
            as_of=ctx.as_of,
            research_mode=ctx.research_mode.value,
            snapshot_id=ctx.dataset_snapshot_id,
            dimensions=dimensions,
            level=level,
            momentum=momentum,
            breadth=sum(c > 0 for c in changes) / len(changes) if changes else None,
            data_coverage=coverage,
            release_recency_days=(ctx.as_of - max(releases)).total_seconds() / 86400
            if releases
            else None,
            drivers=values,
            risk_flags=["PARTIAL_COVERAGE"] if coverage < 1 else [],
        )
