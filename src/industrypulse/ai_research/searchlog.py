"""Append-only reproducible search snapshots and stable URL deduplication."""

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urldefrag

from industrypulse.ai_research.planner import allowed_url
from industrypulse.ai_research.provider import SearchResult
from industrypulse.contracts.observation import ResearchQuery


def archive_search(
    path: Path, query: ResearchQuery, text: str, searched_at: datetime, results: list[SearchResult]
) -> list[SearchResult]:
    if searched_at.tzinfo is None:
        raise ValueError("search timestamp must be timezone aware")
    unique: dict[str, SearchResult] = {}
    for result in results:
        url = urldefrag(result.url).url
        if allowed_url(url, query.allowed_domains):
            unique.setdefault(
                url, SearchResult(url, result.title, result.snippet, result.discovery_url)
            )
    selected = list(unique.values())[: query.max_results]
    record = {
        "query": query.model_dump(mode="json"),
        "search_text": text,
        "searched_at": searched_at.isoformat(),
        "results": [asdict(r) for r in selected],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    return selected


def replay(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
