"""Versioned plans with exact host allowlists."""

from datetime import datetime
from urllib.parse import urlsplit

from industrypulse.contracts.observation import ResearchQuery


def allowed_url(url: str, domains: list[str]) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and not parsed.username
        and any(host == domain or host.endswith("." + domain) for domain in domains)
    )


def plan(
    series_id: str, period: str, publisher: str, host: str, created_at: datetime
) -> tuple[ResearchQuery, str]:
    datetime.strptime(period, "%Y-%m")
    if not publisher.strip() or "/" in host or not host:
        raise ValueError("publisher and plain host required")
    year, month = period.split("-")
    query = ResearchQuery(
        query_id=f"{series_id}:{period}:v1",
        series_id=series_id,
        observation_period=period,
        target_source_host=host,
        template_version="official-v1",
        created_at=created_at,
        allowed_domains=[host],
    )
    return query, f"{publisher} {year}年1—{int(month)}月份 {series_id} 表3 site:{host}"
