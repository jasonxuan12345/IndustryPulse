"""Synthetic frozen snapshot -> economy -> model -> OOS report acceptance."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from industrypulse.api import create_app
from industrypulse.contracts import (
    ROUTES,
    TZ_SHANGHAI,
    AnalysisContext,
    ApiResponse,
    ApprovalState,
    DataQuality,
    ExperimentSpec,
    FeatureFrame,
    FeatureRow,
    ObservationVintage,
    RawArtifact,
    ResearchMode,
    SourceDescriptor,
    SplitSpec,
    TargetFrame,
    TargetRow,
)
from industrypulse.core import Settings
from industrypulse.economy import RobustScaler
from industrypulse.features import target_specs
from industrypulse.repositories import DuckDBObservationRepository
from industrypulse.services import PipelineService
from industrypulse.taxonomy import load_steel_taxonomy

ROOT = Path(__file__).resolve().parents[2]


def test_frozen_fixture_repository_economy_and_api(tmp_path: Path) -> None:
    registry = load_steel_taxonomy()
    repository = DuckDBObservationRepository(tmp_path / "economy.duckdb")
    source = SourceDescriptor(
        source_id="cn.nbs",
        owner="S",
        url="https://synthetic.example.test",
        data_fields=["profit_ytd"],
        frequency="M",
        revision_behavior="revised_later",
        legal_status="public_official",
        priority="S0",
        notes="Synthetic fixture only; not an acquisition permission",
    )
    repository.register_source(source)
    repository.register_series(registry.series[0])
    repository.register_taxonomy(registry.nodes, registry.indicators)
    path = ROOT / "tests/fixtures/g0/steel_profit_vintages.json"
    content = path.read_bytes()
    at = datetime(2025, 4, 30, 17, tzinfo=TZ_SHANGHAI)
    artifact = RawArtifact(
        artifact_id="fixture-raw",
        source_id=source.source_id,
        uri="fixture",
        sha256=hashlib.sha256(content).hexdigest(),
        mime="application/json",
        byte_size=len(content),
        retrieved_at=at,
    )
    repository.archive(artifact, content)
    rows = [
        ObservationVintage.model_validate(row).model_copy(
            update={"artifact_id": artifact.artifact_id}
        )
        for row in json.loads(content)["vintages"]
    ]
    # AI candidate-linked rows require separate reviewed raw artifacts (G2 A-012).
    rows = [row for row in rows if row.evidence_candidate_id is None]
    repository.publish("g1-frozen", rows)
    ctx = AnalysisContext(
        as_of=at,
        research_mode=ResearchMode.STRICT_PIT,
        industry_id="CN_NBS_C31",
        dataset_snapshot_id="g1-frozen",
        mapping_version=registry.version,
    )
    # Fit uses only two pre-decision released rows, preserving exact training reference.
    scalers = {
        "steel_profit_ytd": RobustScaler().fit(
            [(r.available_at, float(r.value)) for r in rows[:2]], rows[1].available_at
        )
    }
    service = PipelineService(repository)
    report = service.observe(registry.indicators, ctx, scalers)
    assert report.snapshot_id == "g1-frozen"
    assert report.drivers[0].ancestor_ids
    assert len(report.dimensions) == 6 and report.dimensions["inventory"].level is None
    response = ApiResponse(
        request_id="integration",
        as_of=at,
        research_mode=ctx.research_mode,
        snapshot_id=ctx.dataset_snapshot_id,
        data_quality=DataQuality(coverage=report.data_coverage, vintage_verified_ratio=1),
        result=report,
    )
    app = create_app({"/v1/industries/{id}/state": lambda request, clock, mode: response})
    with TestClient(app) as client:
        result = client.get("/v1/industries/CN_NBS_C31/state", params={"as_of": at.isoformat()})
        assert (
            result.status_code == 200 and result.json()["result"]["industry_id"] == ctx.industry_id
        )
    repository.database.close()


def test_service_runs_all_five_models_to_frozen_report(tmp_path: Path) -> None:
    rows, labels = [], []
    for year in range(2010, 2021):
        for month in range(1, 13):
            decision = datetime(year, month, 28, 17, tzinfo=TZ_SHANGHAI)
            offset = year * 12 + month - 1 + 3
            ty, tm = divmod(offset, 12)
            target_at = datetime(ty, tm + 1, 28, 17, tzinfo=TZ_SHANGHAI)
            rows.append(
                FeatureRow(
                    industry_id="CN_NBS_C31",
                    decision_as_of=decision,
                    features={"x": float(month % 2)},
                    max_ancestor_available_at=decision,
                )
            )
            labels.append(
                TargetRow(
                    industry_id="CN_NBS_C31",
                    decision_as_of=decision,
                    target_period=target_at.strftime("%Y-%m"),
                    value=float(month % 2),
                    label_available_at=target_at + timedelta(days=5),
                )
            )
    at = datetime(2022, 1, 1, tzinfo=TZ_SHANGHAI)
    frame = FeatureFrame(
        industry_id="CN_NBS_C31",
        snapshot_id="generated-synthetic",
        feature_spec_version="test-1",
        schema_hash="test-1",
        research_mode=ResearchMode.RECONSTRUCTED_PIT,
        built_at=at,
        rows=rows,
    )
    target = TargetFrame(spec=target_specs("CN_NBS_C31")[0], rows=labels)
    split = SplitSpec(
        split_id="outer",
        kind="expanding",
        train_start=date(2010, 1, 1),
        train_end=date(2017, 12, 31),
        test_start=date(2018, 1, 1),
        test_end=date(2020, 12, 31),
        purge_months=3,
        embargo_months=1,
    )
    spec = ExperimentSpec(
        experiment_id="integration-synthetic",
        code_sha="synthetic-test",
        dependencies_hash=hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        dataset_manifest_hash="synthetic-test",
        industry_map_version="1",
        pit_mode=frame.research_mode,
        train_cutoff=datetime(2018, 1, 1, tzinfo=TZ_SHANGHAI),
        outer_splits=[split],
        feature_list=["x"],
        model_grid={
            m: {} for m in ["M00_LAST", "M01_SEASONAL", "M02_AR", "M03_LOGIT", "M04_ELASTIC"]
        },
        random_seed=1,
        target_definition="T1_IVA_DIRECTION@1",
        baselines=["M00_LAST"],
        primary_metric="brier",
        approval_state=ApprovalState.FROZEN,
        created_at=at,
    )
    repository = DuckDBObservationRepository(tmp_path / "models.duckdb")
    result = PipelineService(repository).experiment(frame, target, spec, tmp_path / "reports")
    assert len(result.predictions) == 36 * 5 and not result.failures
    assert result.comparison["status"] == "NO_EDGE" and result.comparison["common_oos_count"] == 36
    repository.database.close()


def test_api_route_inventory_errors_and_discovery_gate() -> None:
    app = create_app(settings=Settings())
    registered = {
        (method, route.path) for route in app.routes for method in getattr(route, "methods", [])
    }
    assert {(route.method, route.path) for route in ROUTES} <= registered
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        disabled = client.post("/v1/research/discover", json={})
        assert disabled.status_code == 503 and disabled.json()["code"] == "SOURCE_UNAVAILABLE"
        invalid = client.get(
            "/v1/industries/CN_NBS_C31/state", params={"as_of": "2025-01-01T17:00:00"}
        )
        assert invalid.status_code == 422 and invalid.json()["code"] == "PIT_NOT_ELIGIBLE"
