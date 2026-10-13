"""C-006 专项：特征/目标/预测的反泄漏不变式（§6.6/§10.5/PIT-006/ PIT-007）。"""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from industrypulse.contracts import (
    FeatureFrame,
    FeatureRow,
    LabeledFeatureFrame,
    Prediction,
    TargetRow,
    TargetSpec,
)

_D = "2025-06-30T17:00:00+08:00"


def _row(as_of: str = _D, ancestor: str = _D) -> FeatureRow:
    return FeatureRow(
        decision_as_of=datetime.fromisoformat(as_of),
        industry_id="CN_NBS_C31",
        features={"a": 1.0},
        max_ancestor_available_at=datetime.fromisoformat(ancestor),
    )


def test_pit_007_feature_ancestor_cannot_be_future() -> None:
    with pytest.raises(ValidationError, match="泄漏"):
        _row(as_of="2025-06-30T17:00:00+08:00", ancestor="2025-07-01T09:00:00+08:00")


def test_feature_rows_must_be_sorted() -> None:
    base = {
        "industry_id": "CN_NBS_C31",
        "snapshot_id": "sha256:abc12345",
        "feature_spec_version": "v1",
        "schema_hash": "sha256:def67890",
        "research_mode": "strict_pit",
        "built_at": "2025-07-01T09:00:00+08:00",
    }
    rows = [_row("2025-07-31T17:00:00+08:00"), _row("2025-06-30T17:00:00+08:00")]
    with pytest.raises(ValidationError, match="升序"):
        FeatureFrame.model_validate({**base, "rows": [r.model_dump() for r in rows]})


def test_pit_006_label_without_availability_rejected() -> None:
    base = {
        "industry_id": "CN_NBS_C31",
        "decision_as_of": _D,
        "target_period": "2025-09",
        "value": 1.0,
        "label_available_at": None,
    }
    with pytest.raises(ValidationError, match="label_available_at"):
        TargetRow.model_validate(base)
    # 测试开始后才公布的标签：模型层允许存在，但可用日必须如实声明晚于 decision
    row = TargetRow.model_validate({**base, "label_available_at": "2025-10-28T09:30:00+08:00"})
    assert row.label_available_at is not None
    assert row.label_available_at > row.decision_as_of


def test_unscorable_requires_reason() -> None:
    base = {
        "industry_id": "CN_NBS_C31",
        "decision_as_of": _D,
        "target_period": "2025-01",
        "value": None,
        "label_available_at": None,
        "unscorable_reason": "year_reset_excluded",
    }
    TargetRow.model_validate(base)
    with pytest.raises(ValidationError, match="unscorable_reason"):
        TargetRow.model_validate({**base, "unscorable_reason": None})


def test_labeled_frame_alignment() -> None:
    frame = FeatureFrame(
        industry_id="CN_NBS_C31",
        snapshot_id="sha256:abc12345",
        feature_spec_version="v1",
        schema_hash="sha256:def67890",
        research_mode="strict_pit",
        built_at=datetime.fromisoformat("2025-07-01T09:00:00+08:00"),
        rows=[_row()],
    )
    with pytest.raises(ValidationError, match="等长"):
        LabeledFeatureFrame(features=frame, labels=[1.0, 0.0], label_available_at=[None, None])


def test_prediction_probability_bounds() -> None:
    base = {
        "prediction_id": "p1",
        "model_id": "M03_LOGIT",
        "model_version": "m03@1",
        "industry_id": "CN_NBS_C31",
        "target_id": "T2",
        "as_of": _D,
        "horizon_months": 3,
        "research_mode": "strict_pit",
        "predicted_probability": 1.2,
        "point_estimate": None,
        "prediction_interval": None,
        "baseline_probability": 0.5,
        "calibration_status": "uncalibrated",
        "model_status": "ok",
        "run_id": "r1",
    }
    with pytest.raises(ValidationError):
        Prediction.model_validate(base)
    ok = Prediction.model_validate({**base, "predicted_probability": 0.6})
    assert ok.predicted_probability == 0.6


def test_target_spec_requires_baselines() -> None:
    """§11.1：任何目标必须预注册对照基线。"""
    base = {
        "target_id": "T1",
        "definition_version": "t1@1",
        "industry_id": "CN_NBS_C31",
        "horizon_months": 3,
        "label_kind": "direction_up",
        "formula": "f",
        "missing_policy": "UNSCORABLE",
        "baselines": [],
        "description": "d",
    }
    with pytest.raises(ValidationError):
        TargetSpec.model_validate(base)
