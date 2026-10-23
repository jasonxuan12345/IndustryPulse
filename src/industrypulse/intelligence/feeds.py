from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx
from pydantic import AwareDatetime, BaseModel

from industrypulse.intelligence.catalog import Source


class NewsItem(BaseModel):
    id: str
    title: str
    summary: str
    url: str
    published_at: AwareDatetime
    time_kind: str = "published"
    publisher: str = ""


def clean_text(value: str, limit: int = 350) -> str:
    value = re.sub(r"<script\b[^>]*>.*?</script>", "", value, flags=re.I | re.S)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", value))).strip()[:limit]


def canonical_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.startswith("utm_") and key not in {"fbclid", "gclid"}
    ]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, urlencode(query), ""))


def parse_date(value: str | int | float | None) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000 if value > 10**12 else value, UTC)
        try:
            result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            result = parsedate_to_datetime(value)
        return result if result.tzinfo else result.replace(tzinfo=UTC)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def make_item(
    title: str,
    summary: str,
    url: str,
    published: str | int | float | None,
    now: datetime,
    publisher: str = "",
) -> NewsItem | None:
    title, url = clean_text(title, 240), canonical_url(url)
    if not title or not url:
        return None
    timestamp = parse_date(published)
    return NewsItem(
        id=hashlib.sha256(url.encode()).hexdigest()[:24],
        title=title,
        summary=clean_text(summary),
        url=url,
        published_at=timestamp or now,
        time_kind="published" if timestamp else "first_seen",
        publisher=clean_text(publisher, 100),
    )


def field_text(fields: dict[str, ElementTree.Element], *names: str) -> str:
    for name in names:
        element = fields.get(name)
        if element is not None:
            value = "".join(element.itertext()).strip()
            if value:
                return value
    return ""


def parse_xml(content: bytes, base_url: str, now: datetime) -> list[NewsItem]:
    content = re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", content)
    root = ElementTree.fromstring(content)
    if root.tag.split("}")[-1].lower() not in {"rss", "feed", "rdf"}:
        raise ValueError("返回的不是 RSS / Atom 订阅内容")
    items = []
    for node in root.iter():
        if node.tag.split("}")[-1] not in {"item", "entry"}:
            continue
        fields = {child.tag.split("}")[-1]: child for child in node}

        link = field_text(fields, "link")
        for child in node:
            if child.tag.split("}")[-1] == "link" and child.get("rel", "alternate") == "alternate":
                link = child.get("href") or link
        if not link:
            link = field_text(fields, "guid", "id")
            if not link.startswith(("http://", "https://")):
                continue
        item = make_item(
            field_text(fields, "title"),
            field_text(fields, "description", "summary", "encoded", "content"),
            urljoin(base_url, link),
            field_text(fields, "pubDate", "published", "date", "updated"),
            now,
            field_text(fields, "source"),
        )
        if item:
            items.append(item)
        if len(items) >= 100:
            break
    return items


def parse_wallstreetcn(payload: dict[str, Any], now: datetime) -> list[NewsItem]:
    if payload.get("code") != 20000 or not isinstance(payload.get("data"), dict):
        raise ValueError("公开资讯接口返回异常状态")
    result = []
    for entry in payload["data"].get("items", []):
        if entry.get("resource_type") in {"ad", "theme"}:
            continue
        entry = entry.get("resource", entry)
        title = entry.get("title") or entry.get("content_text") or entry.get("content_short", "")
        item = make_item(
            title,
            entry.get("content_text") or entry.get("content_short") or entry.get("content", ""),
            entry.get("uri", ""),
            entry.get("display_time"),
            now,
        )
        if item:
            result.append(item)
    return result[:100]


def fetch_source(source: Source, rsshub_base: str, now: datetime) -> list[NewsItem]:
    url = rsshub_base.rstrip("/") + source.route if source.kind == "rsshub" else source.url
    headers = {"User-Agent": "IndustryPulse/0.1 (personal RSS reader)", "Accept": "*/*"}
    with httpx.Client(timeout=httpx.Timeout(12, connect=5), follow_redirects=True) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
    if len(response.content) > 6_000_000:
        raise ValueError("订阅内容超过 6 MB，请选择更精简的源")
    if source.kind == "wallstreetcn":
        return parse_wallstreetcn(response.json(), now)
    return parse_xml(response.content, str(response.url), now)
