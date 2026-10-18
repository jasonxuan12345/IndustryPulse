"""Contract-backed SDK with explicit offline mocks and structured error handling."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from industrypulse.contracts import (
    ROUTES,
    TZ_SHANGHAI,
    ApiResponse,
    ApiWarning,
    DataQuality,
    ErrorCode,
    ErrorPayload,
    ObservationVintage,
    ResearchMode,
    select_vintages_as_of,
)

ROOT = Path(__file__).resolve().parents[2]


class ApiClientError(Exception):
    def __init__(self, payload: ErrorPayload) -> None:
        self.payload = payload
        super().__init__(payload.message)


class DashboardClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        root: Path = ROOT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.root = root
        self.base_url = base_url
        self.http = httpx.Client(
            base_url=base_url or "https://offline.invalid",
            transport=transport,
            timeout=10,
            follow_redirects=False,
        )
        self.openapi = json.loads(
            (root / "contracts/openapi/openapi.json").read_text(encoding="utf-8")
        )

    def close(self) -> None:
        self.http.close()

    def get(self, path: str, *, as_of: datetime, mode: ResearchMode) -> ApiResponse[Any]:
        if as_of.tzinfo is None:
            raise ValueError("Timezone required")
        route = next(
            (
                r
                for r in ROUTES
                if r.method == "GET" and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", r.path), path)
            ),
            None,
        )
        if route is None or route.path not in self.openapi["paths"]:
            raise ValueError("Unknown OpenAPI GET endpoint")
        if self.base_url:
            try:
                response = self.http.get(
                    path, params={"as_of": as_of.isoformat(), "mode": mode.value}
                )
            except httpx.TransportError as exc:
                raise ApiClientError(
                    ErrorPayload.build(
                        request_id=uuid4().hex,
                        code=ErrorCode.SOURCE_UNAVAILABLE,
                        message="后端暂时不可达",
                    )
                ) from exc
            if response.is_error:
                raise ApiClientError(ErrorPayload.model_validate(response.json()))
            return ApiResponse[Any].model_validate(response.json())
        result: Any = [] if route.result_kind.endswith("[]") else {}
        if path == "/v1/industries":
            result = self._fixture("steel_taxonomy")["nodes"]
        elif path == "/v1/sources":
            result = []
        elif "/history" in path:
            result = self._history(as_of, mode, path.split("/")[3])
        elif path.endswith("/state"):
            example = json.loads(
                (self.root / "contracts/openapi/examples/industry_state.json").read_text(
                    encoding="utf-8"
                )
            )
            result = example["result"]
            result["dimensions"] = {
                dim: {"level": None, "coverage": 0, "missing_indicators": ["未提供此维度合成观测"]}
                for dim in ["demand", "supply", "inventory", "price", "profitability", "liquidity"]
            }
            result.update(
                level=None, momentum=None, breadth=None, drivers=[], state="INSUFFICIENT_HISTORY"
            )
        elif path.endswith("/series"):
            result = self._fixture("steel_taxonomy")["indicators"]
        elif path.endswith("/relations"):
            result = self._fixture("steel_taxonomy")["relations"]
        elif "models" in path or "experiments" in path:
            result = {
                "models": {},
                "status": "NOT_VALIDATED",
                "recommendation": None,
                "reason": "合成数据用于工程验证，尚无真实样本外评估",
            }
        elif route.result_kind == "ObservationFrame":
            result = self._history(as_of, mode)
        warnings = [ApiWarning(code="DEMO_DATA", message="离线合成数据；不代表真实行业或预测结果")]
        if mode == ResearchMode.EXPLORATORY:
            warnings.append(ApiWarning(code="NOT_PIT_VERIFIED", message="探索模式：非 PIT 验证"))
        return ApiResponse[Any](
            request_id=uuid4().hex,
            as_of=as_of,
            research_mode=mode,
            snapshot_id="g0-synthetic-v1",
            data_quality=DataQuality(coverage=0, vintage_verified_ratio=0),
            result=result,
            warnings=warnings,
        )

    def _fixture(self, name: str) -> Any:
        return json.loads(
            (self.root / f"tests/fixtures/g0/{name}.json").read_text(encoding="utf-8")
        )

    def fixture_data(self, name: str) -> Any:
        """Explicit synthetic evidence for offline presentation."""
        return self._fixture(name)

    def _history(
        self, at: datetime, mode: ResearchMode, series_id: str | None = None
    ) -> dict[str, Any]:
        data = self._fixture("steel_profit_vintages")
        vintages = [ObservationVintage.model_validate(row) for row in data["vintages"]]
        calendar = self._fixture("market_calendar")
        from datetime import date

        days = [date.fromisoformat(day) for day in calendar["trading_days"]]
        selection = select_vintages_as_of(vintages, as_of=at, mode=mode, trading_days=days)
        requested = series_id or data["series_id"]
        return {
            "series_ids": [requested],
            "as_of": at.isoformat(),
            "research_mode": mode.value,
            "vintages": [
                v.model_dump(mode="json") for v in selection.selected if v.series_id == requested
            ],
        }


def default_as_of() -> datetime:
    return datetime(2025, 4, 30, 17, tzinfo=TZ_SHANGHAI)
