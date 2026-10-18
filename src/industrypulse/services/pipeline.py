"""Composition root: connect lanes through their public APIs and frozen contracts."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from industrypulse.contracts import (
    AnalysisContext,
    ExperimentSpec,
    FeatureFrame,
    IndicatorSpec,
    TargetFrame,
)
from industrypulse.contracts.model import ObservationRepository
from industrypulse.economy import EconomyEngine, EconomyReport, RobustScaler
from industrypulse.forecast import BaselineModel
from industrypulse.research import ResearchReport, run_experiment


class PipelineService:
    def __init__(self, repository: ObservationRepository) -> None:
        self.economy = EconomyEngine(repository)

    def observe(
        self,
        specs: Sequence[IndicatorSpec],
        context: AnalysisContext,
        scalers: dict[str, RobustScaler],
    ) -> EconomyReport:
        return self.economy.calculate(specs, context, scalers)

    def experiment(
        self, features: FeatureFrame, targets: TargetFrame, spec: ExperimentSpec, output: Path
    ) -> ResearchReport:
        return run_experiment(
            features,
            targets,
            spec,
            output,
            model_factory=lambda name, params: BaselineModel(name, **params),
        )
