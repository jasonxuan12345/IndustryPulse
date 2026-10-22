"""UI configuration, model discovery and connection testing."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from industrypulse.ai_research.model_gateway import (
    ModelConnection,
    complete,
    list_models,
    load_connection,
    public_connection,
    resolve_connection,
    save_connection,
)


class AnalysisRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100000)


def create_model_router() -> APIRouter:
    router = APIRouter(prefix="/api/dashboard/model", tags=["model-settings"])

    @router.get("/settings")
    def settings() -> dict[str, Any]:
        return public_connection(load_connection())

    @router.post("/settings")
    def save(payload: ModelConnection) -> dict[str, Any]:
        try:
            return save_connection(payload)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/models")
    def models(payload: ModelConnection) -> dict[str, Any]:
        try:
            return {
                "models": list_models(payload),
                "base_url": resolve_connection(payload).base_url,
            }
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/test")
    def test(payload: ModelConnection) -> dict[str, Any]:
        try:
            connection = resolve_connection(payload)
            reply = complete(connection, "请只回复 OK。")
            return {"ok": True, "model": connection.model, "reply": reply[:300]}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/analyze")
    def analyze(payload: AnalysisRequest) -> dict[str, str]:
        connection = load_connection()
        if connection is None or not connection.enabled:
            raise HTTPException(422, "请先在设置中配置模型 API")
        try:
            return {"model": connection.model, "text": complete(connection, payload.text)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
