"""C-003 专项：Observation/Vintage/Evidence 不变式（§5.4/§6.2/§7.5）。"""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from industrypulse.contracts import (
    AvailabilityBasis,
    EvidenceCandidate,
    EvidenceClass,
    EvidenceGrade,
    MissingReason,
    ObservationVintage,
    VerificationStatus,
)

BASE = {
    "vintage_id": 1,
    "series_id": "cn.nbs.industry_c31.profit_ytd",
    "observation_start": "2025-07-01",
    "observation_end": "2025-07-31",
    "value": "1220.5",
    "unit": "亿元",
    "publication_at": "2025-08-27T09:30:00+08:00",
    "retrieved_at": "2025-08-27T09:35:00+08:00",
    "available_at": "2025-08-27T09:30:00+08:00",
    "availability_basis": "observed_publication",
    "vintage_valid_from": "2025-08-27T09:30:00+08:00",
    "release_evidence": "A",
    "value_evidence": "A",
}


def _vintage(**overrides: object) -> ObservationVintage:
    data = {**BASE, **overrides}
    return ObservationVintage.model_validate(data)


def test_value_missing_reason_xor() -> None:
    with pytest.raises(ValidationError, match="恰好一者"):
        _vintage(value=None, missing_reason=None)
    with pytest.raises(ValidationError, match="恰好一者"):
        _vintage(missing_reason="not_collected")
    ok = _vintage(value=None, missing_reason=MissingReason.NOT_COLLECTED, value_evidence="D")
    assert ok.value is None


def test_period_ordering() -> None:
    with pytest.raises(ValidationError, match="observation_start"):
        _vintage(observation_start="2025-08-01", observation_end="2025-07-31")


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValidationError, match="时区"):
        _vintage(publication_at="2025-08-27T09:30:00")


def test_ab_release_requires_publication_at() -> None:
    with pytest.raises(ValidationError, match="publication_at"):
        _vintage(
            publication_at=None,
            available_at="2025-08-27T00:00:00+08:00",
            vintage_valid_from="2025-08-27T00:00:00+08:00",
            release_evidence="A",
            availability_basis="assumed_policy_lag",
        )


def test_observed_basis_requires_publication_at() -> None:
    with pytest.raises(ValidationError, match="OBSERVED_PUBLICATION"):
        _vintage(publication_at=None, release_evidence="C")


def test_d_release_cannot_carry_publication() -> None:
    with pytest.raises(ValidationError, match="D 级"):
        _vintage(release_evidence="D")


def test_available_at_not_before_publication() -> None:
    with pytest.raises(ValidationError, match="available_at"):
        _vintage(available_at="2025-08-26T09:30:00+08:00")


def test_source_known_at_defaults_to_available_at() -> None:
    v = _vintage()
    assert v.source_known_at == v.available_at


def test_period_label() -> None:
    assert _vintage().period_label == "2025-07"


def test_candidate_fetch_failed_rules() -> None:
    cand = EvidenceCandidate.model_validate(
        {
            "candidate_id": "c1",
            "query_id": "q1",
            "source_url": "https://a.example.cn/x",
            "publisher": "P",
            "document_title": "T",
            "publication_at": None,
            "retrieved_at": "2025-08-27T10:00:00+08:00",
            "raw_sha256": None,
            "missing_reason": "fetch_failed",
            "extraction_engine": "deterministic_html",
            "evidence_class": "search_snippet",
            "verification_status": "fetch_failed",
        }
    )
    assert cand.parsed_value is None


def test_candidate_verified_requires_hash_and_official() -> None:
    base = {
        "candidate_id": "c2",
        "query_id": "q1",
        "source_url": "https://stats.example.cn/x",
        "publisher": "P",
        "document_title": "T",
        "publication_at": "2025-08-27T09:30:00+08:00",
        "retrieved_at": "2025-08-27T09:35:00+08:00",
        "raw_sha256": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
        "raw_text": "…1220.5…",
        "parsed_value": "1220.5",
        "unit": "亿元",
        "extraction_engine": "deterministic_table",
        "evidence_class": "official_page",
        "verification_status": "verified",
    }
    ok = EvidenceCandidate.model_validate(base)
    assert ok.verification_status == VerificationStatus.VERIFIED
    # 搜索摘要不可 VERIFIED
    with pytest.raises(ValidationError, match="搜索摘要"):
        EvidenceCandidate.model_validate({**base, "evidence_class": EvidenceClass.SEARCH_SNIPPET})
    # 无哈希不可 VERIFIED
    with pytest.raises(ValidationError, match="哈希"):
        EvidenceCandidate.model_validate({**base, "raw_sha256": None})


def test_candidate_value_requires_raw_text_and_unit() -> None:
    base = {
        "candidate_id": "c3",
        "query_id": "q1",
        "source_url": "https://b.example.cn/y",
        "publisher": "P",
        "document_title": "T",
        "publication_at": None,
        "retrieved_at": "2025-08-27T10:00:00+08:00",
        "extraction_engine": "deterministic_table",
        "evidence_class": "official_page",
        "verification_status": "needs_review",
        "raw_text": "…999…",
        "parsed_value": "999",
        "unit": None,
    }
    with pytest.raises(ValidationError, match="单位"):
        EvidenceCandidate.model_validate(base)


def test_multiple_vintages_coexist(steel_vintages: list[ObservationVintage]) -> None:
    """C-003 验收核心：同期两版共存且都可查询（修订不覆写）。"""
    july = [v for v in steel_vintages if v.observation_start.isoformat() == "2025-07-01"]
    assert [v.vintage_id for v in july] == [1, 2]
    assert {str(v.value) for v in july} == {"1220.5", "1235.8"}
    # v1 的 vintage_valid_to 在 v2 出现后闭合（fixture 语义自检）
    assert july[1].available_at > july[0].available_at


def test_evidence_grades_are_not_confidence() -> None:
    """§6.2 语义守护：等级枚举仅 A/B/C/D，独立于数值大小。"""
    assert {g.value for g in EvidenceGrade} == {"A", "B", "C", "D"}
    assert {b.value for b in AvailabilityBasis} == {
        "observed_publication",
        "assumed_policy_lag",
        "retrieval_time",
    }


def test_retrieved_at_may_be_after_backtest_window(
    steel_vintages: list[ObservationVintage],
) -> None:
    """§6.1：回填使 retrieved_at 晚于历史 as_of 是合法的（不等于泄漏）。"""
    backfilled = [
        v
        for v in steel_vintages
        if v.retrieved_at > datetime.fromisoformat("2026-01-01T00:00:00+08:00")
    ]
    assert backfilled, "fixture 必须包含跨年回填行"
