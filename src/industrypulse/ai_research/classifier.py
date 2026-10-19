"""Release and numeric-vintage confidence are graded independently.

规则实现已收敛到 :mod:`industrypulse.core.evidence`，本模块保留 A 线原有的
调用形状（``classify(candidate, ...)``），行为逐字段不变。
"""

from industrypulse.contracts.observation import EvidenceCandidate, EvidenceGrade
from industrypulse.contracts.sources import TimestampPrecision
from industrypulse.core.evidence import grades_for_candidate


def classify(
    candidate: EvidenceCandidate,
    *,
    precision: TimestampPrecision,
    contemporary: bool = False,
    trusted_copy: bool = False,
    policy_date: bool = False,
) -> tuple[EvidenceGrade, EvidenceGrade]:
    return grades_for_candidate(
        has_publication=candidate.publication_at is not None,
        precision=precision,
        has_value=candidate.parsed_value is not None,
        evidence_class=candidate.evidence_class,
        has_raw_hash=bool(candidate.raw_sha256),
        has_locator=bool(candidate.table_locator),
        contemporary=contemporary,
        trusted_copy=trusted_copy,
        policy_date=policy_date,
    )


__all__ = ["classify"]
