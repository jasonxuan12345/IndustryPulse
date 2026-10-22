"""Offline-capable query-to-review pipeline, deliberately never writes Silver."""

import hashlib
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from industrypulse.ai_research.html_extractor import locate, parse_number
from industrypulse.ai_research.planner import allowed_url
from industrypulse.ai_research.provider import AiResearchProvider
from industrypulse.ai_research.review_queue import ReviewQueue
from industrypulse.contracts.observation import (
    EvidenceCandidate,
    EvidenceClass,
    ExtractionEngine,
    MissingReason,
    RawArtifact,
    ResearchQuery,
    VerificationStatus,
)


class ParsedLike(Protocol):
    """Read-only structural input from any deterministic table adapter."""

    @property
    def series_id(self) -> str: ...

    @property
    def period(self) -> str: ...

    @property
    def locator(self) -> str: ...

    @property
    def row_label(self) -> str: ...

    @property
    def column_label(self) -> str: ...

    @property
    def raw_text(self) -> str: ...

    @property
    def value(self) -> Decimal | None: ...

    @property
    def unit(self) -> str: ...

    @property
    def publication_at(self) -> datetime | None: ...

    @property
    def missing_reason(self) -> MissingReason | None: ...


def table_candidate(
    query: ResearchQuery,
    parsed: ParsedLike,
    artifact: RawArtifact,
    *,
    publisher: str,
    title: str,
) -> EvidenceCandidate:
    if parsed.period != query.observation_period or parsed.series_id != query.series_id:
        raise ValueError("parsed period does not match research query")
    if not artifact.source_url:
        raise ValueError("raw artifact source URL is required")
    if not allowed_url(artifact.source_url, query.allowed_domains):
        raise PermissionError("artifact source outside the approved query domains")
    path = Path(artifact.uri)
    if hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256:
        raise ValueError("archived bytes no longer match their checksum")
    import re

    table = re.match(r"table\[(\d+)\]", parsed.locator)
    if not table:
        raise ValueError("table candidates require an exact deterministic table location")
    cell = locate(
        path.read_text(encoding="utf-8"),
        parsed.row_label,
        parsed.column_label,
        table_index=int(table.group(1)),
    )
    if (
        cell.locator != parsed.locator
        or cell.text != parsed.raw_text
        or parse_number(cell.text) != parsed.value
    ):
        raise ValueError("parsed candidate does not match its archived source cell")
    return EvidenceCandidate(
        candidate_id=f"{query.query_id}:{artifact.sha256}:{parsed.locator}",
        query_id=query.query_id,
        source_url=artifact.source_url,
        discovery_url=artifact.discovery_url,
        publisher=publisher,
        document_title=title,
        publication_at=parsed.publication_at,
        retrieved_at=artifact.retrieved_at,
        raw_sha256=artifact.sha256,
        table_locator=parsed.locator,
        row_label=parsed.row_label,
        column_label=parsed.column_label,
        observed_period=parsed.period,
        raw_text=parsed.raw_text or None,
        parsed_value=parsed.value,
        unit=parsed.unit,
        missing_reason=parsed.missing_reason,
        extraction_engine=ExtractionEngine.DETERMINISTIC_TABLE,
        evidence_class=EvidenceClass.OFFICIAL_PAGE,
        verification_status=VerificationStatus.NEEDS_REVIEW,
        notes="Deterministic candidate; contemporary status and publication require review",
    )


class EvidencePipeline:
    def __init__(self, provider: AiResearchProvider, queue: ReviewQueue) -> None:
        self.provider, self.queue = provider, queue

    def extract_unstructured(self, document: bytes, artifact: RawArtifact) -> EvidenceCandidate:
        from industrypulse.ai_research.llm_extract import validate_output

        metadata = {
            "source_url": artifact.source_url,
            "raw_sha256": artifact.sha256,
            "retrieved_at": artifact.retrieved_at.isoformat(),
        }
        import json

        output = self.provider.extract(
            "原件元数据（不属于正文）：\n"
            + json.dumps(metadata, ensure_ascii=False)
            + "\n原文：\n"
            + document.decode("utf-8"),
            EvidenceCandidate.model_json_schema(),
        )
        candidate = validate_output(output, document, artifact)
        return self.queue.reconcile([candidate])[0]
