"""Acquisition and scheduling adapters using the existing ingest pipeline."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx

from industrypulse.contracts import TZ_SHANGHAI

if TYPE_CHECKING:
    from industrypulse.services.workbench import Workbench


def fetcher(service: Workbench, client: httpx.Client) -> Any:
    from industrypulse.datahub.fetcher import Fetcher
    from industrypulse.datahub.source_registry import SourceRegistry

    return Fetcher(
        SourceRegistry(
            service.catalog()["sources"]
            and [
                service.repository().source_descriptor(item["source_id"])
                for item in service.catalog()["sources"]
            ]
        ),
        service.output / "downloads",
        client,
        interval=1.5,
    )


def backfill(service: Workbench, options: dict[str, Any]) -> dict[str, Any]:
    from industrypulse.services.as_published import parse_claim
    from industrypulse.services.monthly_backfill import MonthlyBackfill

    today = date.today()
    with httpx.Client(timeout=40, headers={"User-Agent": "IndustryPulse/local-research"}) as client:
        pipeline = MonthlyBackfill(
            service.repository(),
            fetcher(service, client),
            max_pages=int(options.get("max_pages", 10)),
        )
        announcements = pipeline.discover(
            source_id="cn.nbs",
            kinds=["profit", "pmi", "iva"],
            start=options.get("start", (today - timedelta(days=62)).strftime("%Y-%m")),
            end=options.get("end", today.strftime("%Y-%m")),
        )
        if not announcements:
            return {"discovered": 0, "message": "区间内没有新公告，旧快照保持不变"}
        outcome = pipeline.run(
            announcements,
            source_id="cn.nbs",
            snapshot_id="capture-" + datetime.now(TZ_SHANGHAI).strftime("%Y%m%d%H%M%S"),
            as_published=parse_claim("auto"),
        )
    return {
        "discovered": outcome.discovered,
        "report": outcome.report.model_dump(mode="json"),
        "skipped": list(outcome.skipped),
    }


def discover(service: Workbench, options: dict[str, Any]) -> dict[str, Any]:
    from industrypulse.ai_research.http_provider import HttpResearchProvider
    from industrypulse.ai_research.pipeline import EvidencePipeline, table_candidate
    from industrypulse.ai_research.provider import SearchResult
    from industrypulse.ai_research.review_queue import ReviewQueue
    from industrypulse.ai_research.searchlog import archive_search
    from industrypulse.contracts.observation import EvidenceCandidate, ResearchQuery
    from industrypulse.services.monthly_backfill import MonthlyBackfill, parse_document

    query = ResearchQuery(
        query_id="query-" + uuid4().hex[:12],
        series_id=options["series_id"],
        observation_period=options["period"],
        target_source_host=options.get("domain", "stats.gov.cn"),
        template_version="workbench-v1",
        created_at=datetime.now(TZ_SHANGHAI),
        allowed_domains=[options.get("domain", "stats.gov.cn")],
        max_results=10,
    )
    provider = HttpResearchProvider(
        os.getenv("INDUSTRYPULSE_SEARCH_URL", ""),
        os.getenv("INDUSTRYPULSE_LLM_URL", ""),
        os.getenv("INDUSTRYPULSE_RESEARCH_TOKEN", ""),
    )
    queue = ReviewQueue(service.output / "review.jsonl")
    candidates: list[EvidenceCandidate] = []
    errors = []
    search_text = (
        f"site:{query.target_source_host} {query.series_id} {query.observation_period} 官方公告"
    )
    with httpx.Client(timeout=40) as client:
        downloader = fetcher(service, client)
        pipeline = MonthlyBackfill(service.repository(), downloader, max_pages=6)
        if provider.search_url:
            results = provider.search(query, search_text)
            announcements = []
        else:
            if query.target_source_host != "stats.gov.cn":
                raise ValueError("此域名需要配置搜索接口或手工导入候选")
            announcements = pipeline.discover(
                source_id="cn.nbs",
                kinds=["profit", "pmi", "iva"],
                start=query.observation_period,
                end=query.observation_period,
            )
            results = [
                SearchResult(item.url, f"{item.kind} {item.period}") for item in announcements
            ]
        results = archive_search(
            service.output / "search.jsonl", query, search_text, datetime.now(TZ_SHANGHAI), results
        )
        for result in results:
            try:
                source = next(
                    item
                    for item in service.catalog()["sources"]
                    if query.target_source_host in item["url"]
                )
                artifact = downloader.fetch(source["source_id"], result.url)
                content = Path(artifact.uri).read_bytes()
                artifact = service.repository().archive(artifact, content)
                announcement = next(
                    (item for item in announcements if item.url == result.url), None
                )
                parsed = (
                    parse_document(announcement, content.decode("utf-8")) if announcement else []
                )
                matched = [item for item in parsed if item.series_id == query.series_id]
                if matched:
                    candidates.extend(
                        table_candidate(
                            query,
                            item,
                            artifact,
                            publisher=query.target_source_host,
                            title=result.title,
                        )
                        for item in matched
                    )
                elif provider.can_extract:
                    candidates.append(
                        EvidencePipeline(provider, queue).extract_unstructured(content, artifact)
                    )
            except (ValueError, httpx.HTTPError, PermissionError, StopIteration) as exc:
                errors.append({"url": result.url, "reason": str(exc)})
    reconciled = queue.reconcile(candidates) if candidates else []
    return {
        "query": query.model_dump(mode="json"),
        "results": [asdict(item) for item in results],
        "candidates": [item.model_dump(mode="json") for item in reconciled],
        "errors": errors,
    }


def connector(service: Workbench, options: dict[str, Any]) -> dict[str, Any]:
    from industrypulse.datahub.extended import akshare_smoke, customs_import, fred_observations
    from industrypulse.services.workbench import write_json

    kind = options["kind"]
    if kind == "fred":
        with httpx.Client(timeout=40) as client:
            payload = fred_observations(
                options["series"],
                date.fromisoformat(options["vintage"]),
                os.getenv("FRED_API_KEY", ""),
                client,
            )
        count = len(payload["observations"])
    elif kind == "customs":
        payload = {"rows": customs_import(options["payload"])}
        count = len(payload["rows"])
    elif kind == "akshare":
        payload = {"validation": akshare_smoke(options["payload"]), "rows": options["payload"]}
        count = len(options["payload"])
    else:
        raise ValueError("未知适配器")
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    write_json(service.output / "external" / f"{kind}-{digest}.json", payload)
    return {
        "rows": count,
        "sha256": digest,
        "state": "ARCHIVED",
        "message": "原始数据已归档；通过数据包登记行业、来源和口径后发布",
    }


def configure_schedule(service: Workbench, enabled: bool) -> dict[str, Any]:
    from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore[import-untyped]

    from industrypulse.services.workbench import write_json

    scheduler = getattr(service, "scheduler", None)
    if scheduler is not None:
        scheduler.shutdown(wait=False)
    service.scheduler = None
    if enabled:
        scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        scheduler.add_job(
            lambda: service.launch("backfill", {"max_pages": 3}),
            "cron",
            hour=18,
            minute=10,
            id="capture",
            coalesce=True,
            max_instances=1,
        )

        def monthly() -> None:
            now = datetime.now(TZ_SHANGHAI)
            if (now.date() + timedelta(days=1)).month != now.month:
                service.launch(
                    "forecast",
                    {"as_of": now.isoformat(), "models": ["M00_LAST", "M03_LOGIT", "M04_ELASTIC"]},
                )

        scheduler.add_job(
            monthly, "cron", hour=18, minute=40, id="forecast", coalesce=True, max_instances=1
        )
        from industrypulse.services.research_mvp import scheduled_tick

        scheduler.add_job(
            lambda: scheduled_tick(service),
            "cron",
            hour=19,
            minute=10,
            day_of_week="mon-fri",
            id="research-mvp",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )
        scheduler.start()
        service.scheduler = scheduler
    config = {
        "enabled": enabled,
        "timezone": "Asia/Shanghai",
        "capture": "每日 18:10",
        "forecast": "月末 18:40",
        "research_mvp": "已知交易日 19:10（北京时间）",
        "requires": "本地服务持续运行；重启自动恢复配置",
    }
    write_json(service.output / "schedule.json", config)
    return {**config, "message": "每日采集与月末预测已启用" if enabled else "定时任务已关闭"}
