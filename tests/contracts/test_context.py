"""C-004 专项：ResearchMode 三模式行为一致性（§6.2/§6.3/§6.5）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from industrypulse.contracts import (
    MODE_EVIDENCE_GATES,
    AnalysisContext,
    DataQuality,
    EvidenceGrade,
    ResearchMode,
)


def test_mode_enum_values_frozen() -> None:
    assert {m.value for m in ResearchMode} == {"strict_pit", "reconstructed_pit", "exploratory"}


def test_gates_stricter_than_reconstructed_than_exploratory() -> None:
    strict_r, strict_v = MODE_EVIDENCE_GATES[ResearchMode.STRICT_PIT]
    recon_r, recon_v = MODE_EVIDENCE_GATES[ResearchMode.RECONSTRUCTED_PIT]
    expl_r, expl_v = MODE_EVIDENCE_GATES[ResearchMode.EXPLORATORY]
    assert strict_r == strict_v == frozenset({EvidenceGrade.A, EvidenceGrade.B})
    assert recon_r == recon_v == frozenset({EvidenceGrade.A, EvidenceGrade.B, EvidenceGrade.C})
    assert expl_r == expl_v == frozenset(set(EvidenceGrade))
    # 层级关系：STRICT ⊂ RECONSTRUCTED ⊂ EXPLORATORY
    assert strict_r < recon_r < expl_r and strict_v < recon_v < expl_v


def test_analysis_context_requires_aware_as_of() -> None:
    base = {
        "as_of": "2025-09-30T17:00:00+08:00",
        "research_mode": "strict_pit",
        "industry_id": "CN_NBS_C31",
        "dataset_snapshot_id": "sha256:abcd1234",
        "mapping_version": "v1",
    }
    with pytest.raises(ValidationError, match="时区"):
        AnalysisContext.model_validate({**base, "as_of": "2025-09-30T17:00:00"})
    ctx = AnalysisContext.model_validate(base)
    assert ctx.is_pit_verified
    assert not ctx.model_copy(
        update={"research_mode": ResearchMode.RECONSTRUCTED_PIT}
    ).is_pit_verified


def test_data_quality_bounds() -> None:
    with pytest.raises(ValidationError):
        DataQuality(coverage=1.5, vintage_verified_ratio=0.5)
    DataQuality(coverage=0.0, vintage_verified_ratio=0.0)  # 覆盖 0 合法：不得当恶化解读
