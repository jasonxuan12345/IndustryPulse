from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from industrypulse.intelligence.service import NewsService, NewsSettings


class RefreshSources(BaseModel):
    source_ids: list[str] | None = None


def create_sources_router(service: NewsService) -> APIRouter:
    router = APIRouter(prefix="/api/dashboard/sources", tags=["public-news"])

    @router.get("")
    def catalog() -> dict[str, Any]:
        return service.catalog_payload()

    @router.post("/settings")
    def save(settings: NewsSettings) -> dict[str, Any]:
        try:
            service.save(settings)
        except ValueError as exception:
            raise HTTPException(422, str(exception)) from exception
        return service.catalog_payload()

    @router.post("/refresh")
    def refresh(payload: RefreshSources) -> dict[str, Any]:
        try:
            started = service.refresh(payload.source_ids, force=True)
        except ValueError as exception:
            raise HTTPException(422, str(exception)) from exception
        return {"started": started, **service.status()}

    return router
