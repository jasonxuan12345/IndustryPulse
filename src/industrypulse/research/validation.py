"""Chronological validation with release-clock filtering and immutable manifests."""

from __future__ import annotations

import calendar
import hashlib
import itertools
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from industrypulse.contracts.model import (
    FeatureFrame,
    ForecastModel,
    LabeledFeatureFrame,
    Prediction,
    TargetFrame,
)
from industrypulse.contracts.research import ApprovalState, ExperimentSpec, SplitSpec

ModelFactory = Callable[[str, dict[str, Any]], ForecastModel]


class InsufficientEvidence(ValueError):
    """The released/purged fold cannot be evaluated."""


def add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def freeze_manifest(spec: ExperimentSpec, directory: Path) -> Path:
    if spec.approval_state not in {ApprovalState.FROZEN, ApprovalState.APPROVED}:
        raise ValueError("Freeze experiment before execution")
    if any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for c in spec.experiment_id
    ):
        raise ValueError("Experiment identifier must be a safe filename component")
    data = spec.model_dump_json(indent=2).encode()
    digest = hashlib.sha256(data).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{spec.experiment_id}-{digest}.json"
    # Exclusive creation plus experiment-id check prevents overwriting an existing run.
    previous = [
        p
        for p in directory.glob(f"{spec.experiment_id}-*.json")
        if p.name != f"{spec.experiment_id}-report.json"
    ]
    if previous:
        if len(previous) == 1 and previous[0] == path and path.read_bytes() == data:
            return path
        raise ValueError("Changed manifest requires a new experiment_id")
    with path.open("xb") as handle:
        handle.write(data)
    path.chmod(0o444)
    return path


def split_indices(
    features: FeatureFrame, targets: TargetFrame, split: SplitSpec, cutoff: datetime
) -> tuple[list[int], list[int]]:
    if len(features.rows) != len(targets.rows):
        raise ValueError("Feature/target alignment length mismatch")
    train, test = [], []
    for index, (row, target) in enumerate(zip(features.rows, targets.rows, strict=True)):
        if row.decision_as_of != target.decision_as_of or row.industry_id != target.industry_id:
            raise ValueError("Feature/target alignment mismatch")
        day = row.decision_as_of.date()
        if split.test_start <= day <= split.test_end:
            test.append(index)
        if not (split.train_start <= day <= split.train_end):
            continue
        if row.decision_as_of >= cutoff:
            continue
        if target.value is None or target.label_available_at is None:
            continue
        if target.label_available_at >= cutoff:
            continue
        # Actual economic target endpoint plus additional prespecified embargo.
        target_month = datetime.strptime(target.target_period, "%Y-%m").date()
        horizon_end = max(add_months(day, split.purge_months), add_months(target_month, 1))
        if add_months(horizon_end, split.embargo_months) >= split.test_start:
            continue
        train.append(index)
    return train, test


def classification_metrics(y: Sequence[float], p: Sequence[float]) -> dict[str, Any]:
    if len(y) != len(p) or not y:
        raise ValueError("Require nonempty aligned OOS samples")
    if any(v not in (0, 1) for v in y) or any(not 0 <= v <= 1 for v in p):
        raise ValueError("Binary labels and finite probabilities required")
    from sklearn.metrics import (  # type: ignore[import-untyped]
        average_precision_score,
        roc_auc_score,
    )

    n = len(y)
    tp = sum(a == 1 and b >= 0.5 for a, b in zip(y, p, strict=True))
    fp = sum(a == 0 and b >= 0.5 for a, b in zip(y, p, strict=True))
    fn = sum(a == 1 and b < 0.5 for a, b in zip(y, p, strict=True))
    tn = n - tp - fp - fn
    class_recalls = ([tp / (tp + fn)] if tp + fn else []) + ([tn / (tn + fp)] if tn + fp else [])
    reliability = []
    for index in range(10):
        pairs = [(a, b) for a, b in zip(y, p, strict=True) if min(int(b * 10), 9) == index]
        if pairs:
            reliability.append(
                dict(
                    count=len(pairs),
                    predicted=sum(b for _, b in pairs) / len(pairs),
                    observed=sum(a for a, _ in pairs) / len(pairs),
                )
            )
    return dict(
        n=n,
        brier=sum((a - b) ** 2 for a, b in zip(y, p, strict=True)) / n,
        log_loss=-sum(
            a * math.log(max(1e-15, b)) + (1 - a) * math.log(max(1e-15, 1 - b))
            for a, b in zip(y, p, strict=True)
        )
        / n,
        roc_auc=float(roc_auc_score(y, p)) if len(set(y)) == 2 else None,
        pr_auc=float(average_precision_score(y, p)) if len(set(y)) == 2 else None,
        balanced_accuracy=sum(class_recalls) / len(class_recalls),
        precision=tp / (tp + fp) if tp + fp else None,
        recall=tp / (tp + fn) if tp + fn else None,
        false_positive_rate=fp / (fp + tn) if fp + tn else None,
        confusion_matrix=[[tn, fp], [fn, tp]],
        reliability=reliability,
    )


def regression_metrics(
    y: Sequence[float], predictions: Sequence[float], training: Sequence[float], season: int = 12
) -> dict[str, float | None]:
    if not y or len(y) != len(predictions) or season < 1:
        raise ValueError("Require aligned samples and positive seasonal lag")
    errors = [a - b for a, b in zip(y, predictions, strict=True)]
    if any(not math.isfinite(v) for v in [*y, *predictions, *training]):
        raise ValueError("Nonfinite value")
    mae = sum(abs(v) for v in errors) / len(errors)
    differences = [abs(training[i] - training[i - season]) for i in range(season, len(training))]
    scale = sum(differences) / len(differences) if differences else 0.0
    return dict(
        mae=mae,
        rmse=math.sqrt(sum(v * v for v in errors) / len(errors)),
        mase=mae / scale if scale else None,
    )


@dataclass(frozen=True)
class ResearchReport:
    predictions: tuple[Prediction, ...]
    failures: tuple[dict[str, str], ...]
    fold_logs: tuple[dict[str, Any], ...]
    comparison: dict[str, Any]


def _subset(frame: FeatureFrame, indices: list[int]) -> FeatureFrame:
    return frame.model_copy(update={"rows": [frame.rows[i] for i in indices]})


def _fit_predict(
    model_id: str,
    params: dict[str, Any],
    features: FeatureFrame,
    targets: TargetFrame,
    split: SplitSpec,
    spec: ExperimentSpec,
    cutoff: datetime,
    model_factory: ModelFactory,
) -> tuple[list[Prediction], dict[str, Any]]:
    train, test = split_indices(features, targets, split, cutoff)
    if not train or not test:
        raise InsufficientEvidence("Empty released/purged train or OOS window")
    config = spec.model_copy(update={"train_cutoff": cutoff})
    labeled = LabeledFeatureFrame(
        features=_subset(features, train),
        labels=[targets.rows[i].value for i in train],
        label_available_at=[targets.rows[i].label_available_at for i in train],
    )
    model = model_factory(model_id, params)
    artifact = model.fit(labeled, config)
    output = model.predict(_subset(features, test), artifact)
    return output.predictions, dict(
        split_id=split.split_id,
        model_id=model_id,
        training_rows=len(train),
        oos_rows=len(test),
        artifact_hash=artifact.sha256,
        parameters=params,
        train_cutoff=cutoff.isoformat(),
    )


def run_experiment(
    features: FeatureFrame,
    targets: TargetFrame,
    spec: ExperimentSpec,
    directory: Path,
    *,
    model_factory: ModelFactory,
) -> ResearchReport:
    freeze_manifest(spec, directory)
    if features.research_mode != spec.pit_mode:
        raise ValueError("Research mode mismatch")
    if spec.target_definition != f"{targets.spec.target_id}@{targets.spec.definition_version}":
        raise ValueError("Target definition mismatch")
    predictions, failures, logs = [], [], []
    for outer in spec.outer_splits:
        cutoff = min(
            spec.train_cutoff,
            datetime.combine(outer.train_end, time(23, 59, 59), tzinfo=spec.train_cutoff.tzinfo),
        )
        for model_id, grid in spec.model_grid.items():
            names = sorted(grid)
            candidates = [
                dict(zip(names, values, strict=True))
                for values in itertools.product(*(grid[n] for n in names))
            ]
            if not candidates:
                candidates = [{}]
            scores = []
            for params in candidates:
                inner_y, inner_p = [], []
                for inner in spec.inner_splits:
                    if not (
                        outer.train_start <= inner.train_start and inner.test_end < cutoff.date()
                    ):
                        raise ValueError("Inner search extends beyond outer training window")
                    inner_cutoff = datetime.combine(
                        inner.train_end, time(23, 59, 59), tzinfo=cutoff.tzinfo
                    )
                    try:
                        output, _ = _fit_predict(
                            model_id,
                            params,
                            features,
                            targets,
                            inner,
                            spec,
                            inner_cutoff,
                            model_factory,
                        )
                    except ValueError:
                        continue
                    truth = {r.decision_as_of: r for r in targets.rows}
                    for prediction in output:
                        label = truth[prediction.as_of]
                        if (
                            label.value is not None
                            and label.label_available_at is not None
                            and label.label_available_at < cutoff
                        ):
                            inner_y.append(label.value)
                            inner_p.append(prediction.predicted_probability or 0.0)
                if inner_y:
                    scores.append((classification_metrics(inner_y, inner_p)["brier"], params))
            if len(candidates) > 1 and not scores:
                failures.append(
                    dict(
                        split_id=outer.split_id,
                        model_id=model_id,
                        reason="INSUFFICIENT_EVIDENCE: no released inner validation",
                    )
                )
                continue
            chosen = min(scores, key=lambda item: item[0])[1] if scores else candidates[0]
            try:
                output, log = _fit_predict(
                    model_id, chosen, features, targets, outer, spec, cutoff, model_factory
                )
                predictions.extend(output)
                logs.append(log)
            except ValueError as exc:
                failures.append(dict(split_id=outer.split_id, model_id=model_id, reason=str(exc)))
    comparison = compare_models(
        predictions, targets, spec.baselines, evaluation_as_of=spec.created_at
    )
    unscorable = [r for r in targets.rows if r.value is None]
    comparison["target_coverage"] = dict(
        total_rows=len(targets.rows),
        unscorable_count=len(unscorable),
        unscorable_ratio=len(unscorable) / len(targets.rows),
        reasons={
            reason: sum(r.unscorable_reason == reason for r in unscorable)
            for reason in sorted({r.unscorable_reason or "UNKNOWN" for r in unscorable})
        },
    )
    report = ResearchReport(tuple(predictions), tuple(failures), tuple(logs), comparison)
    payload = json.dumps(
        dict(
            predictions=[p.model_dump(mode="json") for p in predictions],
            failures=failures,
            logs=logs,
            comparison=comparison,
        ),
        sort_keys=True,
        indent=2,
    ).encode()
    path = directory / f"{spec.experiment_id}-report.json"
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError("Frozen report changed; create a new run")
    else:
        with path.open("xb") as handle:
            handle.write(payload)
        path.chmod(0o444)
    return report


def compare_models(
    predictions: Sequence[Prediction],
    targets: TargetFrame,
    baselines: Sequence[str],
    evaluation_as_of: datetime,
    threshold: float = 0.03,
) -> dict[str, Any]:
    labels = {
        (r.industry_id, r.decision_as_of): r.value
        for r in targets.rows
        if r.value is not None
        and r.label_available_at is not None
        and r.label_available_at <= evaluation_as_of
    }
    models: dict[str, dict[tuple[str, datetime], Prediction]] = {}
    for prediction in predictions:
        key = prediction.industry_id, prediction.as_of
        if key in models.setdefault(prediction.model_id, {}):
            raise ValueError("Duplicate OOS prediction")
        if key in labels and prediction.predicted_probability is not None:
            models[prediction.model_id][key] = prediction
    common = set(labels)
    for rows in models.values():
        common.intersection_update(rows)
    if not models or not common:
        return dict(status="INSUFFICIENT_EVIDENCE", common_oos_count=0, models={})
    ordered = sorted(common)
    results: dict[str, Any] = {}
    for model, rows in models.items():

        def metric(
            keys: list[tuple[str, datetime]], current: dict[tuple[str, datetime], Prediction] = rows
        ) -> dict[str, Any]:
            return classification_metrics(
                [float(labels[k]) for k in keys],
                [float(current[k].predicted_probability or 0.0) for k in keys],
            )

        results[model] = dict(overall=metric(ordered), by_year={}, by_industry={})
        for year in sorted({k[1].year for k in ordered}):
            results[model]["by_year"][str(year)] = metric([k for k in ordered if k[1].year == year])
        for industry in sorted({k[0] for k in ordered}):
            results[model]["by_industry"][industry] = metric(
                [k for k in ordered if k[0] == industry]
            )
    simple = [b for b in baselines if b in results]
    if not simple or any(b not in results for b in baselines):
        return dict(
            status="INSUFFICIENT_EVIDENCE",
            common_oos_count=len(common),
            models=results,
            reason="Missing mandatory baseline",
        )
    strongest = min(simple, key=lambda b: results[b]["overall"]["brier"])
    base = results[strongest]["overall"]["brier"]
    for result in results.values():
        improvement = (base - result["overall"]["brier"]) / base if base else 0.0
        consistent = sum(
            v["brier"] < results[strongest]["by_year"][year]["brier"]
            for year, v in result["by_year"].items()
        )
        # G1 has no independent calibration or dependence-aware confidence intervals.
        # Meeting the screening rule yields a research candidate, never validated edge.
        result.update(
            relative_brier_improvement=improvement,
            screening_candidate=improvement >= threshold and consistent >= 2,
            status="NO_EDGE",
            calibration_status="uncalibrated",
        )
    return dict(
        status="NO_EDGE",
        common_oos_count=len(common),
        strongest_baseline=strongest,
        models=results,
        uncertainty="Not estimated; overlapping monthly observations",
        synthetic_validation_only=True,
    )
