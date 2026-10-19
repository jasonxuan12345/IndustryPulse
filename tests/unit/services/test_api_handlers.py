"""API 绑定：真实 handler 返回真实数据，未绑定路由必须结构化不可用。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from industrypulse.api import create_app
from industrypulse.contracts import TZ_SHANGHAI
from industrypulse.repositories import DuckDBObservationRepository
from industrypulse.services import build_handlers
from industrypulse.taxonomy import TaxonomyRegistry

AS_OF = "2026-09-28T17:00:00+08:00"


def _client(tmp_path: Path) -> TestClient:
    repository = DuckDBObservationRepository(tmp_path / "api.duckdb")
    taxonomy = TaxonomyRegistry.load(Path("config/industries/steel-g2.yaml"))
    handlers = build_handlers(
        observations=repository, taxonomy=taxonomy, snapshot_id="test-snapshot"
    )
    return TestClient(create_app(handlers))


def test_industries_returns_real_nodes(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/v1/industries", params={"as_of": AS_OF})
    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_id"] == "test-snapshot"
    assert [node["industry_id"] for node in body["result"]] == ["CN_NBS_C31"]


def test_sources_without_data_is_honest(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/v1/sources", params={"as_of": AS_OF})
    body = response.json()
    assert response.status_code == 200
    assert body["result"] == []
    assert body["data_quality"]["coverage"] == 0.0


def test_unbound_route_is_structured_unavailable(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/v1/experiments", params={"as_of": AS_OF})
    body = response.json()
    assert response.status_code == 503
    assert body["code"] == "SOURCE_UNAVAILABLE"


def test_naive_as_of_is_rejected(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/v1/industries", params={"as_of": "2026-09-28T17:00:00"})
    assert response.status_code == 422
    assert response.json()["code"] == "PIT_NOT_ELIGIBLE"


def test_timestamp_in_response_matches_request(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/v1/industries", params={"as_of": AS_OF})
    body = response.json()
    assert datetime.fromisoformat(body["as_of"]) == datetime.fromisoformat(AS_OF).replace(
        tzinfo=TZ_SHANGHAI
    )
