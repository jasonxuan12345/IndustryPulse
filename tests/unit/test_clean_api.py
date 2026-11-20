from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import duckdb
import pytest
from fastapi.testclient import TestClient

from industrypulse.api import dashboard
from industrypulse.api.app import create_app


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("INDUSTRYPULSE_NEWS_DIR", str(tmp_path / "news"))
    monkeypatch.setenv("INDUSTRYPULSE_SMART_MONEY_DIR", str(tmp_path / "sm"))
    monkeypatch.setenv("INDUSTRYPULSE_AI_DIR", str(tmp_path / "ai"))
    monkeypatch.setenv("INDUSTRYPULSE_SIGNALS_DIR", str(tmp_path / "signals"))
    monkeypatch.setenv("INDUSTRYPULSE_MODEL_CONFIG", str(tmp_path / "model.json"))
    monkeypatch.setenv("INDUSTRYPULSE_NEWS_AUTO_FETCH", "0")
    monkeypatch.setenv("INDUSTRYPULSE_MARKET_DATA", "0")


def test_clean_api_end_to_end() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/").status_code == 200

        radar = client.get("/api/radar/overview")
        assert radar.status_code == 200
        assert len(radar.json()["sectors"]) == 12

        sm_get = client.get("/api/smart-money")
        assert sm_get.status_code == 200
        assert len(sm_get.json()["institutions"]) >= 5

        created = client.post(
            "/api/smart-money/entries",
            json={
                "institution_id": "berkshire",
                "sector": "energy",
                "direction": "INCREASE",
                "title": "加仓能源基础设施",
                "summary": "长期现金流稳定",
                "disclosed_at": date.today().isoformat(),
            },
        )
        assert created.status_code == 200
        entry_id = created.json()["id"]
        assert client.delete(f"/api/smart-money/entries/{entry_id}").status_code == 200

        run_res = client.post(
            "/api/ai/butterfly/run", json={"use_ai": False, "refresh_signals": False}
        )
        assert run_res.status_code == 200
        assert run_res.json()["mode"] == "rule_fallback"

        latest = client.get("/api/ai/butterfly/latest")
        assert latest.status_code == 200
        assert latest.json()["run_id"] == run_res.json()["run_id"]

        verify_res = client.post(
            "/api/ai/verify",
            json={"text": "某行业出现产业链订单大增信号", "sector": "semiconductor"},
        )
        assert verify_res.status_code == 200
        assert verify_res.json()["sector"] == "semiconductor"

        hist_res = client.get("/api/ai/history")
        assert hist_res.status_code == 200
        assert hist_res.json()["count"] == 0


def test_market_service_lock_and_busy_db_degradation(monkeypatch) -> None:
    calls = 0
    counter_lock = threading.Lock()

    class SlowMarketService:
        def __init__(self) -> None:
            nonlocal calls
            time.sleep(0.05)
            with counter_lock:
                calls += 1

    monkeypatch.setenv("INDUSTRYPULSE_MARKET_DATA", "1")
    monkeypatch.setattr(dashboard, "_market_service", None)
    monkeypatch.setattr("industrypulse.market.service.MarketService", SlowMarketService)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: dashboard.market_service(), range(4)))
    assert calls == 1
    assert len({id(item) for item in results}) == 1

    def locked_db() -> None:
        raise duckdb.IOException("File is already open in another process")

    monkeypatch.setattr(dashboard, "_market_service", None)
    monkeypatch.setattr("industrypulse.market.service.MarketService", locked_db)
    with TestClient(create_app()) as client:
        macro_res = client.get("/api/dashboard/macro")
        assert macro_res.status_code == 503
        assert client.get("/api/radar/overview").status_code == 200
        assert client.get("/api/dashboard/feed").status_code == 200
    monkeypatch.setattr(dashboard, "_market_service", None)
