"""把 15 条冻结路由接到真实服务（U-009 / §14.2）。

原则：**能接的接真数据，接不了的就明确报不可用并说明原因**——不返回空骨架冒充成功。
每个响应都携带真实的 ``data_quality`` 与 ``snapshot_id``，不再写死 "unbound"。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Request

from industrypulse.api.app import ApiFailure
from industrypulse.contracts import (
    ApiResponse,
    ApiWarning,
    DataQuality,
    ErrorCode,
    ResearchMode,
)
from industrypulse.economy import EconomyEngine, RobustScaler
from industrypulse.repositories import DuckDBObservationRepository, PublicationError
from industrypulse.taxonomy import TaxonomyRegistry

Handler = Callable[[Request, datetime, ResearchMode], Any]


class _Clock:
    def __init__(self, as_of: datetime, mode: ResearchMode, snapshot_id: str | None) -> None:
        self.as_of = as_of
        self.research_mode = mode
        self.dataset_snapshot_id = snapshot_id


def _response(
    *,
    as_of: datetime,
    mode: ResearchMode,
    snapshot_id: str,
    result: Any,
    coverage: float = 0.0,
    verified_ratio: float = 0.0,
    warnings: list[ApiWarning] | None = None,
) -> ApiResponse[Any]:
    return ApiResponse[Any](
        request_id=uuid4().hex,
        as_of=as_of,
        research_mode=mode,
        snapshot_id=snapshot_id,
        data_quality=DataQuality(coverage=coverage, vintage_verified_ratio=verified_ratio),
        result=result,
        warnings=warnings or [],
    )


def build_handlers(
    *,
    observations: DuckDBObservationRepository,
    taxonomy: TaxonomyRegistry,
    snapshot_id: str,
    report_dir: Path | None = None,
    job_status_provider: Callable[[], list[dict[str, Any]]] | None = None,
) -> dict[str, Handler]:
    """构造 path -> handler 映射；未覆盖的路由由 app 返回结构化不可用。"""
    engine = EconomyEngine(observations)

    def industries(request: Request, as_of: datetime, mode: ResearchMode) -> ApiResponse[Any]:
        _ = request
        nodes = [node.model_dump(mode="json") for node in taxonomy.nodes]
        return _response(
            as_of=as_of,
            mode=mode,
            snapshot_id=snapshot_id,
            result=nodes,
            coverage=1.0 if nodes else 0.0,
        )

    def industry_state(request: Request, as_of: datetime, mode: ResearchMode) -> ApiResponse[Any]:
        industry_id = str(request.path_params.get("id", ""))
        clock = _Clock(as_of, mode, snapshot_id)
        specs = [spec for spec in taxonomy.indicators if industry_id in spec.industry_ids]
        if not specs:
            raise ApiFailure(ErrorCode.INDUSTRY_MAPPING_MISSING, f"行业 {industry_id} 无指标映射")
        series = sorted({sid for spec in specs for sid in spec.series_ids})
        rows = observations.fetch(series, clock).vintages
        scalers: dict[str, RobustScaler] = {}
        for spec in specs:
            history = [
                (row.available_at, float(row.value))
                for row in rows
                if row.series_id in spec.series_ids and row.value is not None
            ]
            if len(history) >= 3:
                scalers[spec.indicator_id] = RobustScaler().fit(history, cutoff=as_of)
        from industrypulse.contracts import AnalysisContext

        context = AnalysisContext(
            as_of=as_of,
            research_mode=mode,
            industry_id=industry_id,
            dataset_snapshot_id=snapshot_id,
            mapping_version=taxonomy.version,
        )
        report = engine.calculate(specs, context, scalers)
        warnings = [
            ApiWarning(
                code="PARTIAL_MODEL",
                message=(
                    f"仅 {len(scalers)}/{len(specs)} 个指标具备足够历史用于稳健标准化；"
                    "缺失维度显示为 null 而非 0"
                ),
            )
        ]
        if mode == ResearchMode.EXPLORATORY:
            warnings.append(ApiWarning(code="NOT_PIT_VERIFIED", message="探索模式：非 PIT 验证"))
        return _response(
            as_of=as_of,
            mode=mode,
            snapshot_id=snapshot_id,
            result=report.model_dump(mode="json"),
            coverage=report.data_coverage,
            warnings=warnings,
        )

    def industry_series(request: Request, as_of: datetime, mode: ResearchMode) -> ApiResponse[Any]:
        industry_id = str(request.path_params.get("id", ""))
        clock = _Clock(as_of, mode, snapshot_id)
        specs = [spec for spec in taxonomy.indicators if industry_id in spec.industry_ids]
        if not specs:
            raise ApiFailure(ErrorCode.INDUSTRY_MAPPING_MISSING, f"行业 {industry_id} 无指标映射")
        series = sorted({sid for spec in specs for sid in spec.series_ids})
        rows = observations.fetch(series, clock).vintages
        latest: dict[str, Any] = {}
        for row in rows:
            current = latest.get(row.series_id)
            if current is None or (row.observation_start, row.vintage_id) > (
                current["observation_start"],
                current["vintage_id"],
            ):
                latest[row.series_id] = {
                    "observation_start": row.observation_start,
                    "vintage_id": row.vintage_id,
                    "value": None if row.value is None else str(row.value),
                    "unit": row.unit,
                    "available_at": row.available_at.isoformat(),
                    "release_evidence": row.release_evidence.value,
                    "value_evidence": row.value_evidence.value,
                    "source_url": row.source_url,
                }
        payload = [
            {
                "indicator_id": spec.indicator_id,
                "dimension": spec.dimension.value,
                "direction": spec.economic_direction.value,
                "role": spec.role.value,
                "series_ids": list(spec.series_ids),
                "latest": [latest[sid] for sid in spec.series_ids if sid in latest],
            }
            for spec in specs
        ]
        covered = sum(1 for item in payload if item["latest"])
        return _response(
            as_of=as_of,
            mode=mode,
            snapshot_id=snapshot_id,
            result=payload,
            coverage=covered / len(payload) if payload else 0.0,
        )

    def series_history(request: Request, as_of: datetime, mode: ResearchMode) -> ApiResponse[Any]:
        series_id = str(request.path_params.get("id", ""))
        clock = _Clock(as_of, mode, snapshot_id)
        try:
            frame = observations.fetch([series_id], clock)
        except PublicationError as exc:
            raise ApiFailure(ErrorCode.SERIES_NOT_FOUND, str(exc)) from exc
        except ValueError as exc:
            raise ApiFailure(ErrorCode.PIT_NOT_ELIGIBLE, str(exc)) from exc
        verified = (
            sum(1 for row in frame.vintages if row.value_evidence.value in {"A", "B"})
            / len(frame.vintages)
            if frame.vintages
            else 0.0
        )
        return _response(
            as_of=as_of,
            mode=mode,
            snapshot_id=snapshot_id,
            result=frame.model_dump(mode="json"),
            coverage=1.0 if frame.vintages else 0.0,
            verified_ratio=verified,
        )

    def sources(request: Request, as_of: datetime, mode: ResearchMode) -> ApiResponse[Any]:
        _ = request
        with observations.database.transaction() as conn:
            rows = conn.execute(
                "SELECT payload FROM catalog WHERE kind='source' ORDER BY id"
            ).fetchall()
        descriptors = [json.loads(row[0]) for row in rows]
        return _response(
            as_of=as_of,
            mode=mode,
            snapshot_id=snapshot_id,
            result=descriptors,
            coverage=1.0 if descriptors else 0.0,
        )

    def backtest(request: Request, as_of: datetime, mode: ResearchMode) -> ApiResponse[Any]:
        backtest_id = str(request.path_params.get("id", ""))
        if report_dir is None:
            raise ApiFailure(ErrorCode.SOURCE_UNAVAILABLE, "未配置回测报告目录")
        path = report_dir / f"{backtest_id}.json"
        if not path.is_file():
            raise ApiFailure(ErrorCode.SOURCE_UNAVAILABLE, f"回测报告不存在：{backtest_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return _response(
            as_of=as_of,
            mode=mode,
            snapshot_id=snapshot_id,
            result=payload,
            coverage=1.0,
        )

    def jobs(request: Request, as_of: datetime, mode: ResearchMode) -> ApiResponse[Any]:
        _ = request
        payload = job_status_provider() if job_status_provider else []
        return _response(
            as_of=as_of,
            mode=mode,
            snapshot_id=snapshot_id,
            result=payload,
            coverage=1.0 if payload else 0.0,
            warnings=[
                ApiWarning(
                    code="NO_SCHEDULER",
                    message="未安装系统定时任务；此列表来自本地任务库"
                    if payload
                    else "本地任务库为空",
                )
            ],
        )

    return {
        "/v1/industries": industries,
        "/v1/industries/{id}/state": industry_state,
        "/v1/industries/{id}/series": industry_series,
        "/v1/series/{id}/history": series_history,
        "/v1/sources": sources,
        "/v1/backtests/{id}": backtest,
        "/v1/jobs": jobs,
    }


__all__ = ["Handler", "build_handlers"]
