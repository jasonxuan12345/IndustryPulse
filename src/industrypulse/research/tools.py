"""Dependence-aware intervals, sealed holdouts and independent calibration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from industrypulse.contracts.model import Prediction, TargetFrame


def block_bootstrap(
    values: Sequence[float], block: int = 3, repetitions: int = 1000, seed: int = 20261022
) -> dict[str, Any]:
    if len(values) < block * 2 or block < 1:
        return {"status": "INSUFFICIENT_EVIDENCE", "n": len(values), "interval": None}
    data = np.asarray(values, dtype=float)
    generator = np.random.default_rng(seed)
    means = []
    for _ in range(repetitions):
        starts = generator.integers(0, len(data), size=int(np.ceil(len(data) / block)))
        sample = np.concatenate([data[(start + np.arange(block)) % len(data)] for start in starts])[
            : len(data)
        ]
        means.append(float(sample.mean()))
    return {
        "status": "EXPLORATORY_INTERVAL",
        "n": len(data),
        "block": block,
        "repetitions": repetitions,
        "seed": seed,
        "mean": float(data.mean()),
        "interval": [float(value) for value in np.quantile(means, [0.025, 0.975])],
    }


def report_uncertainty(predictions: Sequence[Prediction], targets: TargetFrame) -> dict[str, Any]:
    labels = {row.decision_as_of: row.value for row in targets.rows}
    grouped: dict[str, list[Prediction]] = {}
    for prediction in predictions:
        grouped.setdefault(prediction.model_id, []).append(prediction)
    return {
        model: block_bootstrap(
            [
                (float(item.predicted_probability) - float(labels[item.as_of] or 0)) ** 2
                for item in sorted(items, key=lambda item: item.as_of)
                if item.predicted_probability is not None and labels.get(item.as_of) is not None
            ]
        )
        for model, items in grouped.items()
    }


def seal_holdout(path: Path, specification: dict[str, Any]) -> str:
    content = json.dumps(specification, sort_keys=True, ensure_ascii=False).encode()
    digest = hashlib.sha256(content).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError("最终 holdout 已封存，修改范围必须创建新实验")
    else:
        with path.open("xb") as stream:
            stream.write(content)
    return digest


def calibrate(
    probabilities: Sequence[float],
    labels: Sequence[int],
    *,
    calibration_end: str,
    prediction_start: str,
) -> Any:
    from sklearn.isotonic import IsotonicRegression  # type: ignore[import-untyped]

    if calibration_end >= prediction_start:
        raise ValueError("校准窗必须早于预测窗")
    if len(labels) < 30 or len(set(labels)) < 2 or len(labels) != len(probabilities):
        raise ValueError("独立校准样本不足，保留 UNCALIBRATED")
    return IsotonicRegression(out_of_bounds="clip").fit(probabilities, labels)


def lead_lag(
    values: Sequence[float], target: Sequence[float], max_lag: int = 6
) -> list[dict[str, Any]]:
    if len(values) != len(target):
        raise ValueError("序列必须对齐；调用方只传入训练窗")
    result = []
    for lag in range(max_lag + 1):
        left = np.asarray(values[: len(values) - lag] if lag else values, dtype=float)
        right = np.asarray(target[lag:], dtype=float)
        correlation = (
            float(np.corrcoef(left, right)[0, 1])
            if len(left) >= 12 and left.std() and right.std()
            else None
        )
        result.append(
            {
                "lag": lag,
                "correlation": correlation,
                "n": len(left),
                "warning": "训练窗探索；多重检验未构成预测证据",
            }
        )
    return result
