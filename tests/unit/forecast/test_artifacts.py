from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from industrypulse.contracts import (
    ApprovalState,
    FeatureFrame,
    FeatureRow,
    LabeledFeatureFrame,
    ResearchMode,
)
from industrypulse.contracts.research import ExperimentSpec, SplitKind, SplitSpec
from industrypulse.forecast import BaselineModel


def test_m00_baseline_rejects_artifact_from_another_fit() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    at = datetime(2020, 1, 1, tzinfo=tz)
    cutoff = datetime(2021, 1, 1, tzinfo=tz)
    row = FeatureRow(
        industry_id="CN_NBS_C31",
        decision_as_of=at,
        features={"x": None},
        missing_features=["x"],
        max_ancestor_available_at=at,
    )
    frame = FeatureFrame(
        industry_id=row.industry_id,
        snapshot_id="s",
        feature_spec_version="1",
        schema_hash="s",
        research_mode=ResearchMode.RECONSTRUCTED_PIT,
        built_at=cutoff,
        rows=[row],
    )
    split = SplitSpec(
        split_id="s",
        kind=SplitKind.EXPANDING,
        train_start=at.date(),
        train_end=at.date(),
        test_start=cutoff.date(),
        test_end=cutoff.date(),
        purge_months=3,
        embargo_months=0,
    )
    spec = ExperimentSpec(
        experiment_id="run",
        code_sha="code",
        dependencies_hash="deps",
        dataset_manifest_hash="data",
        industry_map_version="1",
        pit_mode=frame.research_mode,
        train_cutoff=cutoff,
        outer_splits=[split],
        feature_list=["x"],
        model_grid={"M00_LAST": {}},
        random_seed=1,
        target_definition="T1_IVA_DIRECTION@1",
        baselines=["M00_LAST"],
        primary_metric="brier",
        approval_state=ApprovalState.FROZEN,
        created_at=cutoff,
    )
    model = BaselineModel()
    artifact = model.fit(
        LabeledFeatureFrame(features=frame, labels=[1.0], label_available_at=[at]), spec
    )
    future_row = row.model_copy(update={"decision_as_of": datetime(2022, 1, 1, tzinfo=tz)})
    future = frame.model_copy(update={"rows": [future_row]})
    result = model.predict(future, artifact).predictions[0]
    assert result.predicted_probability == result.baseline_probability == 1.0
    assert result.missing_features == ["x"]
    with pytest.raises(ValueError, match="Artifact"):
        model.predict(future, artifact.model_copy(update={"sha256": "different"}))
