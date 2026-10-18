"""研究实验室：冻结实验、走步验证、指标与报告（lane R；必须可 CLI 独立运行）。"""

from .validation import (
    ResearchReport,
    classification_metrics,
    compare_models,
    freeze_manifest,
    regression_metrics,
    run_experiment,
    split_indices,
)

__all__ = [
    "ResearchReport",
    "classification_metrics",
    "compare_models",
    "freeze_manifest",
    "regression_metrics",
    "run_experiment",
    "split_indices",
]
