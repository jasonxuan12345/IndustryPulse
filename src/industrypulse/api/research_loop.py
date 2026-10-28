"""Local event research, baseline evidence and conditional opportunity endpoints."""

import threading
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from industrypulse.api.workbench import workspace
from industrypulse.services import research_loop, research_view, trusted_baseline


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_sources: bool = False
    use_ai: bool = True


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    ai_enabled: bool = True


def create_research_router() -> APIRouter:
    router = APIRouter()
    submit_lock = threading.Lock()
    active: dict[str, str] = {}

    @router.get("/research", include_in_schema=False)
    def page() -> FileResponse:
        return FileResponse(workspace().root / "apps/dashboard/web/research.html")

    @router.get("/research/quant", include_in_schema=False)
    def quant_page() -> FileResponse:
        return FileResponse(workspace().root / "apps/dashboard/web/research-quant.html")

    @router.get("/api/research/mvp/report")
    def mvp_report(run_id: str = "") -> dict[str, Any]:
        try:
            return research_view.report(workspace().root, run_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise HTTPException(409, "研究归档无法通过完整性检查，请核对文件或备份") from exc

    @router.post("/api/research/mvp/run", status_code=202)
    def mvp_run() -> dict[str, str]:
        service = workspace()
        with submit_lock:
            previous = active.get("id")
            if previous and not (service.output / "results" / f"{previous}.json").exists():
                return {"id": previous, "state": "queued"}
            result = service.launch("research_mvp", {"run_id": "mvp-ui-" + uuid4().hex})
            active["id"] = result["id"]
            return result

    @router.get("/api/research/mvp/download/{run_id}/{name}")
    def mvp_download(run_id: str, name: str) -> FileResponse:
        if name not in research_view.FILES:
            raise HTTPException(404, "该研究文件不提供下载")
        try:
            research_view.report(workspace().root, run_id)
            path = research_view.directory(workspace().root, run_id) / name
            if not path.is_file():
                raise FileNotFoundError
            return FileResponse(path, filename=name)
        except FileNotFoundError as exc:
            raise HTTPException(404, "研究文件不存在") from exc
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise HTTPException(409, "研究文件无法通过完整性检查") from exc

    @router.get("/api/research/report")
    def report() -> dict[str, Any]:
        service = workspace()
        result = research_loop.read(
            service.root / "data/research-loop/latest.json", {"items": [], "status": "not_run"}
        )
        return {
            **result,
            "settings": research_loop.settings(service),
            "monitor_error": research_loop.read(
                service.root / "data/research-loop/monitor-error.json"
            ),
            "baseline": research_loop.read(service.root / "data/baseline/latest.json"),
        }

    @router.post("/api/research/run")
    def run(payload: RunRequest) -> dict[str, Any]:
        return research_loop.run(
            workspace(), refresh=payload.refresh_sources, use_ai=payload.use_ai
        )

    @router.post("/api/research/settings")
    def configure(payload: Settings) -> dict[str, Any]:
        return research_loop.configure(workspace(), **payload.model_dump())

    @router.get("/api/research/events/{event_id}")
    def event(event_id: str) -> dict[str, Any]:
        if (
            not event_id.startswith("data-")
            or len(event_id) != 29
            or not all(c in "0123456789abcdef" for c in event_id[5:])
        ):
            raise HTTPException(422, "事件ID格式错误")
        service = workspace()
        value = research_loop.read(service.root / "data/research-loop/events" / f"{event_id}.json")
        if value is None:
            raise HTTPException(404, "事件不存在")
        inputs = research_loop.read(
            service.root / "data/research-loop/inputs" / f"{value['input_sha256']}.json"
        )
        from industrypulse.services.ledger import _hash

        if inputs is None or _hash(inputs) != value["input_sha256"]:
            raise HTTPException(409, "冻结输入缺失或指纹不符")
        return {"event": value, "inputs": inputs}

    @router.post("/api/research/baseline")
    def baseline(payload: RunRequest) -> dict[str, Any]:
        return trusted_baseline.run(workspace(), refresh=payload.refresh_sources)

    @router.post("/api/research/revalidate/{event_id}")
    def revalidate(event_id: str) -> dict[str, Any]:
        try:
            return research_loop.revalidate(workspace(), event_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
