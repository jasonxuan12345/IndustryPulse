"""AI 蝴蝶效应推演、随手投喂交叉核验与判断回看接口。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from industrypulse.ai.butterfly import load_latest_report, run_butterfly_analysis
from industrypulse.ai.history import JudgmentHistory
from industrypulse.ai.leads import INDUSTRY_END, LeadLedger
from industrypulse.ai.review import (
    ReviewHistory,
    closed_through,
    universe_for_day,
    universe_symbols,
)
from industrypulse.ai.verify import verify_lead
from industrypulse.api.dashboard import market_service, optional_market
from industrypulse.api.radar import collect_sector_radars
from industrypulse.core import TZ_SHANGHAI
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
    market = optional_market(market_service)
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
    reviews = ReviewHistory()
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
        market = optional_market(market_service)
        macro_items: list[dict[str, Any]] = []
        macro_unavailable: list[dict[str, Any]] = []
        commodities: list[dict[str, Any]] = []
        data_status: dict[str, Any] = {
            "request": body.model_dump(),
            "market": "available" if market else "unavailable",
        }
        if market is not None:
            try:
                macro = market.macro()
                macro_items = list(macro.get("indicators") or [])
                macro_unavailable = list(macro.get("unavailable") or [])
                valid_macro = [m for m in macro_items if m.get("value") is not None]
                if len(valid_macro) < 5:
                    market.sync_macro()
                    macro_items = list(market.macro().get("indicators") or [])
            except Exception as exc:  # noqa: BLE001
                data_status["macro_error"] = type(exc).__name__
            try:
                overview, _ = market._overview_snapshot()
                commodities = list((overview or {}).get("commodities") or [])
            except Exception as exc:  # noqa: BLE001
                data_status["commodities_error"] = type(exc).__name__

        radars, stories, signals = collect_sector_radars(news_service, sig_svc)
        data_status["signals_refresh"] = "not_requested"
        if body.refresh_signals:
            try:
                a_rets = {str(r["id"]): r.get("return_20d") for r in radars}
                sig_svc.refresh(a_share_returns_20d=a_rets, commodities=commodities or None)
                radars, stories, signals = collect_sector_radars(news_service, sig_svc)
                data_status["signals_refresh"] = "ok"
            except Exception as exc:  # noqa: BLE001 - 硬异动抓取失败不阻断 AI 分析
                data_status["signals_refresh"] = "failed"
                data_status["signals_error"] = type(exc).__name__

        prices: dict[str, tuple[float | None, str | None]] = {}
        try:
            prices = _sector_latest_prices(radars)
        except Exception as exc:  # noqa: BLE001
            data_status["sector_prices_error"] = type(exc).__name__
        data_status.update(
            macro_count=len(macro_items),
            macro_unavailable=macro_unavailable,
            macro_missing=[
                {key: item.get(key) for key in ("id", "status", "period", "message")}
                for item in macro_items
                if item.get("value") is None
            ],
            commodity_count=len(commodities),
            commodities_missing_price=[
                item.get("id") for item in commodities if item.get("price") is None
            ],
            signals_updated_at=signals.get("updated_at"),
            signal_channels=signals.get("channels", {}),
            sector_prices={
                str(r["id"]): {"close": price, "date": price_date}
                for r in radars
                for price, price_date in [prices.get(str(r["id"]), (None, None))]
            },
        )

        def lead_close(symbol: str) -> tuple[float | None, str | None]:
            if market is not None:
                try:
                    bars = market.cached_bars(symbol, limit=1)
                    if bars:
                        return float(bars[-1]["close"]), str(bars[-1]["date"])
                except Exception:  # noqa: BLE001 - no quote leaves the market track pending
                    pass
            return None, None

        lead_symbols = (
            {sid: str(item["board"]) for sid, item in market.sectors_config["domains"].items()}
            if market
            else {}
        )
        captured_at = datetime.now(TZ_SHANGHAI)
        benchmark = {"symbol": "sh000300", "name": "沪深300"}
        review_symbols = dict(lead_symbols)
        visible = {}
        if market is not None:
            benchmark.update(market.sectors_config.get("benchmarks", {}).get("csi300", {}))
            try:
                overview, _ = market._overview_snapshot()
                preferred = {
                    row["id"]: row.get("index_source")
                    for row in (overview or {}).get("sectors", [])
                }
                for sid in lead_symbols:
                    review_symbols[sid] = market.latest_sector_series(
                        sid, preferred_source=preferred.get(sid)
                    )[0]
            except Exception:  # noqa: BLE001 - fall back to the configured market references
                pass
        universe = universe_for_day(reviews.root, review_symbols, captured_at.date())
        symbols = universe_symbols(universe) | {benchmark["symbol"]}
        if market is not None:
            try:
                end = closed_through(captured_at)
                visible = market.review_closes(symbols, end - timedelta(days=14), end)
            except Exception:  # noqa: BLE001 - no cached bars leaves review prices unavailable
                pass
        report = run_butterfly_analysis(
            radars=radars,
            stories=stories,
            macro_items=macro_items,
            commodities=commodities,
            smart_money_summary=sm.sector_summary(),
            use_ai=body.use_ai,
            data_status=data_status,
            lead_price_lookup=lead_close if market is not None else None,
            lead_sector_symbols=lead_symbols,
        )
        reviews.record_run(
            report,
            universe,
            benchmark,
            visible,
            signals,
            LeadLedger().list(),
            published_at=datetime.now(TZ_SHANGHAI).isoformat(),
            visible_captured_at=captured_at.isoformat(),
        )
        if market is not None:
            market.refresh_review_bars(symbols)
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
        domains = [{"id": domain.id} for domain in news_service.catalog.domains]
        items = journal.list_with_outcomes(_sector_latest_prices(domains))
        return {
            "items": items,
            "count": len(items),
            "summary": JudgmentHistory.summarize(items),
        }

    @router.get("/leads")
    def list_leads() -> dict[str, Any]:
        items = LeadLedger().list()
        return {
            "items": items,
            "count": len(items),
            "active_count": sum(item["industry_status"] not in INDUSTRY_END for item in items),
        }

    @router.get("/review")
    def review(day: str | None = None) -> dict[str, Any]:
        market = optional_market(market_service)
        symbols = reviews.symbols()
        if market is not None and symbols:
            market.refresh_review_bars(symbols)
        return reviews.evaluate(
            market.review_closes if market is not None else lambda *_: {},
            LeadLedger().list(),
            as_of=datetime.now(TZ_SHANGHAI),
            selected=day,
            signals=sig_svc.load_cached(),
        )

    return router
