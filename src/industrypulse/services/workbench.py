"""Composition services for the personal research workbench."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from industrypulse.contracts import (
    TZ_SHANGHAI,
    AnalysisContext,
    FeatureFrame,
    FeatureRow,
    LabeledFeatureFrame,
    PredictionFrame,
    ResearchMode,
)
from industrypulse.economy import EconomyEngine, RobustScaler
from industrypulse.jobs.runner import JobRunner
from industrypulse.repositories import DuckDBMarketRepository, DuckDBObservationRepository
from industrypulse.services.snapshots import ForecastSnapshotStore
from industrypulse.taxonomy import TaxonomyRegistry

ROOT = Path(__file__).resolve().parents[3]
MODEL_NAMES = {
    "M00_LAST": "末值延续",
    "M01_SEASONAL": "季节性",
    "M02_AR": "自回归",
    "M03_LOGIT": "逻辑回归",
    "M04_ELASTIC": "弹性网",
    "M05_FACTOR": "动态因子",
    "M06_LIGHTGBM": "LightGBM",
    "M07_MARKOV": "Markov 状态",
}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    temporary.replace(path)


class Workbench:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.path = Path(os.getenv("INDUSTRYPULSE_DASHBOARD_DB", str(root / "data/g2.duckdb")))
        self.output = root / "artifacts/workbench"
        self.output.mkdir(parents=True, exist_ok=True)
        self.runner = JobRunner(self.output / "jobs.sqlite")
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research")
        self.scheduler: Any = None
        self.ledger_monitor: Any = None
        self.research_monitor: Any = None
        self.news_service: Any = None

    def repository(self, lag: int = 5) -> DuckDBObservationRepository:
        if not self.path.exists():
            raise ValueError("未找到数据库，请先在数据管理中导入数据")
        from industrypulse.core.calendar import load_trading_calendar

        calendar_path = self.root / "data/shfe-calendar-2026.json"
        days = load_trading_calendar(calendar_path).days if calendar_path.exists() else ()
        return DuckDBObservationRepository(self.path, trading_days=days, conservative_lag_days=lag)

    def taxonomy(self, industry: str = "steel-g2") -> TaxonomyRegistry:
        paths = {path.stem: path for path in (self.root / "config/industries").glob("*.yaml")}
        if industry not in paths:
            raise ValueError("行业配置不存在")
        return TaxonomyRegistry.load(paths[industry])

    def snapshot(self, repository: DuckDBObservationRepository, requested: str = "") -> str:
        with repository.database.transaction() as connection:
            rows = connection.execute(
                "SELECT p.snapshot_id, count(r.series_id) AS n FROM publication p "
                "LEFT JOIN publication_row r USING(snapshot_id) GROUP BY p.snapshot_id "
                "ORDER BY n DESC, p.snapshot_id DESC"
            ).fetchall()
        if requested and requested not in {row[0] for row in rows}:
            raise ValueError("快照不存在")
        if not rows:
            raise ValueError("数据库无已发布快照")
        return requested or str(rows[0][0])

    def catalog(self) -> dict[str, Any]:
        repository = self.repository()
        with repository.database.transaction() as connection:
            series = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT payload FROM catalog WHERE kind='series' ORDER BY id"
                ).fetchall()
            ]
            sources = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT payload FROM catalog WHERE kind='source' ORDER BY id"
                ).fetchall()
            ]
            snapshots = connection.execute(
                "SELECT snapshot_id, count(*) FROM publication_row "
                "GROUP BY snapshot_id ORDER BY count(*) DESC"
            ).fetchall()
            coverage = connection.execute(
                "SELECT series_id, count(*), min(observation_start), max(observation_start) "
                "FROM observation_vintage GROUP BY series_id"
            ).fetchall()
            stats = {
                table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in (
                    "observation_vintage",
                    "raw_artifact",
                    "market_bar",
                    "corporate_action",
                    "ancestry",
                )
            }
        by_series = {
            row[0]: {"versions": row[1], "start": str(row[2]), "end": str(row[3])}
            for row in coverage
        }
        configurations = []
        for path in (self.root / "config/industries").glob("*.yaml"):
            registry = TaxonomyRegistry.load(path)
            configurations.append(
                {
                    "id": path.stem,
                    "name": registry.nodes[0].names.get("zh", path.stem),
                    "industry_id": registry.nodes[0].industry_id,
                    "series": len(registry.series),
                    "synthetic": registry.synthetic,
                }
            )
        return {
            "series": [
                {**item, **by_series.get(item["series_id"], {"versions": 0})} for item in series
            ],
            "sources": sources,
            "snapshots": [{"id": row[0], "rows": row[1]} for row in snapshots],
            "industries": configurations,
            "stats": stats,
            "assets": [
                item.model_dump(mode="json")
                for item in DuckDBMarketRepository(repository.database).assets()
            ],
            "models": MODEL_NAMES,
        }

    def context(
        self, industry: str, mode: ResearchMode, as_of: datetime, snapshot: str = "", lag: int = 5
    ) -> tuple[DuckDBObservationRepository, TaxonomyRegistry, AnalysisContext]:
        repository = self.repository(lag)
        registry = self.taxonomy(industry)
        context = AnalysisContext(
            as_of=as_of,
            research_mode=mode,
            industry_id=registry.nodes[0].industry_id,
            dataset_snapshot_id=self.snapshot(repository, snapshot),
            mapping_version=registry.version,
        )
        return repository, registry, context

    def economy(
        self,
        industry: str,
        mode: ResearchMode,
        as_of: datetime,
        snapshot: str = "",
        lag: int = 5,
        with_history: bool = True,
    ) -> dict[str, Any]:
        repository, registry, context = self.context(industry, mode, as_of, snapshot, lag)
        if not registry.series:
            return {
                "status": "NO_DATA",
                "message": "行业模板已就绪，尚未接入对应真实数据",
                "industry": registry.model_dump(mode="json"),
            }
        rows = repository.fetch(
            [item.series_id for item in registry.series], cast(Any, context)
        ).vintages
        scalers = {}
        from industrypulse.economy.engine import transform

        for indicator in registry.indicators:
            sequence = sorted(
                [
                    row
                    for row in rows
                    if row.series_id in indicator.series_ids and row.value is not None
                ],
                key=lambda row: row.observation_start,
            )
            history = []
            for index, row in enumerate(sequence):
                previous = sequence[index - 1] if index else None
                kind = indicator.transform
                if kind == "yoy":
                    previous = next(
                        (
                            item
                            for item in sequence[:index]
                            if item.observation_start.year == row.observation_start.year - 1
                            and item.observation_start.month == row.observation_start.month
                        ),
                        None,
                    )
                    kind = "percent_change"
                value = transform(
                    float(row.value or 0),
                    float(previous.value) if previous and previous.value is not None else None,
                    kind,
                )
                if value is not None:
                    history.append((row.available_at, value))
            if len(history) >= 3:
                scalers[indicator.indicator_id] = RobustScaler().fit(history, as_of)
        previous_scores = {}
        level_history = []
        if with_history:
            last = as_of.replace(day=1) - timedelta(days=1)
            for offset in range(2):
                at = last if offset == 0 else last.replace(day=1) - timedelta(days=1)
                prior_result = self.economy(
                    industry, mode, at, context.dataset_snapshot_id, lag, with_history=False
                )
                if prior_result["status"] == "OK":
                    prior = prior_result["report"]
                    if prior["level"] is not None:
                        level_history.append((at, prior["level"]))
                    if offset == 0:
                        previous_scores = {
                            item["indicator_id"]: item["score"]
                            for item in prior["drivers"]
                            if item["score"] is not None
                        }
        report = EconomyEngine(repository).calculate(
            registry.indicators,
            context,
            scalers,
            previous_scores=previous_scores,
            level_history=level_history,
        )
        return {
            "status": "OK",
            "report": report.model_dump(mode="json"),
            "taxonomy": registry.model_dump(mode="json"),
            "visible_rows": len(rows),
        }

    def frames(self, options: dict[str, Any]) -> tuple[Any, Any, Any]:
        from industrypulse.services.research_run import build_frames, make_experiment_spec

        repository, registry, context = self.context(
            options.get("industry", "steel-g2"),
            ResearchMode(options.get("mode", "reconstructed_pit")),
            datetime.fromisoformat(options["as_of"]),
            options.get("snapshot", ""),
            options.get("lag", 5),
        )
        if not registry.series:
            raise ValueError("该行业尚无真实序列，先导入行业数据")
        feature_series = (
            options.get("features")
            or [item.series_id for item in registry.series if "shfe" not in item.series_id][:8]
        )
        label_series = options.get("label_series") or next(
            (
                item.series_id
                for item in registry.series
                if item.series_id.endswith("profit_ytd_yoy")
            ),
            registry.series[0].series_id,
        )
        if options.get("industry", "steel-g2") == "steel-g2" and not options.get("label_series"):
            label_series = "cn.nbs.industry.c31"
        if label_series not in {item.series_id for item in registry.series}:
            raise ValueError("标签序列不属于所选行业")
        built = build_frames(
            repository,
            snapshot_id=context.dataset_snapshot_id,
            industry_id=context.industry_id,
            feature_series=feature_series,
            label_series=label_series,
            start=date.fromisoformat(options.get("start", "2021-10-01")),
            end=context.as_of.date(),
            horizon_months=int(options.get("horizon", 3)),
            research_mode=context.research_mode,
        )
        if options.get("target_kind") == "margin":
            from industrypulse.services.margin_research import build_margin_frames

            built = build_margin_frames(
                repository,
                snapshot_id=context.dataset_snapshot_id,
                industry_id=context.industry_id,
                feature_series=feature_series,
                prefix=next(
                    (
                        item.series_id.removesuffix(".profit_ytd")
                        for item in registry.series
                        if item.series_id.endswith(".profit_ytd")
                    ),
                    f"cn.nbs.industry_c{registry.nodes[0].code}",
                ),
                start=date.fromisoformat(options.get("start", "2021-10-01")),
                end=context.as_of.date(),
                research_mode=context.research_mode,
            )
        elif label_series != "cn.nbs.industry.c31":
            from dataclasses import replace

            target_spec = built.targets.spec.model_copy(
                update={
                    "target_id": "INDICATOR_DIRECTION",
                    "formula": f"1[{label_series}(m+h)>latest_visible_value]",
                    "description": "所选行业指标方向，不等同于 T1 工业增加值目标",
                }
            )
            built = replace(built, targets=built.targets.model_copy(update={"spec": target_spec}))
        train_end = date.fromisoformat(options.get("train_end", "2024-12-31"))
        excluded: set[int] = set()
        for path in (self.output / "holdouts").glob("*.json"):
            if options.get("evaluate_holdout") == path.stem:
                continue
            holdout = json.loads(path.read_text(encoding="utf-8"))
            if holdout.get("industry") != options.get("industry", "steel-g2"):
                continue
            start_day, end_day = (
                date.fromisoformat(holdout["start"]),
                date.fromisoformat(holdout["end"]),
            )
            excluded.update(
                index
                for index, row in enumerate(built.features.rows)
                if start_day <= row.decision_as_of.date() <= end_day
            )
        if excluded:
            from dataclasses import replace

            keep = [index for index in range(len(built.features.rows)) if index not in excluded]
            if not keep:
                raise ValueError("所有样本均位于封存 holdout，禁止用于训练或选参")
            built = replace(
                built,
                features=built.features.model_copy(
                    update={"rows": [built.features.rows[index] for index in keep]}
                ),
                targets=built.targets.model_copy(
                    update={"rows": [built.targets.rows[index] for index in keep]}
                ),
                skipped=built.skipped + len(excluded),
            )
        outer_start = train_end + timedelta(days=1)
        spec = make_experiment_spec(
            experiment_id=options.get("run_id", "run-" + uuid4().hex[:12]),
            features=built.features,
            targets=built.targets,
            train_end=train_end,
            outer_start=outer_start,
            outer_end=context.as_of.date(),
            feature_list=feature_series,
        )
        spec = spec.model_copy(
            update={"pit_mode": context.research_mode, "industry_map_version": registry.version}
        )
        code = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True, text=True, check=False
        ).stdout.strip()
        with repository.database.transaction() as connection:
            dataset_hash = connection.execute(
                "SELECT sha256 FROM publication WHERE snapshot_id=?", [context.dataset_snapshot_id]
            ).fetchone()[0]
        spec = spec.model_copy(
            update={
                "code_sha": code or "unversioned",
                "dependencies_hash": hashlib.sha256(
                    (self.root / "uv.lock").read_bytes()
                ).hexdigest(),
                "dataset_manifest_hash": dataset_hash,
                "notes": f"真实本地数据；horizon={options.get('horizon', 3)}；状态未验证",
            }
        )
        return built, spec, (repository, registry, context)

    def forecast(self, options: dict[str, Any]) -> dict[str, Any]:
        from industrypulse.forecast.registry import create_model

        built, spec, (repository, registry, context) = self.frames(options)
        cutoff = context.as_of - timedelta(seconds=1)
        indices = [
            index
            for index, target in enumerate(built.targets.rows)
            if target.label_available_at and target.label_available_at < cutoff
        ]
        if not indices:
            raise ValueError("当前时点没有已公布训练标签")
        features = built.features.model_copy(
            update={"rows": [built.features.rows[index] for index in indices]}
        )
        train = LabeledFeatureFrame(
            features=features,
            labels=[built.targets.rows[index].value for index in indices],
            label_available_at=[built.targets.rows[index].label_available_at for index in indices],
        )
        selected = repository.select(spec.feature_list, context).selected
        latest = {
            row.series_id: row
            for row in sorted(selected, key=lambda item: (item.observation_start, item.vintage_id))
        }
        values = {
            key: float(latest[key].value)
            if key in latest and latest[key].value is not None
            else None
            for key in spec.feature_list
        }
        live = FeatureFrame(
            industry_id=context.industry_id,
            snapshot_id=built.features.snapshot_id,
            feature_spec_version=built.features.feature_spec_version,
            schema_hash=built.features.schema_hash,
            research_mode=context.research_mode,
            built_at=context.as_of,
            rows=[
                FeatureRow(
                    decision_as_of=context.as_of,
                    industry_id=context.industry_id,
                    features=values,
                    missing_features=[key for key, value in values.items() if value is None],
                    max_ancestor_available_at=max(
                        (row.available_at for row in latest.values()), default=context.as_of
                    ),
                )
            ],
        )
        spec = spec.model_copy(update={"train_cutoff": cutoff})
        predictions, failures = [], []
        artifacts = []
        for model_id in options.get("models", list(MODEL_NAMES)):
            try:
                model = create_model(model_id, {})
                artifact = model.fit(train, spec)
                artifacts.append(artifact.model_dump(mode="json"))
                prediction = model.predict(live, artifact).predictions[0]
                weights = getattr(model.estimator, "coef_", None)
                if weights is not None:
                    weights = abs(weights[0][: len(spec.feature_list)])
                else:
                    weights = getattr(model.estimator, "feature_importances_", None)
                top = (
                    sorted(
                        zip(spec.feature_list, weights[: len(spec.feature_list)], strict=True),
                        key=lambda item: abs(item[1]),
                        reverse=True,
                    )[:5]
                    if weights is not None
                    else []
                )
                prediction = prediction.model_copy(
                    update={
                        "horizon_months": built.targets.spec.horizon_months,
                        "expiry_at": context.as_of + timedelta(days=35),
                        "top_features": [item[0] for item in top],
                        "driver_evidence_ids": [
                            f"{latest[item[0]].series_id}:{latest[item[0]].observation_start}:v{latest[item[0]].vintage_id}"
                            for item in top
                            if item[0] in latest
                        ],
                    }
                )
                predictions.append(prediction)
            except (ValueError, ImportError, RuntimeError) as exc:
                failures.append({"model": model_id, "reason": str(exc)})
        if not predictions:
            raise ValueError("所有模型均未产生预测：" + str(failures))
        frame = PredictionFrame(run_id=spec.experiment_id, predictions=predictions)
        ForecastSnapshotStore(self.output / "forecast.json").publish(frame)
        result = {
            "frame": frame.model_dump(mode="json"),
            "target_spec": built.targets.spec.model_dump(mode="json"),
            "artifacts": artifacts,
            "failures": failures,
            "training_rows": len(indices),
            "snapshot": context.dataset_snapshot_id,
            "latest_input": {
                key: {
                    "period": str(row.observation_start),
                    "age_days": (context.as_of - row.available_at).days,
                }
                for key, row in latest.items()
            },
            "status": "NOT_VALIDATED",
        }
        write_json(self.output / "forecasts" / f"{spec.experiment_id}.json", result)
        return result

    def experiment(self, options: dict[str, Any]) -> dict[str, Any]:
        from industrypulse.forecast.registry import create_model
        from industrypulse.research.tools import report_uncertainty
        from industrypulse.research.validation import run_experiment

        built, spec, _ = self.frames(options)
        grid: dict[str, Any] = {name: {} for name in options.get("models", list(MODEL_NAMES))}
        spec = spec.model_copy(update={"model_grid": grid, "baselines": ["M00_LAST"]})
        if options.get("evaluate_holdout"):
            holdout = json.loads(
                (self.output / "holdouts" / f"{options['evaluate_holdout']}.json").read_text(
                    encoding="utf-8"
                )
            )
            from industrypulse.contracts import SplitSpec

            first = spec.outer_splits[0]
            spec = spec.model_copy(
                update={
                    "inner_splits": [],
                    "outer_splits": [
                        SplitSpec(
                            split_id="final-holdout",
                            kind="holdout",
                            train_start=first.train_start,
                            train_end=date.fromisoformat(holdout["start"]) - timedelta(days=1),
                            test_start=date.fromisoformat(holdout["start"]),
                            test_end=date.fromisoformat(holdout["end"]),
                            purge_months=built.targets.spec.horizon_months,
                            embargo_months=1,
                        )
                    ],
                }
            )
        if options.get("walk_forward", True):
            from industrypulse.contracts import SplitSpec
            from industrypulse.research.validation import add_months

            first = spec.outer_splits[0]
            folds: list[SplitSpec] = []
            test_start = first.test_start
            while test_start <= first.test_end:
                test_end = min(add_months(test_start, 6) - timedelta(days=1), first.test_end)
                folds.append(
                    SplitSpec(
                        split_id=f"walk-{len(folds) + 1}",
                        kind="expanding",
                        train_start=first.train_start,
                        train_end=test_start - timedelta(days=1),
                        test_start=test_start,
                        test_end=test_end,
                        purge_months=built.targets.spec.horizon_months,
                        embargo_months=1,
                    )
                )
                test_start = test_end + timedelta(days=1)
            spec = spec.model_copy(
                update={
                    "outer_splits": folds,
                    "train_cutoff": datetime.fromisoformat(options["as_of"]),
                }
            )
        report = run_experiment(
            built.features,
            built.targets,
            spec,
            self.output / "frozen-runs",
            model_factory=create_model,
        )
        report.comparison["synthetic_validation_only"] = False
        result = {
            "id": spec.experiment_id,
            "manifest": spec.model_dump(mode="json"),
            "comparison": report.comparison,
            "predictions": [item.model_dump(mode="json") for item in report.predictions],
            "failures": list(report.failures),
            "fold_logs": list(report.fold_logs),
            "samples": len(built.features.rows),
            "skipped": built.skipped,
            "uncertainty": report_uncertainty(report.predictions, built.targets),
            "status": "NOT_VALIDATED",
        }
        write_json(self.output / "experiments" / f"{spec.experiment_id}-report.json", result)
        return result

    def final_holdout(self, options: dict[str, Any]) -> dict[str, Any]:
        identifier = Path(options["id"]).stem
        path = self.output / "holdouts" / f"{identifier}.json"
        holdout = json.loads(path.read_text(encoding="utf-8"))
        at = datetime.now(TZ_SHANGHAI)
        if at.date() <= date.fromisoformat(holdout["end"]):
            raise ValueError("最终 holdout 尚未结束，不能提前查看封存指标")
        result = self.experiment(
            {
                **holdout,
                "as_of": at.isoformat(),
                "start": "2021-10-01",
                "train_end": str(date.fromisoformat(holdout["start"]) - timedelta(days=1)),
                "walk_forward": False,
                "evaluate_holdout": identifier,
                "models": holdout.get("models", ["M00_LAST", "M03_LOGIT"]),
            }
        )
        write_json(self.output / "holdout-results" / f"{identifier}.json", result)
        return result

    def backtest(self, options: dict[str, Any]) -> dict[str, Any]:
        from industrypulse.services.backtest_run import BacktestRequest, run_backtest

        repository = self.repository()
        request = BacktestRequest(
            asset_id=options["asset_id"],
            benchmark_asset_id=options.get("benchmark_asset_id"),
            series_id=options.get("series_id", "cn.nbs.industry_c31.profit_ytd_yoy"),
            snapshot_id=self.snapshot(repository, options.get("snapshot", "")),
            start=date.fromisoformat(options.get("start", "2022-01-01")),
            end=date.fromisoformat(options["end"]),
            threshold=float(options.get("threshold", 0)),
            commission_bps=float(options.get("commission_bps", 3)),
            slippage_bps=float(options.get("slippage_bps", 5)),
            max_weight=float(options.get("max_weight", 1)),
        )
        result, metadata = run_backtest(
            DuckDBMarketRepository(repository.database), repository, request
        )
        payload = result.model_dump(mode="json")
        payload["experiment"] = metadata
        payload["status"] = "RESEARCH_ONLY"
        identifier = "backtest-" + uuid4().hex[:12]
        payload["id"] = identifier
        write_json(self.output / "backtests" / f"{identifier}.json", payload)
        return payload

    def reports(self, kind: str) -> list[dict[str, Any]]:
        directories = {
            "experiments": [
                self.root / "artifacts/reports/experiments",
                self.output / "experiments",
            ],
            "backtests": [self.root / "artifacts/reports", self.output / "backtests"],
            "forecasts": [self.output / "forecasts"],
        }
        result = []
        for directory in directories[kind]:
            for path in directory.glob("*.json"):
                if kind == "experiments" and not path.name.endswith("-report.json"):
                    continue
                if kind == "backtests" and not path.name.startswith("backtest-"):
                    continue
                result.append(
                    {
                        "id": path.stem,
                        "kind": kind,
                        "updated_at": datetime.fromtimestamp(
                            path.stat().st_mtime, TZ_SHANGHAI
                        ).isoformat(),
                        "payload": json.loads(path.read_text(encoding="utf-8")),
                    }
                )
        return sorted(result, key=lambda item: item["updated_at"], reverse=True)

    def launch(self, action: str, options: dict[str, Any]) -> dict[str, str]:
        from industrypulse.services.industry_expand import expand_industries
        from industrypulse.services.research_mvp import run as run_mvp
        from industrypulse.services.workbench_ops import backfill

        operations: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "research_mvp": lambda options: run_mvp(self, run_id=options["run_id"]),
            "forecast": self.forecast,
            "experiment": self.experiment,
            "backtest": self.backtest,
            "backup": self.backup,
            "backfill": lambda options: backfill(self, options),
            "expand": lambda options: expand_industries(self, options),
            "holdout": self.final_holdout,
        }
        if action not in operations:
            raise ValueError("未知任务")
        identifier = uuid4().hex

        def execute() -> dict[str, Any]:
            try:
                return operations[action](options)
            except Exception as exc:
                write_json(
                    self.output / "results" / f"{identifier}.json",
                    {"error": type(exc).__name__, "message": str(exc)},
                )
                raise

        def work() -> None:
            status, result = self.runner.run(action, identifier, execute)
            if result is not None:
                write_json(self.output / "results" / f"{identifier}.json", result)
            if status.error and not (self.output / "results" / f"{identifier}.json").exists():
                write_json(self.output / "results" / f"{identifier}.json", {"error": status.error})

        self.pool.submit(work)
        return {"id": identifier, "state": "queued"}

    def jobs(self) -> list[dict[str, Any]]:
        with self.runner.lock:
            rows = self.runner.connection.execute(
                "SELECT key,status FROM jobs ORDER BY rowid DESC LIMIT 50"
            ).fetchall()
        return [
            {
                "id": key,
                **json.loads(payload),
                "result_available": (self.output / "results" / f"{key}.json").exists(),
            }
            for key, payload in rows
        ]

    def backup(self, options: dict[str, Any]) -> dict[str, Any]:
        import shutil

        repository = self.repository()
        target = self.output / "backups" / datetime.now(TZ_SHANGHAI).strftime("%Y%m%d-%H%M%S-%f")
        target.mkdir(parents=True)
        with repository.database.transaction() as connection:
            counts = {
                table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in (
                    "catalog",
                    "observation_vintage",
                    "raw_artifact",
                    "publication",
                    "publication_row",
                    "market_bar",
                    "asset_master",
                    "corporate_action",
                    "ancestry",
                )
            }
            for table in counts:
                connection.execute(
                    f"COPY {table} TO "
                    f"'{(target / (table + '.parquet')).as_posix()}' (FORMAT PARQUET)"
                )
        bronze = self.path.parent / "bronze"
        if bronze.exists():
            shutil.copytree(bronze, target / "bronze")
        hashes = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in target.glob("*.parquet")
        }
        write_json(target / "manifest.json", {"counts": counts, "hashes": hashes})
        return {"backup": target.name, "counts": counts, "hashes": hashes}

    def restore(self, backup: str) -> dict[str, Any]:
        import shutil

        from industrypulse.repositories.database import Database

        target = self.output / "backups" / Path(backup).name
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        restored = self.path.parent / f"restored-{target.name}.duckdb"
        if restored.exists():
            raise ValueError("恢复文件已存在")
        database = Database(restored)
        with database.transaction() as connection:
            for table, digest in manifest["hashes"].items():
                path = target / table
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise ValueError("备份 hash 不一致")
                table_name = path.stem
                connection.execute(
                    f"INSERT INTO {table_name} SELECT * FROM read_parquet(?)", [str(path)]
                )
            counts = {
                table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in manifest["counts"]
            }
            if counts != manifest["counts"]:
                raise ValueError("恢复行数不一致")
        if (target / "bronze").exists():
            shutil.copytree(target / "bronze", self.path.parent / "bronze", dirs_exist_ok=True)
        return {
            "path": str(restored),
            "counts": counts,
            "verified": True,
            "message": "恢复为独立文件；设置 INDUSTRYPULSE_DASHBOARD_DB 后重启可切换",
        }

    def source_inventory(self) -> list[dict[str, Any]]:
        catalog = self.catalog()
        with (self.root / "source_catalog.csv").open(encoding="utf-8-sig", newline="") as stream:
            candidates = list(csv.DictReader(stream))
        return [
            {
                **row,
                "registered": [
                    item for item in catalog["sources"] if row["url"].split("/")[2] in item["url"]
                ],
                "coverage": [
                    item
                    for item in catalog["series"]
                    if any(
                        item["source_id"] == source["source_id"]
                        for source in catalog["sources"]
                        if row["url"].split("/")[2] in source["url"]
                    )
                ],
            }
            for row in candidates
        ]
