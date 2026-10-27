"""前瞻账本的本地查询、追加与监控入口。"""

from __future__ import annotations

import json
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from industrypulse.api.workbench import workspace
from industrypulse.services import ledger
from industrypulse.services import ledger_runtime as runtime


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    subject: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1, max_length=4000)
    asset: str = Field(pattern=r"^ETF_[0-9]{6}$")
    horizon_months: Literal[3, 6]
    evidence: list[str] = Field(min_length=1)
    invalidation_conditions: list[str] = Field(min_length=1)


class Mark(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["withdrawn", "confirmed", "refuted"]
    reason: str = Field(min_length=1)
    evidence: str = ""
    event_at: str = ""


class MonitorSettings(BaseModel):
    enabled: bool


class Update(BaseModel):
    refresh_prices: bool = False


def create_ledger_router() -> APIRouter:
    router = APIRouter()

    @router.get("/ledger", include_in_schema=False)
    def page() -> FileResponse:
        return FileResponse(workspace().root / "apps/dashboard/web/ledger.html")

    @router.get("/api/ledger/report")
    def report() -> dict[str, Any]:
        service = workspace()
        directory = service.root / "data/ledger"
        try:
            result = ledger.weekly_report(
                path=directory / "recommendations.jsonl",
                tool_journal=service.root / "data/research/tool-journal.jsonl",
            )
            health = directory / "health.json"
            result.update(
                settings=runtime.settings(service),
                health=json.loads(health.read_text(encoding="utf-8")) if health.exists() else None,
            )
            return result
        except ledger.LedgerIntegrityError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/api/ledger/history/{identifier}")
    def history(identifier: str) -> dict[str, Any]:
        records = [
            r
            for r in ledger.load_ledger(workspace().root / "data/ledger/recommendations.jsonl")
            if r["recommendation_id"] == identifier
        ]
        if not records:
            raise HTTPException(404, "记录不存在")
        return {"records": records}

    @router.post("/api/ledger/update")
    def update(payload: Update) -> dict[str, Any]:
        return runtime.run(workspace(), refresh=payload.refresh_prices)

    @router.post("/api/ledger/settings")
    def settings(payload: MonitorSettings) -> dict[str, Any]:
        return runtime.configure(workspace(), payload.enabled)

    @router.post("/api/ledger/candidates")
    def add_candidate(payload: Candidate) -> dict[str, Any]:
        from industrypulse.repositories import DuckDBMarketRepository

        service = workspace()
        market = DuckDBMarketRepository(service.path)
        try:
            asset = market.asset(payload.asset)
            if asset.instrument_type.value != "ETF":
                raise ValueError("必须是已登记的ETF")
            return ledger.register(
                source_kind="manual",
                category="asset_candidate",
                path=service.root / "data/ledger/recommendations.jsonl",
                **payload.model_dump(),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/api/ledger/mark/{identifier}")
    def mark(identifier: str, payload: Mark) -> dict[str, Any]:
        try:
            return ledger.mark(
                identifier,
                path=workspace().root / "data/ledger/recommendations.jsonl",
                **payload.model_dump(),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
