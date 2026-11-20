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
from industrypulse.signals.anomalies import SignalService
from industrypulse.smart_money.service import SmartMoneyService


class ButterflyRunRequest(BaseModel):
    use_ai: bool = True
    refresh_news: bool = False
    refresh_signals: bool = True


class VerifyLeadRequest(BaseModel):
    text: str = Field(min_length=5, max_length=20000)
    sector: str | None = None


def _sector_latest_prices(
    radars: list[dict[str, Any]],
) -> dict[str, tuple[float | None, str | None]]:
    market = market_service()
    if market is None:
        return {}
    overview, _ = market._overview_snapshot()
    source_by_id = {
        str(item["id"]): item.get("index_source")
        for item in (overview or {}).get("sectors", [])
        if "id" in item
    }
    prices: dict[str, tuple[float | None, str | None]] = {}
    for item in radars:
        sid = str(item["id"])
        if sid not in market.sectors_config["domains"]:
            continue
        pref = source_by_id.get(sid)
        bars = market.latest_sector_bars(sid, preferred_source=str(pref) if pref else None, limit=1)
        if bars:
            prices[sid] = (float(bars[-1]["close"]), str(bars[-1]["date"]))
    return prices


def create_ai_router(
    news_service: NewsService,
    smart_money: SmartMoneyService | None = None,
    history: JudgmentHistory | None = None,
    signal_service: SignalService | None = None,
) -> APIRouter:
    sm = smart_money or SmartMoneyService()
    journal = history or JudgmentHistory()
    sig_svc = signal_service or SignalService()
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
        market = market_service()
        macro_items: list[dict[str, Any]] = []
        commodities: list[dict[str, Any]] = []
        if market is not None:
            try:
                macro_items = list(market.macro().get("items") or [])
                valid_macro = [m for m in macro_items if m.get("value") is not None]
                if len(valid_macro) < 5:
                    market.sync_macro()
                    macro_items = list(market.macro().get("items") or [])
            except Exception:  # noqa: BLE001
                macro_items = []
            try:
                overview, _ = market._overview_snapshot()
                commodities = list((overview or {}).get("commodities") or [])
            except Exception:  # noqa: BLE001
                commodities = []

        radars, stories, _ = collect_sector_radars(news_service, sig_svc)
        if body.refresh_signals:
            try:
                a_rets = {str(r["id"]): r.get("return_20d") for r in radars}
                sig_svc.refresh(a_share_returns_20d=a_rets, commodities=commodities or None)
                radars, stories, _ = collect_sector_radars(news_service, sig_svc)
            except Exception:  # noqa: BLE001 - 硬异动抓取失败不阻断 AI 分析
                pass

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
        radars, stories, _ = collect_sector_radars(news_service, sig_svc)
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
        radars, _, _ = collect_sector_radars(news_service, sig_svc)
        items = journal.list_with_outcomes(_sector_latest_prices(radars))
        return {
            "items": items,
            "count": len(items),
            "summary": JudgmentHistory.summarize(items),
        }

    return router
