"""缓存已经核对过的事件。模型调用由路由传入，这里不保存密钥。"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from industrypulse.core import TZ_SHANGHAI
from industrypulse.intelligence.events import (
    apply_extraction,
    build_brief,
    describe_cluster,
    identified_clusters,
    recover_extraction,
    stories_for_day,
)
from industrypulse.intelligence.service import write_json


class EventBook:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.index_path = path.with_name("event-clusters.json")
        self.records: dict[str, dict[str, Any]] = {}
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self.records = {
                    key: value
                    for key, value in payload.items()
                    if isinstance(key, str) and isinstance(value, dict)
                }

    def save(self) -> None:
        write_json(self.path, self.records)

    def brief(self, stories: list[dict[str, Any]], sector: str, day: date | None) -> dict[str, Any]:
        return build_brief(stories, sector, day, self.records, index_path=self.index_path)

    def extract(
        self,
        stories: list[dict[str, Any]],
        sector: str,
        day: date | None,
        complete: Callable[[str], str],
        *,
        model: str,
        limit: int = 8,
    ) -> dict[str, Any]:
        chosen, selected = stories_for_day(stories, sector, day)
        pending = []
        for event_id, members in identified_clusters(selected, self.index_path):
            current = describe_cluster(members)
            record = self.records.get(event_id) or self.records.get(current["cluster_id"])
            if recover_extraction(members, record) is not None:
                continue
            pending.append((event_id, members))
        for event_id, members in pending[:limit]:
            event = apply_extraction(members, complete)
            event["cluster_id"] = event_id
            self.records[event_id] = {
                "fingerprint": event["fingerprint"],
                "model": model,
                "extracted_at": datetime.now(TZ_SHANGHAI).isoformat(),
                "event": event,
            }
        self.save()
        return self.brief(stories, sector, chosen)
