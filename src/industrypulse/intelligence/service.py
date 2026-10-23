from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, Field

from industrypulse.intelligence.catalog import ROOT, Catalog, Source, load_catalog, supports
from industrypulse.intelligence.feeds import NewsItem, fetch_source


class NewsSettings(BaseModel):
    mode: Literal["builtin", "custom", "demo"] = "builtin"
    selections: dict[str, list[str]] = Field(default_factory=dict)
    rsshub_base: str = "https://rsshub.bestblogs.dev"
    refresh_minutes: int = Field(default=15, ge=5, le=1440)


class SourceCache(BaseModel):
    items: list[NewsItem] = Field(default_factory=list)
    last_attempt: datetime | None = None
    last_success: datetime | None = None
    error: str | None = None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def matches(text: str, keywords: list[str]) -> bool:
    text = text.casefold()
    for keyword in keywords:
        keyword = keyword.casefold()
        if keyword.isascii():
            if re.search(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", text):
                return True
        elif keyword in text:
            return True
    return False


def company_name(title: str) -> str | None:
    companies = {
        "NVIDIA": ["NVIDIA", "英伟达"],
        "AMD": ["AMD", "超威"],
        "Intel": ["Intel", "英特尔"],
        "台积电": ["台积电", "TSMC"],
        "中芯国际": ["中芯国际", "SMIC"],
        "ASML": ["ASML", "阿斯麦"],
        "三星": ["三星", "Samsung"],
        "SK 海力士": ["海力士", "SK hynix"],
        "美光": ["美光", "Micron"],
        "Arm": ["Arm"],
        "OpenAI": ["OpenAI"],
        "DeepSeek": ["DeepSeek", "深度求索"],
        "Google": ["Google", "谷歌"],
        "微软": ["微软", "Microsoft"],
        "阿里巴巴": ["阿里巴巴", "Alibaba"],
        "腾讯": ["腾讯", "Tencent"],
        "特斯拉": ["特斯拉", "Tesla"],
        "比亚迪": ["比亚迪", "BYD"],
        "宁德时代": ["宁德时代", "CATL"],
        "隆基绿能": ["隆基", "LONGi"],
        "宝钢": ["宝钢", "宝武", "Baowu", "Baosteel"],
        "安赛乐米塔尔": ["安赛乐", "ArcelorMittal"],
        "辉瑞": ["辉瑞", "Pfizer"],
        "礼来": ["礼来", "Eli Lilly"],
        "诺和诺德": ["诺和诺德", "Novo Nordisk"],
    }
    return next((name for name, aliases in companies.items() if matches(title, aliases)), None)


class NewsService:
    def __init__(self, directory: Path | None = None, catalog: Catalog | None = None) -> None:
        self.directory = directory or Path(
            os.getenv("INDUSTRYPULSE_NEWS_DIR", str(ROOT / "data/news"))
        )
        self.settings_path = self.directory / "settings.json"
        self.cache_path = self.directory / "cache.json"
        self.catalog = catalog or load_catalog()
        self.sources = {source.id: source for source in self.catalog.sources}
        self.lock = threading.RLock()
        self.worker: threading.Thread | None = None
        self.timer: threading.Thread | None = None
        self.closed = threading.Event()
        self.completed = 0
        self.total = 0
        self.last_finished: datetime | None = None
        self.last_started: datetime | None = None
        self.settings = self.defaults()
        self.warning = ""
        self.cache: dict[str, SourceCache] = {}
        try:
            if self.settings_path.exists():
                self.settings = NewsSettings.model_validate_json(
                    self.settings_path.read_text(encoding="utf-8")
                )
            if self.cache_path.exists():
                payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
                self.cache = {
                    key: SourceCache.model_validate(value) for key, value in payload.items()
                }
        except (ValueError, OSError):
            self.warning = "本地资讯配置或缓存不可读；请在来源设置重新保存并抓取。"

    def defaults(self) -> NewsSettings:
        selections = {}
        for domain in self.catalog.domains:
            for industry in domain.industries:
                selections[industry.id] = [
                    source.id
                    for source in self.catalog.sources
                    if source.recommended and supports(source, domain.id, industry.id)
                ]
        return NewsSettings(selections=selections)

    def save(self, settings: NewsSettings) -> None:
        industry_domains = {
            industry.id: domain.id
            for domain in self.catalog.domains
            for industry in domain.industries
        }
        for industry, identifiers in settings.selections.items():
            if industry not in industry_domains:
                raise ValueError(f"未知行业：{industry}")
            for identifier in identifiers:
                if identifier not in self.sources or not supports(
                    self.sources[identifier], industry_domains[industry], industry
                ):
                    raise ValueError(f"来源 {identifier} 不适用于行业 {industry}")
        parts = urlsplit(settings.rsshub_base)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("RSSHub 地址必须是 http(s) 地址")
        settings = settings.model_copy(deep=True)
        settings.selections = {
            key: list(dict.fromkeys(value)) for key, value in settings.selections.items()
        }
        with self.lock:
            write_json(self.settings_path, settings.model_dump(mode="json"))
            self.settings = settings
            self.warning = ""

    def selected(self) -> set[str]:
        return {
            identifier
            for identifiers in self.settings.selections.values()
            for identifier in identifiers
            if identifier in self.sources
        }

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "running": bool(self.worker and self.worker.is_alive()),
                "completed": self.completed,
                "total": self.total,
                "last_started": self.last_started.isoformat() if self.last_started else None,
                "last_finished": self.last_finished.isoformat() if self.last_finished else None,
                "enabled_sources": len(self.selected()),
                "cached_sources": sum(bool(value.items) for value in self.cache.values()),
                "failed_sources": sum(
                    bool(value.error) for key, value in self.cache.items() if key in self.selected()
                ),
                "successful_sources": sum(
                    bool(value.last_success)
                    for key, value in self.cache.items()
                    if key in self.selected()
                ),
                "warning": self.warning,
            }

    def catalog_payload(self) -> dict[str, Any]:
        with self.lock:
            result = self.catalog.model_dump(mode="json")
            result["settings"] = self.settings.model_dump(mode="json")
            result["defaults"] = self.defaults().model_dump(mode="json")
            result["status"] = self.status()
            for source in result["sources"]:
                cache = self.cache.get(source["id"], SourceCache())
                source["status"] = {
                    "state": (
                        ("stale" if cache.items else "error")
                        if cache.error
                        else ("ok" if cache.items else "empty")
                        if cache.last_success
                        else "never"
                    ),
                    "count": len(cache.items),
                    "last_attempt": cache.last_attempt.isoformat() if cache.last_attempt else None,
                    "last_success": cache.last_success.isoformat() if cache.last_success else None,
                    "error": cache.error,
                }
            return result

    def refresh(self, source_ids: list[str] | None = None, *, force: bool = False) -> bool:
        with self.lock:
            if self.closed.is_set() or (self.worker and self.worker.is_alive()):
                return False
            now = datetime.now(UTC)
            if (
                not force
                and self.last_started
                and now - self.last_started < timedelta(minutes=self.settings.refresh_minutes)
            ):
                return False
            identifiers = set(source_ids) if source_ids is not None else self.selected()
            if identifiers - self.sources.keys():
                raise ValueError("包含未知数据源")
            self.last_started = now
            self.completed, self.total = 0, len(identifiers)
            self.worker = threading.Thread(
                target=self._collect,
                args=(sorted(identifiers), self.settings.rsshub_base),
                name="industrypulse-public-news",
                daemon=True,
            )
            self.worker.start()
            return True

    def _collect(self, identifiers: list[str], rsshub_base: str) -> None:
        with ThreadPoolExecutor(max_workers=8) as pool:
            pending = {
                pool.submit(self._fetch, self.sources[identifier], rsshub_base): identifier
                for identifier in identifiers
            }
            for future in as_completed(pending):
                identifier = pending[future]
                try:
                    items = future.result()
                    error = None
                except Exception as exception:
                    items = []
                    error = str(exception)[:300]
                with self.lock:
                    old = self.cache.get(identifier, SourceCache())
                    now = datetime.now(UTC)
                    if error is None:
                        known = {item.id: item for item in old.items}
                        for item in items:
                            if item.time_kind == "first_seen" and item.id in known:
                                item.published_at = known[item.id].published_at
                            known[item.id] = item
                        retained = [
                            item
                            for item in known.values()
                            if now - item.published_at < timedelta(days=180)
                        ]
                        old.items = sorted(
                            retained, key=lambda item: item.published_at, reverse=True
                        )[:300]
                        old.last_success = now
                    old.last_attempt, old.error = now, error
                    self.cache[identifier] = old
                    self.completed += 1
                    try:
                        write_json(
                            self.cache_path,
                            {
                                key: value.model_dump(mode="json")
                                for key, value in self.cache.items()
                            },
                        )
                    except OSError as exception:
                        self.warning = f"资讯已抓取，但缓存保存失败：{exception}"
        with self.lock:
            self.last_finished = datetime.now(UTC)

    def _fetch(self, source: Source, rsshub_base: str) -> list[NewsItem]:
        if self.closed.is_set():
            return []
        return fetch_source(source, rsshub_base, datetime.now(UTC))

    def feed(self) -> dict[str, Any]:
        if os.getenv("INDUSTRYPULSE_NEWS_AUTO_FETCH", "1") != "0":
            self.refresh()
            with self.lock:
                if self.timer is None:
                    self.timer = threading.Thread(
                        target=self._auto_refresh, name="industrypulse-news-timer", daemon=True
                    )
                    self.timer.start()
        with self.lock:
            stories: dict[str, dict[str, Any]] = {}
            for domain in self.catalog.domains:
                for industry in domain.industries:
                    for identifier in self.settings.selections.get(industry.id, []):
                        source = self.sources.get(identifier)
                        if source is None or not supports(source, domain.id, industry.id):
                            continue
                        for item in self.cache.get(identifier, SourceCache()).items:
                            if source.industry != industry.id and not matches(
                                item.title + " " + item.summary, industry.keywords
                            ):
                                continue
                            key = hashlib.sha256((domain.id + item.url).encode()).hexdigest()[:24]
                            if key not in stories:
                                personnel = matches(
                                    item.title,
                                    [
                                        "任命",
                                        "人事",
                                        "辞任",
                                        "辞职",
                                        "离职",
                                        "appointed",
                                        "appoints",
                                        "steps down",
                                        "resigns",
                                        "appointment",
                                    ],
                                )
                                stories[key] = {
                                    "id": key,
                                    "sector": domain.id,
                                    "industry_ids": [],
                                    "source_id": identifier,
                                    "source_ids": [],
                                    "category": (
                                        "personnel"
                                        if personnel
                                        else "hotspot"
                                        if source.id
                                        in {"hub-cls-hot", "hub-wscn-hot", "hub-gelonghui"}
                                        else "news"
                                    ),
                                    "title": item.title,
                                    "summary": item.summary,
                                    "published_at": item.published_at.isoformat(),
                                    "time_kind": item.time_kind,
                                    "source": (
                                        f"{item.publisher}（经 {source.name}）"
                                        if item.publisher
                                        else source.name
                                    ),
                                    "url": item.url,
                                    "company": company_name(item.title),
                                    "tags": [],
                                }
                            if industry.id not in stories[key]["industry_ids"]:
                                stories[key]["industry_ids"].append(industry.id)
                                stories[key]["tags"].append(industry.name)
                            if identifier not in stories[key]["source_ids"]:
                                stories[key]["source_ids"].append(identifier)
            successes = [
                cache.last_success
                for key, cache in self.cache.items()
                if key in self.selected() and cache.last_success
            ]
            return {
                "mode": "live",
                "updated_at": (max(successes) if successes else datetime.now(UTC)).isoformat(),
                "source": "内置公开资讯源 · 保留原文链接与来源",
                "sectors": [
                    {"id": domain.id, "name": domain.name} for domain in self.catalog.domains
                ],
                "stories": sorted(
                    stories.values(), key=lambda item: item["published_at"], reverse=True
                ),
                "companies": list(
                    {
                        (story["sector"], story["company"]): {
                            "id": hashlib.sha256(
                                (story["sector"] + story["company"]).encode()
                            ).hexdigest()[:24],
                            "sector": story["sector"],
                            "name": story["company"],
                            "symbol": "",
                            "focus": "公开资讯提及 · 非官方实体识别结果",
                        }
                        for story in stories.values()
                        if story.get("company")
                    }.values()
                ),
                "collection": self.status(),
            }

    def close(self) -> None:
        self.closed.set()

    def _auto_refresh(self) -> None:
        while not self.closed.wait(60):
            if self.settings.mode == "builtin":
                self.refresh()
