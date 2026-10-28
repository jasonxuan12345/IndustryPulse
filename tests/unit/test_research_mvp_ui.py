from __future__ import annotations

from fastapi.testclient import TestClient

from industrypulse.api.app import create_app
from industrypulse.services import ledger, research_view
from industrypulse.services.workbench import Workbench, write_json


def archive(root, run_id="ui-fixture"):
    folder = root / "artifacts/research-mvp" / run_id
    record = {"opportunity_id": "op-1", "sector": "CN_NBS_C06", "sector_name": "煤炭"}
    artifacts = {
        "opportunity_queue.json": {
            "opportunities": [{**record, "inputs": {"large": "private"}}],
            "coverage": [{"name": "煤炭", "error": None}],
            "scanned_sectors": 20,
            "candidate_count": 1,
        },
        "investment_memo.json": {"memos": [{**record, "recommendation_status": "WATCH"}]},
        "evidence_packet.json": {
            "packets": [
                {
                    **record,
                    "facts": [],
                    "status": "unverified",
                    "documents": {"raw": "not for listing"},
                }
            ]
        },
        "research_review.json": {"reviews": [{**record, "status": "unverified", "rounds": []}]},
    }
    for name, value in artifacts.items():
        write_json(folder / name, value)
    write_json(
        folder / "manifest.json",
        {
            "run_id": run_id,
            "as_of": "2026-10-28T19:00:00+08:00",
            "created_at": "2026-10-28T19:01:00+08:00",
            "status": "degraded",
            "model_version": "test",
            "memo_count": 1,
            "hashes": {name: ledger._hash(value) for name, value in artifacts.items()},
        },
    )
    (folder / "daily_report.md").write_text("# Fixture report", encoding="utf-8")
    return folder


def test_ui_projection_checks_integrity_and_omits_large_raw_inputs(tmp_path):
    assert research_view.report(tmp_path)["status"] == "not_run"
    folder = archive(tmp_path)
    result = research_view.report(tmp_path)
    assert result["scanned_sectors"] == 20 and result["origin_label"] == "已归档研究 · 非实时行情"
    assert result["items"][0]["memo"]["recommendation_status"] == "WATCH"
    assert "inputs" not in result["items"][0]["opportunity"]
    assert "documents" not in result["items"][0]
    write_json(folder / "investment_memo.json", {"memos": []})
    import pytest

    with pytest.raises(ValueError, match="完整性"):
        research_view.report(tmp_path)


def test_ui_routes_empty_missing_corrupt_and_safe_download(tmp_path, monkeypatch):
    from industrypulse.api import research_loop as api

    service = Workbench(tmp_path)
    monkeypatch.setattr(api, "workspace", lambda: service)
    monkeypatch.setenv("INDUSTRYPULSE_NEWS_DIR", str(tmp_path / "news"))
    monkeypatch.setenv("INDUSTRYPULSE_NEWS_AUTO_FETCH", "0")
    with TestClient(create_app()) as client:
        assert client.get("/api/research/mvp/report").json()["status"] == "not_run"
        assert client.get("/api/research/mvp/report?run_id=unknown").status_code == 404
        assert client.get("/api/research/mvp/report?run_id=..%2Fsecrets").status_code == 409
        folder = archive(tmp_path)
        assert client.get("/api/research/mvp/report").status_code == 200
        response = client.get("/api/research/mvp/download/ui-fixture/daily_report.md")
        assert response.status_code == 200 and response.text.startswith("# Fixture")
        assert (
            client.get("/api/research/mvp/download/ui-fixture/tool_archive.json").status_code == 404
        )
        write_json(folder / "opportunity_queue.json", {})
        assert client.get("/api/research/mvp/report").status_code == 409
        assert (
            client.get("/api/research/mvp/download/ui-fixture/investment_memo.json").status_code
            == 409
        )
    service.pool.shutdown()
    service.runner.close()


def test_run_is_background_and_double_submission_reuses_job(tmp_path, monkeypatch):
    from industrypulse.api import research_loop as api

    service = Workbench(tmp_path)
    calls = []

    def launch(action, options):
        calls.append((action, options))
        return {"id": "test-job", "state": "queued"}

    monkeypatch.setattr(service, "launch", launch)
    monkeypatch.setattr(api, "workspace", lambda: service)
    monkeypatch.setenv("INDUSTRYPULSE_NEWS_DIR", str(tmp_path / "news"))
    monkeypatch.setenv("INDUSTRYPULSE_NEWS_AUTO_FETCH", "0")
    with TestClient(create_app()) as client:
        first = client.post("/api/research/mvp/run")
        second = client.post("/api/research/mvp/run")
        assert first.status_code == second.status_code == 202
        assert first.json() == second.json()
        assert len(calls) == 1 and calls[0][0] == "research_mvp"
        assert calls[0][1]["run_id"].startswith("mvp-ui-")
    service.pool.shutdown()
    service.runner.close()


def test_market_initialization_failure_preserves_news_and_explains_gap(tmp_path, monkeypatch):
    from industrypulse.api import dashboard

    def unavailable():
        raise RuntimeError("database unavailable; do not expose local details")

    monkeypatch.setattr(dashboard, "market_service", unavailable)
    monkeypatch.setenv("INDUSTRYPULSE_NEWS_DIR", str(tmp_path / "news"))
    monkeypatch.setenv("INDUSTRYPULSE_NEWS_AUTO_FETCH", "0")
    monkeypatch.setenv("INDUSTRYPULSE_FEED_FILE", str(tmp_path / "missing-feed.json"))
    monkeypatch.delenv("INDUSTRYPULSE_FEED_URL", raising=False)
    with TestClient(create_app()) as client:
        result = client.get("/api/dashboard/feed")
        assert result.status_code == 200
        assert result.json()["mode"] == "live"
        assert result.json()["market"]["status"] == "failed"
        assert "local details" not in result.text
