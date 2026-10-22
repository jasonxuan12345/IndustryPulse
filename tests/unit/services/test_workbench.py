from __future__ import annotations

import json
from pathlib import Path
from time import monotonic, sleep

import pytest

from industrypulse.repositories.database import Database
from industrypulse.services.workbench import Workbench


def test_backup_restores_complete_tables_and_detects_tampering(tmp_path: Path):
    path = tmp_path / "data/g2.duckdb"
    database = Database(path)
    with database.transaction() as connection:
        connection.execute("INSERT INTO ancestry VALUES ('example','[]')")
    service = Workbench(tmp_path)
    backup = service.backup({})
    restored = service.restore(backup["backup"])
    assert restored["verified"] and restored["counts"]["ancestry"] == 1
    manifest = service.output / "backups" / backup["backup"] / "manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["hashes"]["catalog.parquet"] = "broken"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    other = service.output / "backups" / "tampered"
    import shutil

    shutil.copytree(manifest.parent, other)
    with pytest.raises(ValueError, match="hash"):
        service.restore("tampered")
    service.pool.shutdown()
    service.runner.close()


def test_background_failures_keep_actionable_error(tmp_path: Path):
    service = Workbench(tmp_path)
    job = service.launch("forecast", {"as_of": "2026-10-22T17:00:00+08:00"})
    result = service.output / "results" / f"{job['id']}.json"
    deadline = monotonic() + 5
    while not result.exists() and monotonic() < deadline:
        sleep(0.02)
    payload = json.loads(result.read_text(encoding="utf-8"))
    assert payload["error"] == "ValueError"
    assert "数据库" in payload["message"]
    service.pool.shutdown()
    assert service.jobs()[0]["state"] == "failed"
    service.runner.close()


def test_workbench_api_validation_export_and_future_holdout(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import industrypulse.api.workbench as api
    from industrypulse.api import create_app

    Database(tmp_path / "data/g2.duckdb")
    service = Workbench(tmp_path)
    monkeypatch.setattr(api, "workspace", lambda: service)
    client = TestClient(create_app())
    assert client.post("/api/workbench/run", json={"action": "unknown"}).status_code == 422
    exported = client.get("/api/workbench/export")
    assert exported.status_code == 200 and "market_bar" in exported.json()["market"]
    payload = {
        "id": "final-test",
        "start": "2099-01-01",
        "end": "2099-12-31",
        "industry": "steel-g2",
    }
    assert client.post("/api/workbench/holdout", json=payload).status_code == 200
    assert (
        client.post("/api/workbench/holdout", json={**payload, "end": "2099-06-01"}).status_code
        == 422
    )
    service.pool.shutdown()
    service.runner.close()
