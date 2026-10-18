"""Train-only median/scaling; missing indicators survive preprocessing."""

from __future__ import annotations

import calendar
import hashlib
import json
import math
import statistics
from collections.abc import Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

from industrypulse.contracts.model import FeatureFrame, TargetSpec

CANDIDATE_FEATURES = (
    "profit_margin_latest",
    "profit_yoy",
    "profit_yoy_delta",
    "revenue_yoy",
    "revenue_yoy_delta",
    "iva_yoy_l1",
    "iva_yoy_delta",
    "steel_output_yoy",
    "steel_output_accel",
    "rb_ret_20d",
    "rb_ret_60d",
    "rb_vol_20d",
    "hc_ret_20d",
    "hc_ret_60d",
    "hc_vol_20d",
    "inventory_change_4w",
    "shfe_inventory_z",
    "pmi_order_delta",
    "pmi_price_delta",
    "pmi_inventory_delta",
    "tsf_growth_delta",
    "price_up_inventory_down",
    "source_age_days",
    "coverage_ratio",
    "evidence_grade",
)


def decision_calendar(trading_days: Sequence[date]) -> tuple[datetime, ...]:
    """Use the supplied exchange calendar; never infer holidays from weekdays."""
    months: dict[tuple[int, int], date] = {}
    for day in trading_days:
        key = day.year, day.month
        months[key] = max(months.get(key, day), day)
    return tuple(
        datetime(d.year, d.month, d.day, 17, tzinfo=ZoneInfo("Asia/Shanghai"))
        for _, d in sorted(months.items())
    )


def add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def target_specs(industry_id: str) -> tuple[TargetSpec, TargetSpec]:
    common = dict(
        definition_version="1",
        industry_id=industry_id,
        horizon_months=3,
        baselines=["M00_LAST", "M01_SEASONAL", "M02_AR"],
    )
    return (
        TargetSpec(
            **common,
            target_id="T1_IVA_DIRECTION",
            label_kind="direction_up",
            formula="1[IVA_YOY(m+3)-latest_visible_IVA_YOY>delta]",
            missing_policy="UNSCORABLE; no forward-filled labels",
            description="Three complete calendar months; label clock is release clock",
        ),
        TargetSpec(
            **common,
            target_id="T2_MARGIN_IMPROVE",
            label_kind="direction_up",
            formula="1[third_valid_release_margin-latest_visible_margin>delta]",
            missing_policy="UNSCORABLE on cross-year, negative profit, nonpositive revenue",
            description="Same-year cumulative margin; changed basis is unscorable",
        ),
    )


class FeaturePipeline:
    def __init__(self, columns: Sequence[str]) -> None:
        if not columns or len(columns) > 25 or len(set(columns)) != len(columns):
            raise ValueError("Require 1..25 unique preregistered features")
        self.columns = tuple(columns)
        self.medians: tuple[float, ...] = ()
        self.scales: tuple[float, ...] = ()
        self.fit_end: datetime | None = None
        self._cache: dict[str, tuple[tuple[float, ...], ...]] = {}

    def fit(self, frame: FeatureFrame, cutoff: datetime) -> FeaturePipeline:
        if cutoff.tzinfo is None or any(r.decision_as_of > cutoff for r in frame.rows):
            raise ValueError("Preprocessing fit sees future decisions")
        medians, scales = [], []
        for column in self.columns:
            values = [r.features[column] for r in frame.rows if r.features.get(column) is not None]
            observed = [float(v) for v in values if v is not None]
            if any(not math.isfinite(v) for v in observed):
                raise ValueError("Nonfinite feature")
            medians.append(statistics.median(observed) if observed else 0.0)
            scales.append(statistics.pstdev(observed) if len(observed) > 1 else 1.0)
        self.medians = tuple(medians)
        self.scales = tuple(s or 1.0 for s in scales)
        self.fit_end = cutoff
        self._cache.clear()
        return self

    def transform(self, frame: FeatureFrame) -> tuple[tuple[float, ...], ...]:
        if self.fit_end is None:
            raise ValueError("Pipeline must be fitted on training data")
        if any(r.max_ancestor_available_at > r.decision_as_of for r in frame.rows):
            raise ValueError("Future ancestor in feature snapshot")
        key = hashlib.sha256(frame.model_dump_json().encode()).hexdigest()
        if key not in self._cache:
            result = []
            for row in frame.rows:
                values, flags = [], []
                for col, median, scale in zip(self.columns, self.medians, self.scales, strict=True):
                    value = row.features.get(col)
                    if value is not None and not math.isfinite(value):
                        raise ValueError("Nonfinite feature")
                    values.append(((value if value is not None else median) - median) / scale)
                    flags.append(float(value is None))
                result.append(tuple(values + flags))
            self._cache[key] = tuple(result)
        return self._cache[key]

    @property
    def schema_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.columns).encode()).hexdigest()
