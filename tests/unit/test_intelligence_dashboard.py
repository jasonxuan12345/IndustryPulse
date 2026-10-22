from __future__ import annotations

from fastapi.testclient import TestClient

from industrypulse.api.app import create_app
from industrypulse.api.dashboard import demo_feed


def test_feed_import_roundtrip_and_validation(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("INDUSTRYPULSE_FEED_URL", raising=False)
    monkeypatch.setenv("INDUSTRYPULSE_FEED_FILE", str(tmp_path / "feed.json"))
    client = TestClient(create_app())
    assert client.get("/").status_code == 200
    assert client.get("/api/dashboard/feed").json()["mode"] == "demo"
    payload = demo_feed().model_dump(mode="json")
    payload.update(mode="live", source="test provider")
    assert client.post("/api/dashboard/feed", json=payload).status_code == 200
    assert client.get("/api/dashboard/feed").json() == payload
    assert client.post("/api/dashboard/feed", json={"mode": "live"}).status_code == 422
    assert client.get("/api/dashboard/feed").json() == payload
    (tmp_path / "feed.json").write_text("broken", encoding="utf-8")
    assert client.get("/api/dashboard/feed").status_code == 502


def test_remote_failure_does_not_fall_back_to_demo(tmp_path, monkeypatch) -> None:
    import httpx

    original_get = httpx.Client.get

    def fail(self, url, **kwargs):
        if str(url).startswith("https://provider.example"):
            raise httpx.ConnectError("offline")
        return original_get(self, url, **kwargs)

    monkeypatch.setenv("INDUSTRYPULSE_FEED_URL", "https://provider.example/feed")
    monkeypatch.setenv("INDUSTRYPULSE_FEED_FILE", str(tmp_path / "feed.json"))
    monkeypatch.setattr(httpx.Client, "get", fail)
    with TestClient(create_app()) as client:
        assert client.get("/api/dashboard/feed").status_code == 502
        assert (
            client.post("/api/dashboard/feed", json=demo_feed().model_dump(mode="json")).status_code
            == 409
        )


def test_missing_research_database_is_explicit(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDUSTRYPULSE_DASHBOARD_DB", str(tmp_path / "missing.duckdb"))
    client = TestClient(create_app())
    response = client.get("/api/dashboard/research")
    assert response.status_code == 200
    assert response.json()["mode"] == "unavailable"
    assert not (tmp_path / "missing.duckdb").exists()
