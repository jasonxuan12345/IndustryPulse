"""Explicit official publication metadata; HTTP modification time is never used."""

import re
from datetime import datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from industrypulse.contracts.sources import TimestampPrecision


class _Metadata(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta":
            return
        metadata = dict(attrs)
        name = metadata.get("name") or metadata.get("property") or ""
        if name.lower() in {"pubdate", "publishdate", "article:published_time", "publication_date"}:
            if metadata.get("content"):
                self.values.append(str(metadata["content"]))


def extract_publication(html: str) -> tuple[datetime | None, TimestampPrecision]:
    parser = _Metadata()
    parser.feed(html)
    values = parser.values
    if not values:
        clean = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
        clean = re.sub(r"<[^>]+>", " ", clean)
        values = re.findall(
            r"(?:发布时间|发布日期)\s*[：:]\s*"
            r"(\d{4}[-/]\d{2}[-/]\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)",
            clean,
        )
        if not values:
            # 2021–2024 的公告页没有 PubDate meta，也没有"发布时间："前缀，
            # 日期以裸 "2022/07/27 09:30" 形式出现在页头。只扫描前 4000 字，
            # 并沿用下方的"多值即冲突"保护，避免抓到正文里的日期。
            head = re.sub(r"\s+", " ", clean[:4000])
            values = re.findall(r"\d{4}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}(?::\d{2})?", head)
    parsed: set[tuple[datetime, TimestampPrecision]] = set()
    for value in values:
        normalized = value.strip().replace("/", "-")
        precision = TimestampPrecision.DAY
        if ":" in normalized:
            precision = (
                TimestampPrecision.SECOND
                if re.search(r"[T ]\d{2}:\d{2}:\d{2}", normalized)
                else TimestampPrecision.MINUTE
            )
        timestamp = datetime.fromisoformat(normalized)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        parsed.add((timestamp, precision))
    if len(parsed) > 1:
        raise ValueError("conflicting publication metadata requires review")
    return next(iter(parsed)) if parsed else (None, TimestampPrecision.UNKNOWN)
