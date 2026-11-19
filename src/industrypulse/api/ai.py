"""AI 蝴蝶效应推演、随手投喂交叉核验与判断回看接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from industrypulse.ai.butterfly import load_latest_report, run_butterfly_analysis
from industrypulse.ai.history import JudgmentHistory
from industrypulse.ai.verify import verify_lead
from industrypulse.api.dashboard import market_service
from industrypulse.api.radar import collect_sector_radars
from industrypulse.intelligence.service import NewsService
from industrypulse.smart_money.service import SmartMoneyService


class ButterflyRunRequest(BaseModel):
    use_ai: bool = True
    refresh_news: bool = False


class VerifyLeadRequest(BaseModel):
    text: str = Field(min_length=5, max_length=20000)
    sector: str | None = None


def _sector_latest_prices(
    radars: list[dict[str, Any]],
) -> dict[str, tuple[float | None, str | None]]:
    market = market_service()
    if market is None:
        return {}
    prices: dict[str, tuple[float | None, str | None]] = {}
    for item in radars:
        sid = str(item["id"])
        cfg = market.sectors_config["domains"].get(sid)
        if not cfg:
            continue
        bars = market.cached_bars(str(cfg["board"]), limit=1) or market.cached_bars(
            str(cfg["etfs"]["primary"]), limit=1
        )
        if bars:
            prices[sid] = (float(bars[-1]["close"]), str(bars[-1]["date"]))
    return prices


def create_ai_router(
    news_service: NewsService,
    smart_money: SmartMoneyService | None = None,
    history: JudgmentHistory | None = None,
) -> APIRouter:
    sm = smart_money or SmartMoneyService()
    journal = history or JudgmentHistory()
    router = APIRouter(prefix="/api/ai", tags=["ai-butterfly"])

    @router.get("/butterfly/latest")
    def latest_butterfly() -> dict[str, Any]:
        report = load_latest_report()
        if report is None:
            return {"status": "empty", "message": "尚未运行过 AI 蝴蝶效应分析，请点击运行"}
        return report

    @router.post("/butterfly/run")
    def run_butterfly(body: ButterflyRunRequest) -> dict[str, Any]:
        if body.refresh_news:
            news_service.refresh(force=True)
        radars, stories = collect_sector_radars(news_service)
        market = market_service()
        macro_items: list[dict[str, Any]] = []
        commodities: list[dict[str, Any]] = []
        if market is not None:
            try:
                macro_items = list(market.macro().get("items") or [])
            except Exception:  # noqa: BLE001
                macro_items = []
            try:
                overview, _ = market._overview_snapshot()
                commodities = list((overview or {}).get("commodities") or [])
            except Exception:  # noqa: BLE001
                commodities = []

        report = run_butterfly_analysis(
            radars=radars,
            stories=stories,
            macro_items=macro_items,
            commodities=commodities,
            smart_money_summary=sm.sector_summary(),
            use_ai=body.use_ai,
        )
        radar_map = {str(r["id"]): r for r in radars}
        journal.record_run(report, radar_map, _sector_latest_prices(radars))
        return report

    @router.post("/verify")
    def verify(body: VerifyLeadRequest) -> dict[str, Any]:
        radars, stories = collect_sector_radars(news_service)
        try:
            return verify_lead(
                text=body.text,
                sector=body.sector,
                radars=radars,
                stories=stories,
                smart_money_summary=sm.sector_summary(),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/history")
    def list_history() -> dict[str, Any]:
        radars, _ = collect_sector_radars(news_service)
        items = journal.list_with_outcomes(_sector_latest_prices(radars))
        return {"items": items, "count": len(items)}

    return router
