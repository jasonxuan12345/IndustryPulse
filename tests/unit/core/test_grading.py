"""证据分级：默认不得升格，且与 A 线 classifier 行为逐字段一致。"""

from __future__ import annotations

import pytest

from industrypulse.ai_research.classifier import classify
from industrypulse.contracts.observation import EvidenceCandidate, EvidenceGrade
from industrypulse.contracts.sources import TimestampPrecision
from industrypulse.core.evidence import release_grade, value_grade


@pytest.mark.parametrize(
    "precision,expected",
    [
        (TimestampPrecision.SECOND, EvidenceGrade.A),
        (TimestampPrecision.MINUTE, EvidenceGrade.A),
        (TimestampPrecision.DAY, EvidenceGrade.B),
        (TimestampPrecision.MONTH, EvidenceGrade.C),
        (TimestampPrecision.UNKNOWN, EvidenceGrade.C),
    ],
)
def test_release_grade_from_precision(
    precision: TimestampPrecision, expected: EvidenceGrade
) -> None:
    assert release_grade(has_publication=True, precision=precision) == expected


def test_missing_publication_is_d_unless_policy_backed() -> None:
    assert (
        release_grade(has_publication=False, precision=TimestampPrecision.UNKNOWN)
        == EvidenceGrade.D
    )
    assert (
        release_grade(has_publication=False, precision=TimestampPrecision.UNKNOWN, policy_date=True)
        == EvidenceGrade.C
    )


def test_value_grade_never_upgrades_without_explicit_claim() -> None:
    # 关键纪律：有哈希、有定位也不算"同期原件"，默认只能是 C
    assert value_grade(has_value=True, has_raw_hash=True, has_locator=True) == EvidenceGrade.C
    assert (
        value_grade(has_value=True, has_raw_hash=True, has_locator=True, contemporary=True)
        == EvidenceGrade.A
    )
    assert (
        value_grade(
            has_value=True,
            has_raw_hash=True,
            has_locator=True,
            contemporary=True,
            trusted_copy=True,
        )
        == EvidenceGrade.B
    )


def test_snippet_and_missing_value_are_d() -> None:
    assert value_grade(has_value=True, is_search_snippet=True) == EvidenceGrade.D
    assert value_grade(has_value=False, contemporary=True) == EvidenceGrade.D


def test_classifier_delegates_to_core(evidence_candidates: list[EvidenceCandidate]) -> None:
    """A 线 classifier 与 core 规则必须给出同一结论，防止两套实现静默分叉。"""
    verified = next(item for item in evidence_candidates if item.verification_status == "verified")
    for precision in TimestampPrecision:
        expected = (
            release_grade(has_publication=verified.publication_at is not None, precision=precision),
            value_grade(
                has_value=verified.parsed_value is not None,
                contemporary=True,
                has_raw_hash=bool(verified.raw_sha256),
                has_locator=bool(verified.table_locator),
            ),
        )
        assert classify(verified, precision=precision, contemporary=True) == expected
    # 不显式主张"同期原件"时，A 线同样只能给 C
    assert classify(verified, precision=TimestampPrecision.MINUTE)[1] == EvidenceGrade.C
