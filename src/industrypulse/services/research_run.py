"""真实数据研究编排：从仓储构造 FeatureFrame / TargetFrame 并跑冻结实验。

这补上了此前缺失的一环——`research/validation.py` 有完整的实验框架，但从未用
**真实观测**驱动过。本模块负责：

1. 按月末决策日从仓储做 PIT 查询，构造特征帧（每行带祖先最大可用时点）；
2. 用未来 h 个月的标签序列构造目标帧（`label_available_at` = 该期真正公布的时间）；
3. 交给 `run_experiment` 做时间折 + purge/embargo + 共同 OOS 比较。

纪律：任何一步缺数据就跳过该决策日并在报告里计数，绝不向前填充。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from industrypulse.contracts.common import TZ_SHANGHAI
from industrypulse.contracts.context import ResearchMode
from industrypulse.contracts.model import (
    FeatureFrame,
    FeatureRow,
    TargetFrame,
    TargetRow,
    TargetSpec,
)
from industrypulse.contracts.research import ApprovalState, ExperimentSpec, SplitKind, SplitSpec
from industrypulse.repositories import DuckDBObservationRepository


class _Clock:
    def __init__(self, as_of: datetime, snapshot_id: str) -> None:
        self.as_of = as_of
        self.research_mode = ResearchMode.RECONSTRUCTED_PIT
        self.dataset_snapshot_id = snapshot_id


@dataclass(frozen=True)
class DatasetBuild:
    features: FeatureFrame
    targets: TargetFrame
    decision_dates: tuple[date, ...]
    skipped: int


def _month_ends(start: date, end: date) -> list[date]:
    out: list[date] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        if month == 12:
            last = date(year, 12, 31)
            year, month = year + 1, 1
        else:
            nxt = date(year, month + 1, 1)
            last = date.fromordinal(nxt.toordinal() - 1)
            month += 1
        if start <= last <= end:
            out.append(last)
    return out


def build_frames(
    observations: DuckDBObservationRepository,
    *,
    snapshot_id: str,
    industry_id: str,
    feature_series: Sequence[str],
    label_series: str,
    start: date,
    end: date,
    horizon_months: int = 3,
    feature_spec_version: str = "g2-f1",
) -> DatasetBuild:
    """构造真实数据的研究帧。缺数据的决策日与样本直接跳过并计数。"""
    rows: list[FeatureRow] = []
    targets: list[TargetRow] = []
    decision_dates: list[date] = []
    skipped = 0
    for decision in _month_ends(start, end):
        clock = _Clock(datetime.combine(decision, time(17), tzinfo=TZ_SHANGHAI), snapshot_id)
        selection = observations.select(list(feature_series) + [label_series], clock)
        visible = {row.series_id: row for row in selection.selected}
        if label_series not in visible or visible[label_series].value is None:
            skipped += 1
            continue
        features: dict[str, float | None] = {}
        ancestors: list[datetime] = []
        for series_id in feature_series:
            row = visible.get(series_id)
            if row is not None and row.value is not None:
                features[series_id] = float(row.value)
                ancestors.append(row.available_at)
            else:
                features[series_id] = None
        if not ancestors:
            skipped += 1
            continue
        target_period_month = _add_months(date(decision.year, decision.month, 1), horizon_months)
        label_period = target_period_month.replace(day=1)
        future = observations.vintages_for(label_series, label_period)
        if not future:
            skipped += 1
            continue
        latest_future = max(future, key=lambda v: v.vintage_id)
        if latest_future.value is None or latest_future.publication_at is None:
            skipped += 1
            continue
        base_raw = visible[label_series].value
        assert base_raw is not None
        base_value = float(base_raw)
        label_value = 1.0 if float(latest_future.value) > base_value else 0.0
        rows.append(
            FeatureRow(
                decision_as_of=clock.as_of,
                industry_id=industry_id,
                features=features,
                missing_features=[name for name, value in features.items() if value is None],
                max_ancestor_available_at=max(ancestors),
            )
        )
        targets.append(
            TargetRow(
                industry_id=industry_id,
                decision_as_of=clock.as_of,
                target_period=label_period.strftime("%Y-%m"),
                value=label_value,
                label_available_at=latest_future.publication_at,
            )
        )
        decision_dates.append(decision)
    if not rows:
        raise ValueError("没有任何可用的决策日样本")
    features_frame = FeatureFrame(
        industry_id=industry_id,
        snapshot_id=f"{snapshot_id}:{feature_spec_version}",
        feature_spec_version=feature_spec_version,
        schema_hash=hashlib.sha256(json.dumps(list(feature_series)).encode()).hexdigest(),
        research_mode=ResearchMode.RECONSTRUCTED_PIT,
        built_at=datetime.now(TZ_SHANGHAI),
        rows=rows,
    )
    periods = sorted({row.target_period for row in targets})
    spec = _target_spec(industry_id, periods, horizon_months)
    return DatasetBuild(
        features=features_frame,
        targets=TargetFrame(spec=spec, rows=targets),
        decision_dates=tuple(decision_dates),
        skipped=skipped,
    )


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, 1)


def _target_spec(industry_id: str, periods: list[str], horizon: int) -> TargetSpec:
    from industrypulse.features import target_specs

    t1, _t2 = target_specs(industry_id)
    return t1.model_copy(update={"horizon_months": horizon})


def make_experiment_spec(
    *,
    experiment_id: str,
    features: FeatureFrame,
    targets: TargetFrame,
    train_end: date,
    outer_start: date,
    outer_end: date,
    feature_list: Sequence[str],
    seed: int = 20261019,
) -> ExperimentSpec:
    """构造冻结实验规格：外折 holdout + 内层早于 cutoff 的超参搜索折。"""
    split = SplitSpec(
        split_id="outer-1",
        kind=SplitKind.EXPANDING,
        train_start=features.rows[0].decision_as_of.date(),
        train_end=train_end,
        test_start=outer_start,
        test_end=outer_end,
        purge_months=3,
        embargo_months=1,
    )
    inner_train_end = date(train_end.year - 1, train_end.month, 1)
    inner_test_start = date(
        inner_train_end.year + (1 if inner_train_end.month == 12 else 0),
        1 if inner_train_end.month == 12 else inner_train_end.month + 1,
        1,
    )
    inner = SplitSpec(
        split_id="inner-1",
        kind=SplitKind.EXPANDING,
        train_start=features.rows[0].decision_as_of.date(),
        train_end=inner_train_end,
        test_start=inner_test_start,
        # 内层测试必须严格早于 cutoff，否则超参搜索会看到 OOS
        test_end=date(train_end.year, train_end.month, 1) - timedelta(days=1),
        purge_months=3,
        embargo_months=1,
    )
    return ExperimentSpec(
        experiment_id=experiment_id,
        code_sha="local-" + hashlib.sha256(experiment_id.encode()).hexdigest()[:12],
        dependencies_hash="uv-lock",
        dataset_manifest_hash=features.schema_hash,
        industry_map_version="steel-g2",
        pit_mode=ResearchMode.RECONSTRUCTED_PIT,
        train_cutoff=datetime.combine(train_end, time(23, 59), tzinfo=TZ_SHANGHAI),
        outer_splits=[split],
        inner_splits=[inner],
        feature_list=list(feature_list),
        model_grid={
            "M00_LAST": {},
            "M01_SEASONAL": {"window": [36, 60]},
            "M02_AR": {"lag": [1, 2]},
            "M03_LOGIT": {"C": [0.1, 1.0]},
        },
        random_seed=seed,
        target_definition=f"{targets.spec.target_id}@{targets.spec.definition_version}",
        baselines=["M00_LAST", "M01_SEASONAL", "M02_AR"],
        primary_metric="brier",
        secondary_metrics=["log_loss", "roc_auc"],
        approval_state=ApprovalState.FROZEN,
        created_at=datetime.now(TZ_SHANGHAI),
        notes="真实 NBS 观测 + 基线模型；RECONSTRUCTED PIT；结论上限为研究候选",
    )


__all__ = ["DatasetBuild", "build_frames", "make_experiment_spec"]
