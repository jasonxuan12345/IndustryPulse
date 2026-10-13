"""C-007 专项：实验可复现 / 回测因果链 / 任务状态机（§12.2/§12.4/INVEST-001）。"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from industrypulse.contracts import (
    ApprovalState,
    BacktestEvent,
    ExperimentSpec,
    JobState,
    JobStatus,
    SplitSpec,
)

_SPLIT = {
    "split_id": "fold-1",
    "kind": "expanding",
    "train_start": date(2020, 1, 1),
    "train_end": date(2023, 12, 31),
    "test_start": date(2024, 1, 1),
    "test_end": date(2024, 12, 31),
    "purge_months": 3,
    "embargo_months": 1,
}


def test_split_boundaries_strict() -> None:
    SplitSpec.model_validate(_SPLIT)
    # train_end == test_start 禁止（无 purge 缓冲）
    with pytest.raises(ValidationError):
        SplitSpec.model_validate({**_SPLIT, "test_start": date(2023, 12, 31)})


_EXPERIMENT = {
    "experiment_id": "exp-1",
    "code_sha": "abc1234",
    "dependencies_hash": "sha256:dep",
    "dataset_manifest_hash": "sha256:data",
    "industry_map_version": "v1",
    "pit_mode": "strict_pit",
    "train_cutoff": datetime.fromisoformat("2023-12-31T23:59:00+08:00"),
    "outer_splits": [_SPLIT],
    "feature_list": ["f1", "f2"],
    "model_grid": {"M06_GBM": {"leaves": [7, 15]}},
    "random_seed": 42,
    "target_definition": "T2@1",
    "baselines": ["M00_LAST"],
    "primary_metric": "brier_score",
    "created_at": datetime.fromisoformat("2025-01-01T00:00:00+08:00"),
}


def test_experiment_freeze_semantics() -> None:
    """§12.4：冻结后只读——approval_state 变更须新建 manifest（frozen 守护）。"""
    spec = ExperimentSpec.model_validate(_EXPERIMENT)
    assert spec.approval_state == ApprovalState.DRAFT
    frozen = spec.model_copy(update={"approval_state": ApprovalState.FROZEN})
    with pytest.raises(ValidationError):  # frozen=True：字段不可就地改
        frozen.random_seed = 43


def test_experiment_reproducibility_fields_complete() -> None:
    """C-007 验收：可复现字段一个都不能少。"""
    spec = ExperimentSpec.model_validate(_EXPERIMENT)
    for field in (
        "code_sha",
        "dependencies_hash",
        "dataset_manifest_hash",
        "train_cutoff",
        "outer_splits",
        "feature_list",
        "model_grid",
        "random_seed",
        "target_definition",
        "baselines",
        "primary_metric",
        "approval_state",
    ):
        assert getattr(spec, field) is not None or field == "approval_state"


_EVENT = {
    "event_id": "bt-1",
    "experiment_id": "exp-1",
    "asset_id": "ETF_SH_STEEL_A",
    "signal_at": "2025-10-31T17:00:00+08:00",
    "order_at": "2025-11-03T09:30:00+08:00",
    "fill_at": "2025-11-03T09:30:00+08:00",
    "side": "buy",
    "quantity": "1000",
    "price": "1.075",
}


def test_invest_001_order_cannot_precede_signal() -> None:
    BacktestEvent.model_validate(_EVENT)
    with pytest.raises(ValidationError, match="未来函数"):
        BacktestEvent.model_validate({**_EVENT, "order_at": "2025-10-31T16:00:00+08:00"})
    with pytest.raises(ValidationError, match="fill_at"):
        BacktestEvent.model_validate({**_EVENT, "fill_at": "2025-11-02T09:30:00+08:00"})


def test_job_terminal_state_requires_times_and_error() -> None:
    base = {
        "job_id": "j1",
        "job_name": "n",
        "state": "succeeded",
        "start_at": "2025-10-31T17:00:00+08:00",
        "end_at": "2025-10-31T17:05:00+08:00",
    }
    JobStatus.model_validate(base)
    with pytest.raises(ValidationError, match="start_at"):
        JobStatus.model_validate({**base, "start_at": None, "end_at": None})
    with pytest.raises(ValidationError, match="error"):
        JobStatus.model_validate({**base, "state": JobState.FAILED})
