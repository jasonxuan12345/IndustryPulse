"""Release and numeric-vintage confidence are graded independently."""

from industrypulse.contracts.observation import EvidenceCandidate, EvidenceClass, EvidenceGrade
from industrypulse.contracts.sources import TimestampPrecision


def classify(
    candidate: EvidenceCandidate,
    *,
    precision: TimestampPrecision,
    contemporary: bool = False,
    trusted_copy: bool = False,
    policy_date: bool = False,
) -> tuple[EvidenceGrade, EvidenceGrade]:
    if candidate.publication_at is None:
        release = EvidenceGrade.C if policy_date else EvidenceGrade.D
    elif precision in {TimestampPrecision.SECOND, TimestampPrecision.MINUTE}:
        release = EvidenceGrade.A
    elif precision == TimestampPrecision.DAY:
        release = EvidenceGrade.B
    else:
        release = EvidenceGrade.C
    if candidate.parsed_value is None or candidate.evidence_class == EvidenceClass.SEARCH_SNIPPET:
        value = EvidenceGrade.D
    elif contemporary and candidate.raw_sha256 and candidate.table_locator:
        value = EvidenceGrade.B if trusted_copy else EvidenceGrade.A
    else:
        value = EvidenceGrade.C
    return release, value
