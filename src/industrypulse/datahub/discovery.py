"""官方公告发现：从新闻发布列表页确定性地识别目标文档。

A-009 在 AI 线做"跨年度公告批量发现"，这里提供 S 线对应物：不调用搜索、不猜 URL，
只解析官方列表页的标题与链接。标题格式是国家统计局公开、稳定的写法，因此可以用
严格正则识别；识别不了就返回空，绝不"猜一个看起来像的"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

#: 支持的公告类别。
KINDS = ("profit", "pmi", "iva")

#: 标题正则。国家统计局对"1—7月份"使用全角破折号，但也见过半角写法。
_PATTERNS: dict[str, re.Pattern[str]] = {
    "profit": re.compile(r"(\d{4})年(?:1[—\-–])?(\d{1,2})月份全国规模以上工业企业利润"),
    "pmi": re.compile(r"(\d{4})年(\d{1,2})月中国采购经理指数"),
    "iva": re.compile(r"(\d{4})年(?:1[—\-–])?(\d{1,2})月份规模以上工业增加值"),
}

#: 年度利润公告用另一种标题（"2025年全国规模以上工业企业利润…"），代表 12 月累计。
_ANNUAL_PROFIT = re.compile(r"(\d{4})年全国规模以上工业企业利润")

_ANCHOR = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Announcement:
    """一条已识别的官方公告。``period`` 为所属期 YYYY-MM。"""

    kind: str
    period: str
    url: str
    title: str


def _clean_title(raw: str) -> str:
    return _SPACE.sub(" ", _TAG.sub("", raw)).strip()


def _match(kind: str, title: str) -> tuple[int, int]:
    """返回 (年, 月)；未识别返回 (0, 0)。年度利润公告折算为 12 月累计。"""
    if kind == "profit":
        annual = _ANNUAL_PROFIT.search(title)
        if annual is not None:
            return int(annual.group(1)), 12
    match = _PATTERNS[kind].search(title)
    if match is None:
        return (0, 0)
    return int(match.group(1)), int(match.group(2))


def discover(
    listing_html: str, *, base_url: str, kinds: tuple[str, ...] = KINDS
) -> list[Announcement]:
    """解析一个列表页，返回其中可识别的目标公告（按出现顺序去重）。

    ``base_url`` 用于把相对链接补成绝对 URL；未知类别直接拒绝，避免静默忽略。
    """
    unknown = [kind for kind in kinds if kind not in _PATTERNS]
    if unknown:
        raise ValueError(f"未支持的公告类别：{unknown}")
    seen: set[tuple[str, str]] = set()
    found: list[Announcement] = []
    for href, raw_title in _ANCHOR.findall(listing_html):
        title = _clean_title(raw_title)
        if not title:
            continue
        for kind in kinds:
            year, month = _match(kind, title)
            if month < 1 or month > 12:
                continue
            period = f"{year:04d}-{month:02d}"
            key = (kind, period)
            if key in seen:
                continue
            seen.add(key)
            found.append(
                Announcement(kind=kind, period=period, url=urljoin(base_url, href), title=title)
            )
    return found


def in_range(announcements: list[Announcement], start: str, end: str) -> list[Announcement]:
    """按所属期闭区间过滤；``start``/``end`` 为 YYYY-MM。"""
    for value in (start, end):
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
            raise ValueError(f"所属期必须是 YYYY-MM：{value!r}")
    if start > end:
        raise ValueError("起始期不得晚于结束期")
    return sorted(
        (item for item in announcements if start <= item.period <= end),
        key=lambda item: (item.period, item.kind),
    )


__all__ = ["KINDS", "Announcement", "discover", "in_range"]
