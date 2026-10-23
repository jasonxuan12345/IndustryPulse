from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[3]


class Industry(BaseModel):
    id: str
    name: str
    keywords: list[str]


class Domain(BaseModel):
    id: str
    name: str
    industries: list[Industry]


class Source(BaseModel):
    id: str
    name: str
    url: str = ""
    route: str = ""
    domains: list[str]
    industry: str | None = None
    language: str = "en"
    kind: Literal["rss", "rsshub", "search", "wallstreetcn"] = "rss"
    category: str = "media"
    recommended: bool = False
    reference: str = ""


class Catalog(BaseModel):
    domains: list[Domain]
    sources: list[Source]
    references: list[str] = Field(default_factory=list)


def load_catalog() -> Catalog:
    catalog = Catalog.model_validate(
        import_module("yaml").safe_load(
            (ROOT / "config/news_sources.yaml").read_text(encoding="utf-8")
        )
    )
    for source in catalog.sources:
        if source.kind == "rsshub":
            source.reference = "https://github.com/DIYgod/RSSHub"
        elif source.kind == "wallstreetcn":
            source.reference = (
                "https://github.com/newsnext/newsnow/blob/main/server/sources/wallstreetcn.ts"
            )
        else:
            source.reference = source.url
    for domain in catalog.domains:
        for industry in domain.industries:
            query = " OR ".join(f'"{word}"' for word in industry.keywords[:3])
            catalog.sources.append(
                Source(
                    id=f"google-{industry.id}",
                    name=f"Google 新闻·{industry.name}",
                    url="https://news.google.com/rss/search?"
                    + urlencode({"q": query, "hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"}),
                    domains=[domain.id],
                    industry=industry.id,
                    language="zh",
                    kind="search",
                    category="aggregator",
                    recommended=True,
                    reference="https://news.google.com/",
                )
            )
    catalog.references = [
        "https://github.com/plenaryapp/awesome-rss-feeds",
        "https://github.com/zerahchu/rsslist",
        "https://github.com/DIYgod/RSSHub",
        "https://github.com/newsnext/newsnow",
    ]
    return catalog


def supports(source: Source, domain: str, industry: str) -> bool:
    return ("*" in source.domains or domain in source.domains) and (
        source.industry is None or source.industry == industry
    )
