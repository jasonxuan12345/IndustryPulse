"""Runnable research, investment and operations endpoints for the dashboard."""

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, Field

from industrypulse.contracts import TZ_SHANGHAI, ResearchMode
from industrypulse.services.workbench import Workbench


@lru_cache(maxsize=1)
def workspace() -> Workbench:
    return Workbench()


class Context(BaseModel):
    industry: str = "steel-g2"
    mode: ResearchMode = ResearchMode.RECONSTRUCTED_PIT
    as_of: AwareDatetime = Field(default_factory=lambda: datetime.now(TZ_SHANGHAI))
    snapshot: str = ""
    lag: int = Field(default=5, ge=0, le=90)


class Question(Context):
    question: str = Field(min_length=1, max_length=800)
    sector: str = Field(default="", max_length=40)


class Operation(BaseModel):
    action: Literal["forecast", "experiment", "backtest", "backup", "expand", "holdout"]
    options: dict[str, Any] = Field(default_factory=dict)


class Decision(BaseModel):
    candidate_id: str
    approved: bool
    reason: str = Field(min_length=1)
    reviewer: str = "local_owner"


def create_workbench_router() -> APIRouter:
    router = APIRouter(prefix="/api/workbench", tags=["research-workbench"])

    @router.get("/catalog")
    def catalog() -> dict[str, Any]:
        try:
            return workspace().catalog()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/economy")
    def economy(context: Context) -> dict[str, Any]:
        try:
            return workspace().economy(**context.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/compare-modes")
    def modes(context: Context) -> dict[str, Any]:
        from industrypulse.services.pit_report import compare_modes

        service = workspace()
        repository, registry, ctx = service.context(**context.model_dump())
        if not registry.series:
            return {"summaries": [], "rows": [], "message": "该行业无真实序列"}
        return compare_modes(
            repository,
            [item.series_id for item in registry.series],
            [context.as_of],
            modes=list(ResearchMode),
            snapshot_id=ctx.dataset_snapshot_id,
        ).model_dump(mode="json")

    @router.post("/state")
    def state(context: Context) -> dict[str, Any]:
        from industrypulse.services.economic_state import build_economic_state

        try:
            return build_economic_state(workspace(), context.mode, context.as_of, context.snapshot)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/ask")
    def ask(body: Question, request: Request) -> dict[str, Any]:
        from industrypulse.ai_research.model_gateway import complete, load_connection
        from industrypulse.intelligence.event_book import EventBook
        from industrypulse.services.economic_state import build_economic_state
        from industrypulse.services.research_ask import (
            SYSTEM,
            guard_answer,
            question_prompt,
            research_packet,
        )

        connection = load_connection()
        if connection is None or not connection.enabled or not connection.model:
            raise HTTPException(422, "请先在设置中启用并保存模型")
        try:
            state = build_economic_state(
                workspace(), body.mode, body.as_of, body.snapshot
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        brief = None
        if body.sector:
            news = getattr(request.app.state, "news_service", None)
            if news is None:
                brief = {
                    "sector": body.sector,
                    "clusters": [],
                    "events": [],
                    "error": "事件简报暂时读不出来",
                }
            else:
                try:
                    stories = list(news.feed().get("stories", []))
                    brief = EventBook(news.directory / "events.json").brief(
                        stories, body.sector, None
                    )
                except (OSError, ValueError, RuntimeError):
                    brief = {
                        "sector": body.sector,
                        "clusters": [],
                        "events": [],
                        "error": "事件简报暂时读不出来",
                    }
        packet = research_packet(state, brief)
        try:
            text = complete(connection, question_prompt(body.question, packet), system=SYSTEM)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        guarded = guard_answer(text)
        return {
            "answer": guarded["answer"],
            "withheld": guarded["withheld"],
            "model": connection.model,
            "packet": packet,
        }

    @router.post("/macro")
    def macro(context: Context) -> dict[str, Any]:
        from types import SimpleNamespace

        service = workspace()
        repository = service.repository(context.lag)
        series = [
            item
            for item in service.catalog()["series"]
            if "macro_proxy" in item.get("tags", [])
            or item["series_id"].startswith(("cn.nbs.pmi", "cn.pbc"))
        ]
        clock = SimpleNamespace(
            as_of=context.as_of,
            research_mode=context.mode,
            dataset_snapshot_id=service.snapshot(repository, context.snapshot),
        )
        rows = (
            repository.fetch([item["series_id"] for item in series], clock).vintages
            if series
            else []
        )
        output = []
        for item in series:
            history = sorted(
                [row for row in rows if row.series_id == item["series_id"]],
                key=lambda row: row.observation_start,
            )
            output.append(
                {
                    **item,
                    "latest": history[-1].model_dump(mode="json") if history else None,
                    "history": [row.model_dump(mode="json") for row in history],
                }
            )
        return {"series": output, "mode": context.mode.value, "as_of": context.as_of.isoformat()}

    @router.post("/run")
    def run(operation: Operation) -> dict[str, str]:
        options = {"as_of": datetime.now(TZ_SHANGHAI).isoformat(), **operation.options}
        return workspace().launch(operation.action, options)

    @router.get("/jobs")
    def jobs() -> list[dict[str, Any]]:
        return workspace().jobs()

    @router.get("/jobs/{identifier}")
    def result(identifier: str) -> dict[str, Any]:
        service = workspace()
        path = service.output / "results" / f"{identifier}.json"
        if path.parent != service.output / "results" or not path.exists():
            return {
                "status": "pending",
                "job": next((item for item in service.jobs() if item["id"] == identifier), None),
            }
        return {"status": "complete", "result": json.loads(path.read_text(encoding="utf-8"))}

    @router.get("/reports/{kind}")
    def reports(kind: Literal["experiments", "backtests", "forecasts"]) -> list[dict[str, Any]]:
        return workspace().reports(kind)

    @router.get("/sources")
    def sources() -> list[dict[str, Any]]:
        return workspace().source_inventory()

    @router.get("/reviews")
    def reviews() -> dict[str, Any]:
        service = workspace()
        repository = service.repository()
        with repository.database.transaction() as connection:
            rows = connection.execute(
                "SELECT payload FROM catalog WHERE kind='candidate'"
            ).fetchall()
        candidates = {item["candidate_id"]: item for item in (json.loads(row[0]) for row in rows)}
        for path in (service.output / "review.candidates.jsonl",):
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    item = json.loads(line)
                    candidates[item["candidate_id"]] = item
        decisions = []
        path = service.output / "review.decisions.jsonl"
        if path.exists():
            decisions = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return {"candidates": list(candidates.values()), "decisions": decisions}

    @router.post("/reviews/import")
    def import_candidates(payload: list[dict[str, Any]]) -> dict[str, int]:
        from industrypulse.ai_research.review_queue import ReviewQueue
        from industrypulse.contracts.observation import EvidenceCandidate

        candidates = [EvidenceCandidate.model_validate(item) for item in payload]
        ReviewQueue(workspace().output / "review.jsonl").reconcile(candidates)
        return {"imported": len(candidates)}

    @router.post("/reviews/decision")
    def decide(decision: Decision) -> dict[str, Any]:
        from industrypulse.ai_research.review_queue import ReviewQueue

        service = workspace()
        candidates = {item["candidate_id"]: item for item in reviews()["candidates"]}
        if decision.candidate_id not in candidates:
            raise HTTPException(404, "候选不存在")
        candidate = candidates[decision.candidate_id]
        if decision.approved:
            repository = service.repository()
            with repository.database.transaction() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM raw_artifact WHERE sha256=?", [candidate.get("raw_sha256")]
                ).fetchone()
            if not exists or candidate.get("raw_text") is None:
                raise HTTPException(422, "缺少已归档原件或原文定位，不能批准；请先导入对应原件")
        ReviewQueue(service.output / "review.jsonl").record_decision(
            **decision.model_dump(), decided_at=datetime.now(TZ_SHANGHAI)
        )
        return {"recorded": True, "approved": decision.approved}

    @router.post("/reviews/publish")
    def publish_review(payload: dict[str, Any]) -> dict[str, Any]:
        from industrypulse.contracts.observation import EvidenceCandidate, ObservationVintage

        service = workspace()
        candidates = {item["candidate_id"]: item for item in reviews()["candidates"]}
        decisions = {item["candidate_id"]: item for item in reviews()["decisions"]}
        identifier = payload["candidate_id"]
        decision = decisions.get(identifier)
        if not decision or not decision["approved"]:
            raise HTTPException(422, "须先批准候选")
        candidate = EvidenceCandidate.model_validate(
            {
                **candidates[identifier],
                "candidate_id": identifier + ":approved",
                "verification_status": "verified",
                "reviewer": decision["reviewer"],
            }
        )
        row = ObservationVintage.model_validate(
            {**payload["observation"], "evidence_candidate_id": candidate.candidate_id}
        )
        repository = service.repository()
        repository.register_candidate(candidate)
        digest = repository.publish(
            payload.get(
                "snapshot_id", "review-" + datetime.now(TZ_SHANGHAI).strftime("%Y%m%d%H%M%S")
            ),
            [row],
        )
        return {"published": True, "sha256": digest}

    @router.get("/backups")
    def backups() -> list[dict[str, Any]]:
        return [
            {"id": path.parent.name, **json.loads(path.read_text(encoding="utf-8"))}
            for path in sorted(
                (workspace().output / "backups").glob("*/manifest.json"), reverse=True
            )
        ]

    @router.post("/restore/{identifier}")
    def restore(identifier: str) -> dict[str, Any]:
        return workspace().restore(identifier)

    @router.post("/holdout")
    def holdout(payload: dict[str, Any]) -> dict[str, str]:
        from industrypulse.research.tools import seal_holdout

        identifier = str(payload.get("id", "final-holdout"))
        if not identifier.replace("-", "").replace("_", "").isalnum():
            raise HTTPException(422, "实验标识格式错误")
        from datetime import date

        if not (
            datetime.now(TZ_SHANGHAI).date()
            < date.fromisoformat(payload["start"])
            <= date.fromisoformat(payload["end"])
        ):
            raise HTTPException(422, "新封存窗口必须从未来开始，且结束日不能早于起始日")
        digest = seal_holdout(workspace().output / "holdouts" / f"{identifier}.json", payload)
        return {"sha256": digest, "status": "sealed"}

    @router.post("/connector")
    def connector(payload: dict[str, Any]) -> dict[str, Any]:
        from industrypulse.services.workbench_ops import connector as execute

        try:
            return execute(workspace(), payload)
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/discover")
    def discover(payload: dict[str, Any]) -> dict[str, Any]:
        from industrypulse.services.workbench_ops import discover as execute

        try:
            return execute(workspace(), payload)
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/backfill")
    def backfill(payload: dict[str, Any]) -> dict[str, str]:
        return workspace().launch("backfill", payload)

    @router.get("/schedule")
    def schedule() -> dict[str, Any]:
        path = workspace().output / "schedule.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"enabled": False}

    @router.post("/schedule")
    def update_schedule(payload: dict[str, Any]) -> dict[str, Any]:
        from industrypulse.services.workbench_ops import configure_schedule

        return configure_schedule(workspace(), bool(payload.get("enabled")))

    @router.post("/taxonomy")
    def taxonomy(payload: dict[str, Any]) -> dict[str, Any]:
        from industrypulse.services.workbench import write_json
        from industrypulse.taxonomy import TaxonomyRegistry

        registry = TaxonomyRegistry.model_validate(payload)
        service = workspace()
        identifier = registry.nodes[0].industry_id.lower()
        write_json(service.root / "config/industries" / f"{identifier}.yaml", payload)
        repository = service.repository()
        for series in registry.series:
            repository.register_series(series)
        repository.register_taxonomy(registry.nodes, registry.indicators)
        return {"id": identifier, "series": len(registry.series)}

    @router.get("/gold-audit")
    def gold_audit() -> dict[str, Any]:
        from industrypulse.services.audit_packet import prepare

        return prepare(workspace())

    @router.post("/gold-audit")
    def evaluate_gold(payload: dict[str, Any]) -> dict[str, Any]:
        from industrypulse.services.audit_packet import evaluate
        from industrypulse.services.workbench import write_json

        result = evaluate(payload)
        write_json(workspace().output / "gold-audit-result.json", result)
        return result

    @router.post("/research-tools")
    def research_tools(payload: dict[str, Any]) -> dict[str, Any]:
        from industrypulse.research.tools import block_bootstrap, calibrate, lead_lag

        kind = payload["kind"]
        if kind == "monitoring":
            from industrypulse.economy.monitoring import turning_points

            return {
                "events": turning_points(payload["values"], payload.get("threshold", 0.5)),
                "causal": True,
            }
        if kind == "margin_proxy":
            from industrypulse.economy.monitoring import margin_proxy

            return {
                "value": margin_proxy(
                    payload.get("steel_price"), payload.get("ore_price"), payload.get("coke_price")
                ),
                "label": "价格-原料价差代理，不是绝对钢厂毛利",
            }
        if kind == "bootstrap":
            return block_bootstrap(payload["values"], block=payload.get("block", 3))
        if kind == "lead_lag":
            if payload["train_end"] >= payload["test_start"]:
                raise HTTPException(422, "领先滞后分析只能传入训练窗")
            return {"rows": lead_lag(payload["values"], payload["target"])}
        if kind == "calibration":
            model = calibrate(
                payload["probabilities"],
                payload["labels"],
                calibration_end=payload["calibration_end"],
                prediction_start=payload["prediction_start"],
            )
            result = {
                "x": model.X_thresholds_.tolist(),
                "y": model.y_thresholds_.tolist(),
                "calibration_end": payload["calibration_end"],
                "prediction_start": payload["prediction_start"],
                "model_id": payload["model_id"],
                "status": "CALIBRATION_FITTED",
            }
            from industrypulse.services.workbench import write_json

            write_json(workspace().output / "calibration.json", result)
            return result
        raise HTTPException(422, "未知研究工具")

    @router.get("/performance")
    def performance() -> dict[str, Any]:
        path = workspace().output / "performance.json"
        return (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status": "not_run"}
        )

    @router.get("/vintages")
    def vintages(series_id: str) -> list[dict[str, Any]]:
        repository = workspace().repository()
        with repository.database.transaction() as connection:
            rows = connection.execute(
                "SELECT payload FROM observation_vintage WHERE series_id=? "
                "ORDER BY observation_start,vintage_id",
                [series_id],
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    @router.get("/export")
    def export() -> dict[str, Any]:
        import base64

        repository = workspace().repository()
        with repository.database.transaction() as connection:
            catalog_rows = connection.execute("SELECT kind,payload FROM catalog").fetchall()
            raw = [
                json.loads(row[0])
                for row in connection.execute("SELECT payload FROM raw_artifact").fetchall()
            ]
            rows = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT payload FROM observation_vintage "
                    "ORDER BY series_id,observation_start,vintage_id"
                ).fetchall()
            ]
            market = {
                table: [
                    json.loads(row[0])
                    for row in connection.execute(f"SELECT payload FROM {table}").fetchall()
                ]
                for table in ("asset_master", "market_bar", "corporate_action")
            }
        return {
            "catalog": [
                {"kind": kind, "payload": json.loads(payload)} for kind, payload in catalog_rows
            ],
            "raw": [
                {
                    "artifact": item,
                    "base64": base64.b64encode(Path(item["uri"]).read_bytes()).decode(),
                }
                for item in raw
            ],
            "observations": rows,
            "market": market,
        }

    @router.post("/import")
    def import_data(payload: dict[str, Any]) -> dict[str, Any]:
        import base64

        from industrypulse.contracts import (
            EvidenceCandidate,
            IndicatorSpec,
            IndustryNode,
            ObservationVintage,
            RawArtifact,
            SeriesSpec,
            SourceDescriptor,
        )

        repository = workspace().repository()
        models: dict[str, type[BaseModel]] = {
            "source": SourceDescriptor,
            "series": SeriesSpec,
            "industry": IndustryNode,
            "indicator": IndicatorSpec,
            "candidate": EvidenceCandidate,
        }
        records = payload["catalog"]
        for kind in models:
            for item in records:
                if item["kind"] != kind:
                    continue
                model = models[kind].model_validate(item["payload"])
                with repository.database.transaction() as connection:
                    repository._put(
                        connection,
                        kind,
                        item["payload"].get(
                            {
                                "source": "source_id",
                                "series": "series_id",
                                "industry": "industry_id",
                                "indicator": "indicator_id",
                                "candidate": "candidate_id",
                            }[kind]
                        ),
                        model,
                    )
        for item in payload["raw"]:
            repository.archive(
                RawArtifact.model_validate(item["artifact"]), base64.b64decode(item["base64"])
            )
        rows = [ObservationVintage.model_validate(item) for item in payload["observations"]]
        digest = repository.publish(
            "import-" + datetime.now(TZ_SHANGHAI).strftime("%Y%m%d%H%M%S%f"), rows
        )
        if payload.get("market"):
            from industrypulse.contracts import AssetMaster, CorporateAction, MarketBar
            from industrypulse.repositories import DuckDBMarketRepository

            market = DuckDBMarketRepository(repository.database)
            for asset in payload["market"].get("asset_master", []):
                market.register_asset(AssetMaster.model_validate(asset))
            bars = [
                MarketBar.model_validate(item) for item in payload["market"].get("market_bar", [])
            ]
            if bars:
                market.publish_bars(bars)
            for item in payload["market"].get("corporate_action", []):
                market.register_action(CorporateAction.model_validate(item))
        return {"rows": len(rows), "sha256": digest}

    return router
