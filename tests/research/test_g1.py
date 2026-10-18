from datetime import date, datetime, timedelta
from functools import partial
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from industrypulse.contracts.context import ResearchMode
from industrypulse.contracts.model import (
    FeatureFrame,
    FeatureRow,
    LabeledFeatureFrame,
    TargetFrame,
    TargetRow,
)
from industrypulse.contracts.research import ApprovalState, ExperimentSpec, SplitKind, SplitSpec
from industrypulse.features import (
    FeaturePipeline,
    FeatureValue,
    build_feature_row,
    decision_calendar,
    target_specs,
)
from industrypulse.features.pipeline import add_months
from industrypulse.forecast import BaselineModel, InsufficientEvidence
from industrypulse.research import (
    classification_metrics,
    freeze_manifest,
    regression_metrics,
    run_experiment,
    split_indices,
)

TZ = ZoneInfo("Asia/Shanghai")
execute = partial(run_experiment, model_factory=lambda name, params: BaselineModel(name, **params))


def dt(year: int, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, 17, tzinfo=TZ)


def data() -> tuple[FeatureFrame, TargetFrame, ExperimentSpec]:
    rows, labels = [], []
    for year in range(2010, 2021):
        for month in range(1, 13):
            decision = dt(year, month)
            value = float((year + month) % 2)
            rows.append(
                FeatureRow(
                    industry_id="CN_NBS_C31",
                    decision_as_of=decision,
                    features={"x": value},
                    max_ancestor_available_at=decision,
                )
            )
            period = add_months(decision.date(), 3)
            labels.append(
                TargetRow(
                    industry_id="CN_NBS_C31",
                    decision_as_of=decision,
                    target_period=period.strftime("%Y-%m"),
                    value=value,
                    label_available_at=dt(period.year, period.month) + timedelta(days=5),
                )
            )
    frame = FeatureFrame(
        industry_id="CN_NBS_C31",
        snapshot_id="synthetic",
        feature_spec_version="1",
        schema_hash="synthetic",
        research_mode=ResearchMode.RECONSTRUCTED_PIT,
        built_at=dt(2022),
        rows=rows,
    )
    target = TargetFrame(spec=target_specs("CN_NBS_C31")[0], rows=labels)
    outer = SplitSpec(
        split_id="outer",
        kind=SplitKind.EXPANDING,
        train_start=date(2010, 1, 1),
        train_end=date(2017, 12, 31),
        test_start=date(2018, 1, 1),
        test_end=date(2020, 12, 31),
        purge_months=3,
        embargo_months=1,
    )
    inner = SplitSpec(
        split_id="inner",
        kind=SplitKind.EXPANDING,
        train_start=date(2010, 1, 1),
        train_end=date(2015, 12, 31),
        test_start=date(2016, 1, 1),
        test_end=date(2016, 12, 31),
        purge_months=3,
        embargo_months=1,
    )
    spec = ExperimentSpec(
        experiment_id="synthetic-g1",
        code_sha="test",
        dependencies_hash="lock",
        dataset_manifest_hash="synthetic",
        industry_map_version="1",
        pit_mode=frame.research_mode,
        train_cutoff=dt(2018),
        outer_splits=[outer],
        inner_splits=[inner],
        feature_list=["x"],
        model_grid={
            "M00_LAST": {},
            "M01_SEASONAL": {"window": [24, 60]},
            "M02_AR": {"lag": [1, 2]},
            "M03_LOGIT": {"C": [0.1, 1]},
            "M04_ELASTIC": {"alpha": [0.1], "l1_ratio": [0.5]},
        },
        random_seed=1,
        target_definition="T1_IVA_DIRECTION@1",
        baselines=["M00_LAST", "M01_SEASONAL", "M02_AR"],
        primary_metric="brier",
        approval_state=ApprovalState.FROZEN,
        created_at=dt(2022),
    )
    return frame, target, spec


def test_fold_refits_all_models_and_replays(tmp_path: Path) -> None:
    frame, target, spec = data()
    result = execute(frame, target, spec, tmp_path)
    replay = execute(frame, target, spec, tmp_path)
    assert result == replay
    assert {p.model_id for p in result.predictions} == set(spec.model_grid)
    assert result.comparison["common_oos_count"] == 36
    assert result.comparison["status"] == "NO_EDGE"
    assert set(result.comparison["models"]["M03_LOGIT"]["by_year"]) == {"2018", "2019", "2020"}
    assert all(p.model_status.value == "not_validated" for p in result.predictions)


def test_train_scaler_does_not_see_future_or_change_after_transform() -> None:
    frame, _, _ = data()
    train = frame.model_copy(update={"rows": frame.rows[:20]})
    pipeline = FeaturePipeline(["x"]).fit(train, train.rows[-1].decision_as_of)
    state = pipeline.medians, pipeline.scales
    future_row = frame.rows[-1].model_copy(update={"features": {"x": 1e12}})
    future = frame.model_copy(update={"rows": [future_row]})
    assert pipeline.transform(future)[0][0] > 1e10
    assert (pipeline.medians, pipeline.scales) == state
    with pytest.raises(ValueError, match="future"):
        pipeline.fit(future, train.rows[-1].decision_as_of)


def test_label_clock_and_purge_filter() -> None:
    frame, target, spec = data()
    train, test = split_indices(frame, target, spec.outer_splits[0], dt(2018))
    assert train and test
    assert frame.rows[train[-1]].decision_as_of < dt(2017, 9)
    late = target.rows[0].model_copy(update={"label_available_at": dt(2025)})
    changed = target.model_copy(update={"rows": [late, *target.rows[1:]]})
    filtered, _ = split_indices(frame, changed, spec.outer_splits[0], dt(2018))
    assert 0 not in filtered


def test_model_rejects_unknown_training_labels_and_single_class() -> None:
    frame, _, spec = data()
    training = frame.model_copy(update={"rows": frame.rows[:12]})
    labels = LabeledFeatureFrame(
        features=training, labels=[0.0] * 12, label_available_at=[dt(2010)] * 12
    )
    with pytest.raises(InsufficientEvidence):
        BaselineModel("M03_LOGIT").fit(labels, spec)
    future = labels.model_copy(update={"label_available_at": [dt(2025)] * 12})
    with pytest.raises(ValueError, match="released"):
        BaselineModel().fit(future, spec)


def test_manifest_is_immutable(tmp_path: Path) -> None:
    _, _, spec = data()
    path = freeze_manifest(spec, tmp_path)
    assert freeze_manifest(spec, tmp_path) == path
    with pytest.raises(ValueError, match="new experiment_id"):
        freeze_manifest(spec.model_copy(update={"random_seed": 2}), tmp_path)


def test_future_transitive_ancestor_rejected() -> None:
    nodes = {"raw": FeatureValue(1.0, dt(2025)), "derived": FeatureValue(2.0, dt(2020), ("raw",))}
    with pytest.raises(ValueError, match="ancestor"):
        build_feature_row("CN_NBS_C31", dt(2021), nodes, ["derived"])


def test_calendar_uses_exchange_holidays() -> None:
    result = decision_calendar([date(2026, 1, 28), date(2026, 1, 29), date(2026, 2, 27)])
    assert result == (dt(2026, 1, 29), dt(2026, 2, 27))


def test_metric_single_class_and_training_mase() -> None:
    assert classification_metrics([1.0, 1.0], [0.2, 0.8])["roc_auc"] is None
    assert regression_metrics([2.0], [1.0], [0.0, 1.0, 2.0], season=1)["mase"] == 1.0


def test_heldout_values_do_not_change_selected_parameters_or_artifacts(tmp_path: Path) -> None:
    frame, target, spec = data()
    first = execute(frame, target, spec, tmp_path / "original")
    poisoned_rows = [
        r.model_copy(update={"features": {"x": 1e9}}) if r.decision_as_of.year >= 2018 else r
        for r in frame.rows
    ]
    poisoned = frame.model_copy(update={"rows": poisoned_rows})
    second = execute(poisoned, target, spec, tmp_path / "poisoned")
    assert first.fold_logs == second.fold_logs


def test_inner_search_cannot_reach_outer_test(tmp_path: Path) -> None:
    frame, target, spec = data()
    leaked = spec.inner_splits[0].model_copy(update={"test_end": date(2018, 1, 1)})
    spec = spec.model_copy(update={"inner_splits": [leaked]})
    with pytest.raises(ValueError, match="outer training"):
        execute(frame, target, spec, tmp_path)


def test_embargo_removes_additional_overlapping_horizon_samples() -> None:
    frame, target, spec = data()
    split = spec.outer_splits[0]
    no_embargo = split.model_copy(update={"embargo_months": 0})
    extra_embargo = split.model_copy(update={"embargo_months": 2})
    baseline, _ = split_indices(frame, target, no_embargo, dt(2018))
    restricted, _ = split_indices(frame, target, extra_embargo, dt(2018))
    assert len(baseline) - len(restricted) == 2
    assert set(restricted) < set(baseline)
