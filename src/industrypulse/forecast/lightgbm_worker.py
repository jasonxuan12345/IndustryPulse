"""Standalone LightGBM process, isolated from database OpenMP runtimes on Windows."""

from __future__ import annotations

import json
import sys


def main() -> None:
    import lightgbm as lgb
    import numpy as np

    payload = json.load(sys.stdin)
    if payload["operation"] == "fit":
        estimator = lgb.LGBMClassifier(**payload["parameters"])
        estimator.fit(np.asarray(payload["x"], dtype=float), np.asarray(payload["y"], dtype=float))
        json.dump(
            {
                "model": estimator.booster_.model_to_string(),
                "importance": estimator.feature_importances_.tolist(),
            },
            sys.stdout,
        )
    else:
        model = lgb.Booster(model_str=payload["model"])
        prediction = model.predict(np.asarray(payload["x"], dtype=float), num_threads=1)
        json.dump({"probability": np.asarray(prediction).tolist()}, sys.stdout)


if __name__ == "__main__":
    main()
