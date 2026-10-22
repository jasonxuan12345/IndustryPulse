"""Same-year, third-release profitability target built from published observations."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time

from industrypulse.contracts import (
    TZ_SHANGHAI,
    FeatureFrame,
    FeatureRow,
    ResearchMode,
    TargetFrame,
    TargetRow,
)
from industrypulse.contracts.pit import effective_available_at
from industrypulse.features import target_specs
from industrypulse.repositories import DuckDBObservationRepository
from industrypulse.services.research_run import DatasetBuild, _Clock, _month_ends


def build_margin_frames(
    repository: DuckDBObservationRepository,
    *,
    snapshot_id: str,
    industry_id: str,
    feature_series: Sequence[str],
    prefix: str,
    start: date,
    end: date,
    research_mode: ResearchMode,
) -> DatasetBuild:
    profit_id, revenue_id = prefix + ".profit_ytd", prefix + ".revenue_ytd"
    final = _Clock(datetime.combine(end, time(23, 59), TZ_SHANGHAI), snapshot_id, research_mode)
    history = repository.fetch([profit_id, revenue_id], final).vintages
    profits = {item.observation_start: item for item in history if item.series_id == profit_id}
    revenue = {item.observation_start: item for item in history if item.series_id == revenue_id}
    features, targets, dates = [], [], []
    skipped = 0
    for day in _month_ends(start, end):
        clock = _Clock(datetime.combine(day, time(17), TZ_SHANGHAI), snapshot_id, research_mode)
        visible = repository.fetch(
            list(set(feature_series) | {profit_id, revenue_id}), clock
        ).vintages
        latest = {
            item.series_id: item
            for item in sorted(visible, key=lambda item: (item.observation_start, item.vintage_id))
        }
        profit, sales = latest.get(profit_id), latest.get(revenue_id)
        if (
            profit is None
            or sales is None
            or profit.observation_start != sales.observation_start
            or profit.value is None
            or sales.value is None
            or profit.value < 0
            or sales.value <= 0
        ):
            skipped += 1
            continue
        period = profit.observation_start
        future = [
            key
            for key in sorted(profits)
            if key > period
            and key.year == period.year
            and key in revenue
            and profits[key].publication_at
            and (profits[key].publication_at or clock.as_of) > clock.as_of
        ]
        if len(future) < 3:
            skipped += 1
            continue
        target_day = future[2]
        next_profit, next_sales = profits[target_day], revenue[target_day]
        if (
            next_profit.value is None
            or next_sales.value is None
            or next_profit.value < 0
            or next_sales.value <= 0
        ):
            skipped += 1
            continue
        values = {
            key: float(latest[key].value or 0)
            if key in latest and latest[key].value is not None
            else None
            for key in feature_series
        }
        features.append(
            FeatureRow(
                industry_id=industry_id,
                decision_as_of=clock.as_of,
                features=values,
                missing_features=[key for key, value in values.items() if value is None],
                max_ancestor_available_at=max(item.available_at for item in latest.values()),
            )
        )
        targets.append(
            TargetRow(
                industry_id=industry_id,
                decision_as_of=clock.as_of,
                target_period=target_day.strftime("%Y-%m"),
                value=float(next_profit.value / next_sales.value > profit.value / sales.value),
                label_available_at=max(
                    effective_available_at(
                        item,
                        research_mode,
                        trading_days=repository.trading_days,
                        conservative_lag_days=repository.conservative_lag_days,
                    )
                    for item in (next_profit, next_sales)
                ),
            )
        )
        dates.append(day)
    if not features:
        raise ValueError("利润率目标无可用样本；跨年、亏损或第三次有效发布不足会跳过")
    import hashlib

    frame = FeatureFrame(
        industry_id=industry_id,
        snapshot_id=snapshot_id + ":margin-v1",
        feature_spec_version="margin-v1",
        schema_hash=hashlib.sha256(str(list(feature_series)).encode()).hexdigest(),
        research_mode=research_mode,
        built_at=final.as_of,
        rows=features,
    )
    return DatasetBuild(
        frame, TargetFrame(spec=target_specs(industry_id)[1], rows=targets), tuple(dates), skipped
    )
