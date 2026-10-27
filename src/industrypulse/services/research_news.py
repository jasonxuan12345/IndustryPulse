"""Route real cached RSS leads without promoting headlines into model features."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import yaml

from industrypulse.intelligence.feeds import NewsItem, canonical_url
from industrypulse.intelligence.service import matches

if TYPE_CHECKING:
    from industrypulse.services.workbench import Workbench


def leads(service: Workbench, industry_id: str, now: datetime) -> list[dict[str, Any]]:
    directory = service.root / "data/news"
    options_path = directory / "settings.json"
    if (
        options_path.exists()
        and json.loads(options_path.read_text(encoding="utf-8")).get("mode") == "demo"
    ):
        return []
    routing = service.root / "config/research/news-routing.yaml"
    cache_path = directory / "cache.json"
    if not routing.exists() or not cache_path.exists():
        return []
    config = yaml.safe_load(routing.read_text(encoding="utf-8"))
    keywords = config["keywords"].get(industry_id, [])
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    selected = {}
    for source, payload in cache.items():
        success = payload.get("last_success")
        if not success or datetime.fromisoformat(success) > now:
            continue
        for raw in payload.get("items", []):
            item = NewsItem.model_validate(raw)
            if not now - timedelta(days=config["max_age_days"]) <= item.published_at <= now:
                continue
            if not matches(item.title + " " + item.summary, keywords):
                continue
            url = canonical_url(item.url)
            if not url:
                continue
            selected[url] = {
                "title": item.title,
                "summary": item.summary,
                "url": url,
                "source_id": source,
                "published_at": item.published_at.isoformat(),
                "time_kind": item.time_kind,
                "routing_version": config["version"],
                "evidence": "RSS或媒体线索，未核定为量化事实；不能用于历史训练或改变资产准入",
            }
    return sorted(selected.values(), key=lambda i: (i["published_at"], i["url"]), reverse=True)[
        : config["max_items_per_industry"]
    ]
