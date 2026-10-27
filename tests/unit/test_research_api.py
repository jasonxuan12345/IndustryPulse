from __future__ import annotations

from fastapi.testclient import TestClient

from industrypulse.api.app import create_app
from industrypulse.services import ledger
from industrypulse.services import research_loop as loop
from industrypulse.services.workbench import Workbench, write_json


def test_research_api_enforces_timestamp_boundary_and_checks_frozen_input_hash(
    tmp_path, monkeypatch
):
    from industrypulse.api import research_loop as api

    service = Workbench(tmp_path)
    monkeypatch.setattr(api, "workspace", lambda: service)
    client = TestClient(create_app())
    assert client.post("/api/research/run", json={"as_of": "2020-01-01"}).status_code == 422
    assert client.get("/api/research/events/data-invalid").status_code == 422
    event_id = "data-" + "a" * 24
    inputs = {"data": "real frozen input"}
    sha = ledger._hash(inputs)
    directory = tmp_path / "data/research-loop"
    write_json(directory / f"events/{event_id}.json", {"input_sha256": sha})
    write_json(directory / f"inputs/{sha}.json", inputs)
    assert client.get("/api/research/events/" + event_id).status_code == 200
    write_json(directory / f"inputs/{sha}.json", {"data": "changed"})
    assert client.get("/api/research/events/" + event_id).status_code == 409
    response = client.post("/api/research/settings", json={"enabled": True, "ai_enabled": False})
    assert response.status_code == 200 and loop.settings(service)["ai_enabled"] is False
    service.pool.shutdown()
    service.runner.close()
