from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from industrypulse.contracts import (
    TZ_SHANGHAI,
    FeatureFrame,
    FeatureRow,
    LabeledFeatureFrame,
    TargetFrame,
    TargetRow,
)
from industrypulse.features import target_specs
from industrypulse.forecast.registry import create_model
from industrypulse.research.tools import block_bootstrap, seal_holdout
from industrypulse.services.research_run import make_experiment_spec


@pytest.mark.parametrize("model_id", ["M05_FACTOR", "M06_LIGHTGBM", "M07_MARKOV"])
def test_advanced_models_filter_without_future_features(model_id):
    generator = np.random.default_rng(23)
    rows, labels = [], []
    for index in range(96):
        at = datetime(2010 + index // 12, index % 12 + 1, 28, 17, tzinfo=TZ_SHANGHAI)
        value = float(np.sin(index / 5) + generator.normal(0, 0.25))
        rows.append(
            FeatureRow(
                industry_id="CN_NBS_C31",
                decision_as_of=at,
                features={
                    "first": value,
                    "second": float(np.cos(index / 7) + generator.normal(0, 0.2)),
                },
                max_ancestor_available_at=at,
            )
        )
        labels.append(float(value > 0))
    frame = FeatureFrame(
        industry_id="CN_NBS_C31",
        snapshot_id="test",
        feature_spec_version="v1",
        schema_hash="test",
        research_mode="reconstructed_pit",
        built_at=rows[-1].decision_as_of,
        rows=rows,
    )
    targets = TargetFrame(
        spec=target_specs("CN_NBS_C31")[0],
        rows=[
            TargetRow(
                industry_id="CN_NBS_C31",
                decision_as_of=row.decision_as_of,
                target_period=row.decision_as_of.strftime("%Y-%m"),
                value=value,
                label_available_at=row.decision_as_of,
            )
            for row, value in zip(rows, labels, strict=True)
        ],
    )
    cutoff = datetime(2018, 1, 1, tzinfo=TZ_SHANGHAI)
    spec = make_experiment_spec(
        experiment_id="test",
        features=frame,
        targets=targets,
        train_end=cutoff.date(),
        outer_start=datetime(2018, 1, 2).date(),
        outer_end=datetime(2020, 1, 1).date(),
        feature_list=["first", "second"],
    ).model_copy(update={"train_cutoff": cutoff})
    model = create_model(model_id, {})
    artifact = model.fit(
        LabeledFeatureFrame(
            features=frame, labels=labels, label_available_at=[row.decision_as_of for row in rows]
        ),
        spec,
    )
    future = frame.model_copy(
        update={
            "rows": [
                rows[-2].model_copy(
                    update={"decision_as_of": datetime(2018, 2, 28, tzinfo=TZ_SHANGHAI)}
                ),
                rows[-1].model_copy(
                    update={"decision_as_of": datetime(2018, 3, 28, tzinfo=TZ_SHANGHAI)}
                ),
            ]
        }
    )
    first = model.predict(future, artifact)
    changed = future.model_copy(
        update={
            "rows": [
                future.rows[0],
                future.rows[1].model_copy(
                    update={"features": {"first": 1000.0, "second": -1000.0}}
                ),
            ]
        }
    )
    second = model.predict(changed, artifact)
    assert first.predictions[0].predicted_probability == pytest.approx(
        second.predictions[0].predicted_probability
    )
    assert all(0 <= row.predicted_probability <= 1 for row in first.predictions)


def test_uncertainty_seed_and_holdout_immutability(tmp_path):
    assert block_bootstrap([0.0, 1.0] * 10) == block_bootstrap([0.0, 1.0] * 10)
    assert block_bootstrap([1.0])["interval"] is None
    path = tmp_path / "holdout.json"
    digest = seal_holdout(path, {"start": "2030-01-01"})
    assert seal_holdout(path, {"start": "2030-01-01"}) == digest
    with pytest.raises(ValueError):
        seal_holdout(path, {"start": "2020-01-01"})
