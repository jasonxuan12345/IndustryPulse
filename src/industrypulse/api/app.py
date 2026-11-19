"""Clean FastAPI application wiring intelligence, market, quant radar, smart money, and AI."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from industrypulse.api.ai import create_ai_router
from industrypulse.api.dashboard import ROOT, create_dashboard_router
from industrypulse.api.events import create_events_router
from industrypulse.api.model_settings import create_model_router
from industrypulse.api.news_sources import create_sources_router
from industrypulse.api.radar import create_radar_router
from industrypulse.api.smart_money import create_smart_money_router
from industrypulse.core import Settings
from industrypulse.intelligence.service import NewsService
from industrypulse.smart_money.service import SmartMoneyService


def create_app(settings: Settings | None = None, *, root: Path | None = None) -> FastAPI:
    _ = settings
    news_service = NewsService()
    smart_money_service = SmartMoneyService()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> Any:
        try:
            yield
        finally:
            news_service.close()

    application = FastAPI(title="IndustryPulse", version="2.0.0", lifespan=lifespan)
    application.state.news_service = news_service
    application.state.smart_money_service = smart_money_service
    application.include_router(create_dashboard_router(news_service))
    application.include_router(create_sources_router(news_service))
    application.include_router(create_events_router(news_service))
    application.include_router(create_model_router())
    application.include_router(create_radar_router(news_service, smart_money_service))
    application.include_router(create_smart_money_router(smart_money_service))
    application.include_router(create_ai_router(news_service, smart_money_service))

    @application.middleware("http")
    async def protect_local_writes(request: Request, call_next: Any) -> Any:
        host = (request.url.hostname or "").lower().rstrip(".")
        test_transport = (
            host == "testserver"
            and request.scope.get("server", (None,))[0] == "testserver"
            and request.client is not None
            and request.client.host == "testclient"
        )
        if host not in {"127.0.0.1", "localhost", "::1"} and not test_transport:
            return JSONResponse(
                status_code=403, content={"detail": "请通过127.0.0.1或localhost访问本机工作台"}
            )
        if request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD"}:
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                return JSONResponse(
                    status_code=403, content={"detail": "请从本机工作台页面执行此操作"}
                )
        return await call_next(request)

    @application.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    web_root = (root or ROOT) / "apps/dashboard/web"
    if web_root.is_dir():
        application.mount(
            "/dashboard-assets", StaticFiles(directory=web_root), name="dashboard-assets"
        )

        @application.get("/", include_in_schema=False)
        def dashboard() -> FileResponse:
            return FileResponse(web_root / "index.html")

    return application


app = create_app()
