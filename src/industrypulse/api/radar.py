"""量化位置雷达与四象限视图接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from industrypulse.ai.butterfly import load_latest_report
from industrypulse.analytics.position import compute_position_radar
from industrypulse.api.dashboard import market_service
from industrypulse.intelligence.service import NewsService
from industrypulse.smart_money.service import SmartMoneyService


def collect_sector_radars(
    news_service: NewsService,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """收集 12 行业当前新闻与量化位置/高危雷达；停用行情时优雅降级。"""
    feed = news_service.feed()
    stories = list(feed.get("stories") or [])
    market = market_service()
    if market is not None:
        return market.position_radars(stories), stories

    radars: list[dict[str, Any]] = []
    for domain in news_service.catalog.domains:
        domain_stories = [s for s in stories if s.get("sector") == domain.id]
        radar = compute_position_radar(
            closes=[],
            news_count_24h=len(domain_stories),
        )
        radars.append(
            {
                "id": domain.id,
                "name": domain.name,
                "etf": None,
                "change": None,
                "net_flow": None,
                "heat": None,
                "news_count_24h": len(domain_stories),
                **radar,
            }
        )
    return radars, stories


def create_radar_router(
    news_service: NewsService,
    smart_money: SmartMoneyService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/radar", tags=["quant-radar"])
    sm_service = smart_money or SmartMoneyService()

    @router.get("/overview")
    def overview() -> dict[str, Any]:
        radars, _ = collect_sector_radars(news_service)
        sm_summary = sm_service.sector_summary()
        enriched = [{**item, "smart_money": sm_summary.get(str(item["id"]), {})} for item in radars]
        return {
            "sectors": enriched,
            "lurking": [r for r in enriched if r.get("zone") == "LURKING"],
            "high_risk": [r for r in enriched if r.get("zone") in {"HIGH_RISK", "CROWDED"}],
            "latest_ai_report": load_latest_report(),
        }

    return router
