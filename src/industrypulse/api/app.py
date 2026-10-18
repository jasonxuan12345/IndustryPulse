"""Frozen-route API adapter with dependency injection and structured errors."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from industrypulse.contracts import (
    ROUTES,
    TZ_SHANGHAI,
    ApiResponse,
    ApiWarning,
    DataQuality,
    ErrorCode,
    ErrorPayload,
    ResearchMode,
)
from industrypulse.core import Settings

Handler = Callable[[Request, datetime, ResearchMode], Any]


class ApiFailure(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code, self.message = code, message


def create_app(
    handlers: Mapping[str, Handler] | None = None, settings: Settings | None = None
) -> FastAPI:
    application = FastAPI(title="IndustryPulse", version="1.0.0")
    resolved = settings or Settings.from_env()
    bindings = dict(handlers or {})

    @application.exception_handler(ApiFailure)
    async def failure(request: Request, exc: ApiFailure) -> JSONResponse:
        _ = request
        payload = ErrorPayload.build(request_id=uuid4().hex, code=exc.code, message=exc.message)
        return JSONResponse(
            status_code=payload.http_status, content=payload.model_dump(mode="json")
        )

    @application.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, exc: RequestValidationError) -> JSONResponse:
        _ = exc
        return await failure(request, ApiFailure(ErrorCode.CONTRACT_MISMATCH, "请求参数与合同不符"))

    def endpoint_for(path: str) -> Callable[..., Any]:
        def endpoint(
            request: Request,
            as_of: datetime | None = None,
            mode: ResearchMode = ResearchMode.STRICT_PIT,
        ) -> JSONResponse:
            at = as_of or datetime.now(TZ_SHANGHAI)
            if at.tzinfo is None:
                raise ApiFailure(ErrorCode.PIT_NOT_ELIGIBLE, "as_of 必须包含时区")
            if request.method == "POST" and (
                not resolved.discovery_enabled or resolved.api_key is None
            ):
                raise ApiFailure(ErrorCode.SOURCE_UNAVAILABLE, "检索服务未配置或未启用")
            handler = bindings.get(path)
            if handler is None:
                if path == "/healthz":
                    result: Any = {"status": "ok", "discovery_enabled": resolved.discovery_enabled}
                else:
                    raise ApiFailure(ErrorCode.SOURCE_UNAVAILABLE, "此业务端点尚未绑定数据服务")
            else:
                result = handler(request, at, mode)
            # Strongly typed lane reports may supply the complete response envelope.
            if isinstance(result, ApiResponse):
                if result.as_of != at or result.research_mode != mode:
                    raise ApiFailure(ErrorCode.CONTRACT_MISMATCH, "响应研究时点或模式不一致")
                response = result
            else:
                warnings = []
                if mode == ResearchMode.EXPLORATORY:
                    warnings.append(
                        ApiWarning(code="NOT_PIT_VERIFIED", message="探索模式：非 PIT 验证")
                    )
                response = ApiResponse[Any](
                    request_id=uuid4().hex,
                    as_of=at,
                    research_mode=mode,
                    snapshot_id="unbound",
                    data_quality=DataQuality(coverage=0, vintage_verified_ratio=0),
                    result=result,
                    warnings=warnings,
                )
            return JSONResponse(content=jsonable_encoder(response))

        return endpoint

    for route in ROUTES:
        application.add_api_route(
            route.path,
            endpoint_for(route.path),
            methods=[route.method],
            name=f"{route.method} {route.path}",
        )
    return application


app = create_app()
