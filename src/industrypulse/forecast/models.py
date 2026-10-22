"""G1 M00..M04 deterministic, uncalibrated research models."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from industrypulse.contracts.model import (
    CalibrationStatus,
    FeatureFrame,
    LabeledFeatureFrame,
    ModelArtifact,
    ModelStatus,
    Prediction,
    PredictionFrame,
)
from industrypulse.contracts.research import ExperimentSpec
from industrypulse.features import FeaturePipeline


class InsufficientEvidence(ValueError):
    """A model's prespecified minimum training evidence was not met."""


class BaselineModel:
    def __init__(self, model_id: str = "M00_LAST", **parameters: Any) -> None:
        if model_id not in {"M00_LAST", "M01_SEASONAL", "M02_AR", "M03_LOGIT", "M04_ELASTIC"}:
            raise ValueError("Unknown G1 model")
        self.model_id = model_id
        self.parameters = parameters
        self.estimator: Any = None
        self.pipeline: FeaturePipeline | None = None
        self.config: ExperimentSpec | None = None
        self.history: list[float] = []
        self.seasonal: dict[int, float] = {}
        self.coefficients: Any = None
        self.artifact: ModelArtifact | None = None

    def fit(self, train: LabeledFeatureFrame, config: ExperimentSpec) -> ModelArtifact:
        for row, y, available in zip(
            train.features.rows, train.labels, train.label_available_at, strict=True
        ):
            if row.decision_as_of > config.train_cutoff:
                raise ValueError("Future training feature")
            if y is None or available is None or available >= config.train_cutoff:
                raise ValueError("Unknown or not yet released training label")
            if y not in (0, 1):
                raise ValueError("G1 models require binary direction labels")
        if train.features.research_mode != config.pit_mode:
            raise ValueError("Research mode mismatch")
        self.history = [float(y) for y in train.labels if y is not None]
        if not self.history:
            raise InsufficientEvidence("No released training labels")
        self.config = config
        self.pipeline = FeaturePipeline(config.feature_list).fit(
            train.features, config.train_cutoff
        )
        x = self.pipeline.transform(train.features)
        if self.model_id in {"M03_LOGIT", "M04_ELASTIC"}:
            if min(self.history.count(0.0), self.history.count(1.0)) < 5:
                raise InsufficientEvidence("Require at least five released events per class")
            from sklearn import __version__ as sklearn_version  # type: ignore[import-untyped]
            from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]

            args: dict[str, Any] = dict(
                random_state=config.random_seed,
                max_iter=2000,
                C=float(self.parameters.get("C", 1.0)),
                class_weight=self.parameters.get("class_weight"),
            )
            if self.model_id == "M04_ELASTIC":
                args.update(
                    solver="saga",
                    C=1.0 / float(self.parameters.get("alpha", 0.01)),
                    l1_ratio=float(self.parameters.get("l1_ratio", 0.5)),
                )
                if tuple(int(v) for v in sklearn_version.split(".")[:2]) < (1, 8):
                    args["penalty"] = "elasticnet"
            self.estimator = LogisticRegression(**args).fit(x, self.history)
        elif self.model_id == "M01_SEASONAL":
            if len(self.history) < 48:
                raise InsufficientEvidence("Seasonal model requires 48 valid months")
            window = int(self.parameters.get("window", 60))
            for month in range(1, 13):
                values = [
                    y
                    for r, y in zip(
                        train.features.rows[-window:], self.history[-window:], strict=True
                    )
                    if r.decision_as_of.month == month
                ]
                if values:
                    self.seasonal[month] = sum(values) / len(values)
        elif self.model_id == "M02_AR":
            if len(self.history) < 60:
                raise InsufficientEvidence("AR requires 60 valid months")
            import numpy as np

            lag = int(self.parameters.get("lag", 1))
            if lag not in (1, 2, 3, 6):
                raise ValueError("AR lag must be preregistered")
            matrix = [[1.0, *self.history[i - lag : i]] for i in range(lag, len(self.history))]
            self.coefficients = np.linalg.lstsq(matrix, self.history[lag:], rcond=None)[0]
        payload: dict[str, Any] = dict(
            model=self.model_id,
            params=self.parameters,
            config=config.model_dump(mode="json"),
            train=train.model_dump(mode="json"),
        )
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        self.artifact = ModelArtifact(
            model_id=self.model_id,
            model_version="g1-v1",
            artifact_uri=f"memory://{digest}",
            sha256=digest,
            trained_at=config.train_cutoff,
            feature_spec_version=train.features.feature_spec_version,
            target_id=config.target_definition.split("@")[0],
            training_rows=len(self.history),
        )
        return self.artifact

    def predict(self, features: FeatureFrame, artifact: ModelArtifact) -> PredictionFrame:
        if self.artifact != artifact or self.config is None or self.pipeline is None:
            raise ValueError("Artifact does not belong to fitted model")
        if features.research_mode != self.config.pit_mode:
            raise ValueError("Research mode mismatch")
        if features.feature_spec_version != artifact.feature_spec_version:
            raise ValueError("Feature version mismatch")
        if any(r.decision_as_of <= self.config.train_cutoff for r in features.rows):
            raise ValueError("Prediction must follow training cutoff")
        base = sum(self.history) / len(self.history)
        probabilities: list[float] = []
        x = self.pipeline.transform(features)
        estimated = self.estimator.predict_proba(x) if self.estimator is not None else None
        for i, row in enumerate(features.rows):
            if estimated is not None:
                probability = float(estimated[i][1])
            elif self.model_id == "M01_SEASONAL":
                probability = self.seasonal.get(row.decision_as_of.month, base)
            elif self.model_id == "M02_AR":
                lag = int(self.parameters.get("lag", 1))
                # Recursive forecasts never append held-out labels.
                history = self.history + probabilities
                probability = float(self.coefficients @ [1.0, *history[-lag:]])
            else:
                probability = self.history[-1]
            probabilities.append(max(0.0, min(1.0, probability)))
        run_id = self.config.experiment_id
        predictions = [
            Prediction(
                prediction_id=hashlib.sha256(
                    f"{artifact.sha256}:{r.decision_as_of}".encode()
                ).hexdigest(),
                model_id=self.model_id,
                model_version=artifact.model_version,
                industry_id=r.industry_id,
                target_id=artifact.target_id,
                as_of=r.decision_as_of,
                horizon_months=3,
                research_mode=features.research_mode,
                predicted_probability=p,
                point_estimate=None,
                prediction_interval=None,
                baseline_probability=base,
                calibration_status=CalibrationStatus.UNCALIBRATED,
                missing_features=r.missing_features,
                model_status=ModelStatus.NOT_VALIDATED,
                run_id=run_id,
            )
            for r, p in zip(features.rows, probabilities, strict=True)
        ]
        return PredictionFrame(run_id=run_id, predictions=predictions)
