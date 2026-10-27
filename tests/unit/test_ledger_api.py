from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from industrypulse.api import ledger as ledger_api
from industrypulse.api.app import create_app
from industrypulse.contracts import TZ_SHANGHAI, AssetMaster
from industrypulse.repositories import DuckDBMarketRepository
from industrypulse.services.workbench import Workbench


@pytest.fixture
def client(tmp_path, monkeypatch):
    service = Workbench(tmp_path)
    market = DuckDBMarketRepository(service.path)
    market.register_asset(
        AssetMaster(
            asset_id="ETF_159611",
            exchange="SZSE",
            instrument_type="ETF",
            name_zh="电力ETF",
            list_at=datetime(2020, 1, 1, tzinfo=TZ_SHANGHAI),
        )
    )
    monkeypatch.setattr(ledger_api, "workspace", lambda: service)
    with TestClient(create_app()) as client:
        yield client, service
    service.pool.shutdown()
    service.runner.close()


def test_api_registers_without_backdating_and_history_keeps_original(client):
    api, _ = client
    payload = {
        "source_id": "manual-one",
        "subject": "电力",
        "statement": "未来需求改善",
        "asset": "ETF_159611",
        "horizon_months": 3,
        "evidence": ["https://www.stats.gov.cn/original"],
        "invalidation_conditions": ["利润同比下行"],
    }
    result = api.post("/api/ledger/candidates", json=payload)
    assert result.status_code == 200
    record = result.json()["record"]
    assert record["source_at"] is None and record["model_status"] == "NOT_VALIDATED"
    assert api.post("/api/ledger/candidates", json=payload).json()["created"] is False
    assert (
        api.post("/api/ledger/candidates", json={**payload, "created_at": "2020-01-01"}).status_code
        == 422
    )
    identifier = record["recommendation_id"]
    mark = api.post(
        "/api/ledger/mark/" + identifier, json={"action": "withdrawn", "reason": "条件已变化"}
    )
    assert mark.status_code == 200
    history = api.get("/api/ledger/history/" + identifier).json()["records"]
    assert len(history) == 2 and history[0]["statement"] == history[1]["statement"]
    assert api.get("/api/ledger/report").json()["asset_evaluation"]["false_positive_rate"] is None


def test_api_reports_integrity_failure_instead_of_dropping_rows(client):
    api, service = client
    path = service.root / "data/ledger/recommendations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"partial":', encoding="utf-8")
    response = api.get("/api/ledger/report")
    assert response.status_code == 409
