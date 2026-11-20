"""量化位置雷达、硬异动聚合与四象限视图接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from industrypulse.ai.butterfly import load_latest_report
from industrypulse.analytics.position import compute_position_radar
from industrypulse.api.dashboard import market_service
from industrypulse.intelligence.service import NewsService
from industrypulse.signals.anomalies import SignalService
from industrypulse.smart_money.service import SmartMoneyService


def collect_sector_radars(
    news_service: NewsService,
    signal_service: SignalService | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """收集 12 行业当前新闻、硬异动缓存与量化位置/高危雷达；停用行情时优雅降级。"""
    feed = news_service.feed()
    stories = list(feed.get("stories") or [])
    sig_svc = signal_service or SignalService()
    anomalies = sig_svc.load_cached()
    by_sector = dict(anomalies.get("by_sector") or {})

    market = market_service()
    if market is not None:
        radars = market.position_radars(stories, anomalies_by_sector=by_sector)
        enriched_radars = [
            {
                **r,
                "hard_signals": (by_sector.get(str(r["id"])) or {}).get("hard_signals") or [],
                "commodity_warnings": (by_sector.get(str(r["id"])) or {}).get("commodity_warnings")
                or [],
                "twse_pulse": (by_sector.get(str(r["id"])) or {}).get("twse_pulse"),
                "overseas_proxies": (by_sector.get(str(r["id"])) or {}).get("overseas_proxies")
                or [],
            }
            for r in radars
        ]
        return enriched_radars, stories, anomalies

    radars_fallback: list[dict[str, Any]] = []
    for domain in news_service.catalog.domains:
        domain_stories = [s for s in stories if s.get("sector") == domain.id]
        anom = by_sector.get(domain.id) or {}
        radar = compute_position_radar(
            closes=[],
            news_count_24h=len(domain_stories),
            hard_signal_count=int(anom.get("hard_signal_count") or 0),
            overseas_lead_20d=anom.get("overseas_lead_20d"),
            overseas_avg_20d=anom.get("overseas_avg_20d"),
            commodity_warnings=anom.get("commodity_warnings") or None,
        )
        radars_fallback.append(
            {
                "id": domain.id,
                "name": domain.name,
                "etf": None,
                "companies": [],
                "change": None,
                "net_flow": None,
                "heat": None,
                "news_count_24h": len(domain_stories),
                "hard_signals": anom.get("hard_signals") or [],
                "commodity_warnings": anom.get("commodity_warnings") or [],
                "twse_pulse": anom.get("twse_pulse"),
                "overseas_proxies": anom.get("overseas_proxies") or [],
                **radar,
            }
        )
    return radars_fallback, stories, anomalies


def create_radar_router(
    news_service: NewsService,
    smart_money: SmartMoneyService | None = None,
    signal_service: SignalService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/radar", tags=["quant-radar"])
    sm_service = smart_money or SmartMoneyService()
    sig_service = signal_service or SignalService()

    @router.get("/overview")
    def overview() -> dict[str, Any]:
        radars, _, anomalies = collect_sector_radars(news_service, sig_service)
        sm_summary = sm_service.sector_summary()
        enriched = [{**item, "smart_money": sm_summary.get(str(item["id"]), {})} for item in radars]
        return {
            "sectors": enriched,
            "lurking": [r for r in enriched if r.get("zone") == "LURKING"],
            "falling": [r for r in enriched if r.get("zone") == "FALLING"],
            "high_risk": [r for r in enriched if r.get("zone") in {"HIGH_RISK", "CROWDED"}],
            "anomalies": {
                "updated_at": anomalies.get("updated_at"),
                "channels": anomalies.get("channels") or {},
                "highlights": anomalies.get("highlights") or [],
            },
            "latest_ai_report": load_latest_report(),
        }

    @router.post("/signals/refresh")
    def refresh_signals() -> dict[str, Any]:
        radars, _, _ = collect_sector_radars(news_service, sig_service)
        a_rets = {str(r["id"]): r.get("return_20d") for r in radars}
        commodities: list[dict[str, Any]] | None = None
        market = market_service()
        if market is not None:
            try:
                overview, _ = market._overview_snapshot()
                commodities = list((overview or {}).get("commodities") or []) or None
            except Exception:  # noqa: BLE001
                commodities = None
        return sig_service.refresh(a_share_returns_20d=a_rets, commodities=commodities)

    return router
