"""Official document collection never upgrades snippets to facts."""

from datetime import UTC, datetime
from typing import Protocol

import httpx

from industrypulse.ai_research.planner import allowed_url
from industrypulse.ai_research.provider import SearchResult
from industrypulse.contracts.observation import (
    EvidenceCandidate,
    EvidenceClass,
    ExtractionEngine,
    MissingReason,
    RawArtifact,
    ResearchQuery,
    VerificationStatus,
)


class ArtifactFetcher(Protocol):
    def fetch(
        self, source_id: str, url: str, *, discovery_url: str | None = None
    ) -> RawArtifact: ...


def collect(
    fetcher: ArtifactFetcher,
    source_id: str,
    query: ResearchQuery,
    result: SearchResult,
    publisher: str,
) -> RawArtifact | EvidenceCandidate:
    if not allowed_url(result.url, query.allowed_domains):
        raise PermissionError("candidate URL outside query whitelist")
    try:
        return fetcher.fetch(source_id, result.url, discovery_url=result.discovery_url)
    except httpx.HTTPError:
        return EvidenceCandidate(
            candidate_id=f"{query.query_id}:failed",
            query_id=query.query_id,
            source_url=result.url,
            discovery_url=result.discovery_url,
            publisher=publisher,
            document_title=result.title,
            publication_at=None,
            retrieved_at=datetime.now(UTC),
            missing_reason=MissingReason.FETCH_FAILED,
            extraction_engine=ExtractionEngine.DETERMINISTIC_HTML,
            evidence_class=EvidenceClass.SEARCH_SNIPPET,
            verification_status=VerificationStatus.FETCH_FAILED,
        )
