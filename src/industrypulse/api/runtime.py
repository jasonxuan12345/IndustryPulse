"""Local application with real legacy routes and research dashboard enabled."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import Request

from industrypulse.api.app import ApiFailure, create_app
from industrypulse.api.workbench import workspace
from industrypulse.contracts import ApiResponse, DataQuality, ErrorCode, ResearchMode
from industrypulse.services.api_handlers import build_handlers


def bindings() -> dict[str, Any]:
    service = workspace()
    if not service.path.exists():
        return {}
    repository = service.repository()
    snapshot = service.snapshot(repository)
    handlers = build_handlers(
        observations=repository,
        taxonomy=service.taxonomy(),
        snapshot_id=snapshot,
        report_dir=service.root / "artifacts/reports",
        job_status_provider=service.jobs,
    )

    def respond(at: datetime, mode: ResearchMode, payload: Any) -> ApiResponse[Any]:
        return ApiResponse[Any](
            request_id=uuid4().hex,
            as_of=at,
            research_mode=mode,
            snapshot_id=snapshot,
            data_quality=DataQuality(coverage=0, vintage_verified_ratio=0),
            result=payload,
        )

    def experiments(request: Request, at: datetime, mode: ResearchMode) -> ApiResponse[Any]:
        _ = request
        return respond(at, mode, service.reports("experiments"))

    def experiment(request: Request, at: datetime, mode: ResearchMode) -> ApiResponse[Any]:
        found = next(
            (
                item
                for item in service.reports("experiments")
                if item["id"] == request.path_params["id"]
            ),
            None,
        )
        if found is None:
            raise ApiFailure(ErrorCode.SOURCE_UNAVAILABLE, "实验报告不存在")
        return respond(at, mode, found["payload"])

    def forecast(request: Request, at: datetime, mode: ResearchMode) -> ApiResponse[Any]:
        reports = service.reports("forecasts")
        predictions = [
            prediction
            for report in reports
            for prediction in report["payload"]["frame"]["predictions"]
            if prediction["industry_id"] == request.path_params["id"]
            and prediction["research_mode"] == mode.value
            and datetime.fromisoformat(prediction["as_of"]) <= at
        ]
        if not predictions:
            raise ApiFailure(
                ErrorCode.INSUFFICIENT_HISTORY, "此模式与时点尚无预测，请先在模型预测页运行"
            )
        latest = max(predictions, key=lambda item: item["as_of"])["as_of"]
        return respond(
            at,
            mode,
            {
                "predictions": [item for item in predictions if item["as_of"] == latest],
                "prediction_as_of": latest,
                "stale": datetime.fromisoformat(latest).date() != at.date(),
            },
        )

    def evidence(request: Request, at: datetime, mode: ResearchMode) -> ApiResponse[Any]:
        path = service.output / "review.candidates.jsonl"
        candidates = (
            [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            if path.exists()
            else []
        )
        found = next(
            (
                item
                for item in reversed(candidates)
                if item["candidate_id"] == request.path_params["id"]
            ),
            None,
        )
        if found is None:
            raise ApiFailure(ErrorCode.EVIDENCE_UNVERIFIED, "候选不存在")
        return respond(at, mode, found)

    def opportunities(request: Request, at: datetime, mode: ResearchMode) -> ApiResponse[Any]:
        _ = request
        economy = service.economy("steel-g2", mode, at)
        return respond(
            at,
            mode,
            {
                "watchlist": [
                    {
                        "industry": "CN_NBS_C31",
                        "priority": "OBSERVE",
                        "economy": economy,
                        "model_status": "NOT_VALIDATED",
                        "pricing": "市场定价与经济信号独立，不给买卖推荐",
                    }
                ]
            },
        )

    handlers.update(
        {
            "/v1/experiments": experiments,
            "/v1/experiments/{id}": experiment,
            "/v1/industries/{id}/forecast": forecast,
            "/v1/evidence/{id}": evidence,
            "/v1/investment/opportunities": opportunities,
        }
    )
    return handlers


app = create_app(bindings())


@app.on_event("startup")
def start_schedule() -> None:
    from industrypulse.services.workbench_ops import configure_schedule

    service = workspace()
    path = service.output / "schedule.json"
    if path.exists() and json.loads(path.read_text(encoding="utf-8")).get("enabled"):
        configure_schedule(service, True)


@app.on_event("shutdown")
def stop_schedule() -> None:
    service = workspace()
    if service.scheduler is not None:
        service.scheduler.shutdown(wait=False)
    service.pool.shutdown(wait=False)
    service.runner.close()
