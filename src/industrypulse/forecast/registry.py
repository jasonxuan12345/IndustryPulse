"""Eight local model families with train-only preprocessing and explicit failures."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

import numpy as np

from industrypulse.contracts.model import LabeledFeatureFrame, ModelArtifact
from industrypulse.contracts.research import ExperimentSpec
from industrypulse.forecast.models import BaselineModel, InsufficientEvidence


class FilterClassifier:
    def __init__(self, kind: str, x: Any, labels: Any) -> None:
        self.kind = kind
        self.history = np.asarray(x, dtype=float)
        self.labels = np.asarray(labels, dtype=float)
        if kind == "M05_FACTOR":
            from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
            from statsmodels.tsa.statespace.dynamic_factor_mq import DynamicFactorMQ  # type: ignore[import-untyped]

            self.columns = np.flatnonzero(np.std(self.history, axis=0) > 1e-8)
            if len(self.columns) < 2:
                raise InsufficientEvidence("动态因子需要至少两条非恒定输入序列")
            data = self.history[:, self.columns]
            self.factor = DynamicFactorMQ(data, factors=1, factor_orders=1, standardize=False).fit(
                maxiter=150, disp=False
            )
            factors = np.asarray(self.factor.filtered_state[:1]).T
            self.link = LogisticRegression().fit(factors, self.labels)
        else:
            from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression  # type: ignore[import-untyped]

            self.columns = np.array([0])
            self.factor = MarkovRegression(
                self.history[:, 0], k_regimes=2, trend="c", switching_variance=True
            ).fit(disp=False, maxiter=150)
            weights = np.asarray(self.factor.filtered_marginal_probabilities)
            self.regime_rates = (weights.T @ self.labels + 1) / (weights.sum(axis=0) + 2)
            self.state_order = np.argsort(
                [np.average(self.history[:, 0], weights=weights[:, index]) for index in range(2)]
            )

    def predict_proba(self, values: Any) -> Any:
        incoming = np.asarray(values, dtype=float)
        combined = np.concatenate([self.history, incoming])
        if self.kind == "M05_FACTOR":
            from statsmodels.tsa.statespace.dynamic_factor_mq import DynamicFactorMQ

            filtered = DynamicFactorMQ(
                combined[:, self.columns], factors=1, factor_orders=1, standardize=False
            ).filter(self.factor.params)
            probability = self.link.predict_proba(
                np.asarray(filtered.filtered_state[:1]).T[-len(incoming) :]
            )[:, 1]
        else:
            from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression

            filtered = MarkovRegression(
                combined[:, 0], k_regimes=2, trend="c", switching_variance=True
            ).filter(self.factor.params)
            weights = np.asarray(filtered.filtered_marginal_probabilities)[-len(incoming) :]
            probability = weights @ self.regime_rates
        return np.column_stack([1 - probability, probability])


class IsolatedLightGBM:
    def __init__(self, parameters: dict[str, Any], x: Any, y: Any) -> None:
        result = self.execute({"operation": "fit", "parameters": parameters, "x": x, "y": y})
        self.model = result["model"]
        self.feature_importances_ = result["importance"]

    @staticmethod
    def execute(payload: dict[str, Any]) -> dict[str, Any]:
        process = subprocess.run(
            [sys.executable, "-m", "industrypulse.forecast.lightgbm_worker"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            env={**os.environ, "OMP_NUM_THREADS": "1"},
            check=False,
        )
        if process.returncode:
            raise RuntimeError("LightGBM 独立进程失败：" + process.stderr[-500:])
        return dict(json.loads(process.stdout))

    def predict_proba(self, values: Any) -> Any:
        result = self.execute({"operation": "predict", "model": self.model, "x": values})
        probability = np.asarray(result["probability"])
        return np.column_stack([1 - probability, probability])


class AdvancedModel(BaselineModel):
    def __init__(self, model_id: str, **parameters: Any) -> None:
        super().__init__()
        self.model_id = model_id
        self.parameters = parameters

    def fit(self, train: LabeledFeatureFrame, config: ExperimentSpec) -> ModelArtifact:
        artifact = super().fit(train, config)
        if len(self.history) < 60 or min(self.history.count(0.0), self.history.count(1.0)) < 10:
            raise InsufficientEvidence(
                "高级模型需要至少 60 个训练样本且每类至少 10 个；当前样本不足"
            )
        assert self.pipeline is not None
        x = self.pipeline.transform(train.features)
        if self.model_id == "M06_LIGHTGBM":
            self.estimator = IsolatedLightGBM(
                dict(
                    n_estimators=int(self.parameters.get("n_estimators", 80)),
                    num_leaves=int(self.parameters.get("num_leaves", 7)),
                    max_depth=3,
                    min_child_samples=10,
                    verbosity=-1,
                    deterministic=True,
                    force_col_wise=True,
                    n_jobs=1,
                    random_state=config.random_seed,
                ),
                x,
                self.history,
            )
        else:
            self.estimator = FilterClassifier(self.model_id, x, self.history)
        return artifact


def create_model(name: str, parameters: dict[str, Any]) -> BaselineModel:
    if name in {"M05_FACTOR", "M06_LIGHTGBM", "M07_MARKOV"}:
        return AdvancedModel(name, **parameters)
    return BaselineModel(name, **parameters)
