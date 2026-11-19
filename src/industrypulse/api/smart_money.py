"""国内外知名投资机构与投资人趋势接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from industrypulse.smart_money.service import SmartMoneyEntryInput, SmartMoneyService


def create_smart_money_router(service: SmartMoneyService | None = None) -> APIRouter:
    sm = service or SmartMoneyService()
    router = APIRouter(prefix="/api/smart-money", tags=["smart-money"])

    @router.get("")
    def overview() -> dict[str, Any]:
        return sm.overview()

    @router.post("/entries")
    def add_entry(payload: SmartMoneyEntryInput) -> dict[str, Any]:
        try:
            return sm.add_entry(payload)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.delete("/entries/{entry_id}")
    def delete_entry(entry_id: str) -> dict[str, bool]:
        if not sm.delete_entry(entry_id):
            raise HTTPException(404, "未找到该机构记录")
        return {"deleted": True}

    @router.post("/sync")
    def sync_filings() -> dict[str, Any]:
        return sm.sync_sec_filings()

    return router
