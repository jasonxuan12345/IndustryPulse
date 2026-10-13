"""C-002 专项：SourceDescriptor / SeriesSpec 领域不变式。"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from industrypulse.contracts import (
    Frequency,
    LegalStatus,
    ReleasePolicy,
    RevisionBehavior,
    SeriesSpec,
    SourceDescriptor,
    SourceStatus,
)


def _policy() -> ReleasePolicy:
    return ReleasePolicy(policy_version="p1", description="d", release_lag_days=30, safety_days=5)


def _descriptor(**overrides: object) -> SourceDescriptor:
    base: dict[str, object] = {
        "source_id": "src.demo",
        "owner": "S",
        "url": "https://example.gov.cn/data",
        "data_fields": ["x"],
        "frequency": Frequency.MONTHLY,
        "legal_status": LegalStatus.PUBLIC_OFFICIAL,
        "revision_behavior": RevisionBehavior.REVISED_LATER,
        "priority": "S0",
    }
    base.update(overrides)
    return SourceDescriptor.model_validate(base)


def test_continuously_revised_requires_policy() -> None:
    with pytest.raises(ValidationError, match="release_policy"):
        _descriptor(revision_behavior=RevisionBehavior.CONTINUOUSLY_REVISED)
    ok = _descriptor(
        revision_behavior=RevisionBehavior.CONTINUOUSLY_REVISED, release_policy=_policy()
    )
    assert ok.release_policy is not None


def test_default_status_is_candidate() -> None:
    """§4.1 核验声明：验收前一律 CANDIDATE。"""
    assert _descriptor().status == SourceStatus.CANDIDATE


def test_series_unit_scale_must_be_positive() -> None:
    base: dict[str, object] = {
        "series_id": "cn.demo.series",
        "source_id": "src.demo",
        "unit": "万吨",
        "frequency": Frequency.MONTHLY,
        "revision_policy": RevisionBehavior.REVISED_LATER,
        "timestamp_precision": "day",
        "description": "d",
        "unit_scale": Decimal("-1"),
    }
    with pytest.raises(ValidationError, match="unit_scale"):
        SeriesSpec.model_validate(base)
    base["unit_scale"] = Decimal("10000")  # 万元→亿元
    spec = SeriesSpec.model_validate(base)
    assert spec.unit_scale == Decimal("10000")


def test_series_id_shape() -> None:
    base = {
        "series_id": "Not Valid ID",
        "source_id": "src.demo",
        "unit": "亿元",
        "frequency": "M",
        "revision_policy": "revised_later",
        "timestamp_precision": "day",
        "description": "d",
    }
    with pytest.raises(ValidationError):
        SeriesSpec.model_validate(base)
