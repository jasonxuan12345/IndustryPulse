"""Treat provider output as untrusted candidates and bind every fact to raw bytes."""

import hashlib
import re
from decimal import Decimal

from industrypulse.ai_research.html_extractor import locate, parse_number
from industrypulse.contracts.observation import (
    EvidenceCandidate,
    EvidenceClass,
    ExtractionEngine,
    RawArtifact,
    VerificationStatus,
)


def validate_output(payload: str, document: bytes, artifact: RawArtifact) -> EvidenceCandidate:
    candidate = EvidenceCandidate.model_validate_json(payload)
    digest = hashlib.sha256(document).hexdigest()
    text = document.decode("utf-8")
    if digest != artifact.sha256 or candidate.raw_sha256 != digest:
        raise ValueError("candidate must reference the exact fetched document")
    if candidate.source_url != artifact.source_url:
        raise ValueError("candidate source URL mismatch")
    if candidate.evidence_class == EvidenceClass.SEARCH_SNIPPET:
        raise ValueError("snippets cannot support extracted facts")
    if not candidate.raw_text or candidate.raw_text not in text or not candidate.table_locator:
        raise ValueError("candidate has no exact original evidence location")
    span = re.fullmatch(r"text\[(\d+):(\d+)\]", candidate.table_locator)
    if span:
        start, end = map(int, span.groups())
        if not 0 <= start < end <= len(text) or text[start:end] != candidate.raw_text:
            raise ValueError("text span does not match the cited evidence")
    else:
        table = re.match(r"table\[(\d+)\]", candidate.table_locator)
        if not table or not candidate.row_label or not candidate.column_label:
            raise ValueError("candidate needs an exact text span or table cell")
        cell = locate(
            text, candidate.row_label, candidate.column_label, table_index=int(table.group(1))
        )
        if (
            cell.locator != candidate.table_locator
            or parse_number(cell.text) != candidate.parsed_value
        ):
            raise ValueError("candidate does not match the deterministic cell extraction")
    if candidate.parsed_value is not None:
        numbers = {
            Decimal(token.replace(",", ""))
            for token in re.findall(
                r"(?<![\d.])[+-]?\d+(?:,\d{3})*(?:\.\d+)?(?![\d.])", candidate.raw_text
            )
        }
        if candidate.parsed_value not in numbers:
            raise ValueError("candidate value is absent from the cited text")
    # Even valid schema and textual support cannot establish semantic correctness.
    return EvidenceCandidate.model_validate(
        {
            **candidate.model_dump(),
            "verification_status": VerificationStatus.NEEDS_REVIEW,
            "extraction_engine": ExtractionEngine.LLM_ASSISTED,
            "reviewer": None,
        }
    )
