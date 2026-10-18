"""Replaceable search and extraction boundary, including offline/manual mode."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from industrypulse.contracts.observation import ResearchQuery


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    snippet: str = ""
    discovery_url: str | None = None


class AiResearchProvider(Protocol):
    def search(self, query: ResearchQuery, search_text: str) -> list[SearchResult]: ...

    def extract(self, document: str, schema: dict[str, object]) -> str: ...


class OfflineProvider:
    def __init__(self, results: list[SearchResult], extraction_json: str = "{}") -> None:
        self.results, self.extraction_json = results, extraction_json

    def search(self, query: ResearchQuery, search_text: str) -> list[SearchResult]:
        return self.results[: query.max_results]

    def extract(self, document: str, schema: dict[str, object]) -> str:
        return self.extraction_json


class ManualFileProvider(OfflineProvider):
    def __init__(self, path: Path) -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        super().__init__(
            [SearchResult(**result) for result in payload.get("results", [])],
            json.dumps(payload.get("candidate", {})),
        )


def select_provider(
    api_key: str | None, manual_file: Path, configured_provider: AiResearchProvider | None = None
) -> AiResearchProvider:
    if api_key and configured_provider is not None:
        return configured_provider
    return ManualFileProvider(manual_file)
