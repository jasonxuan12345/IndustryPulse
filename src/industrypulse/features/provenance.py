"""Transitive feature ancestry is checked before a snapshot enters preprocessing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from industrypulse.contracts.model import FeatureRow


@dataclass(frozen=True)
class FeatureValue:
    value: float | None
    available_at: datetime
    ancestors: tuple[str, ...] = ()


def build_feature_row(
    industry_id: str, decision: datetime, nodes: Mapping[str, FeatureValue], columns: Sequence[str]
) -> FeatureRow:
    def latest(name: str, visiting: frozenset[str]) -> datetime:
        if name in visiting:
            raise ValueError("Cyclic feature ancestry")
        node = nodes[name]
        return max(
            [node.available_at, *(latest(parent, visiting | {name}) for parent in node.ancestors)]
        )

    if not columns:
        raise ValueError("Empty feature columns")
    ancestor = max(latest(name, frozenset()) for name in columns)
    if ancestor > decision:
        raise ValueError("Future ancestor in derived feature closure")
    return FeatureRow(
        industry_id=industry_id,
        decision_as_of=decision,
        features={name: nodes[name].value for name in columns},
        missing_features=[name for name in columns if nodes[name].value is None],
        max_ancestor_available_at=ancestor,
    )
