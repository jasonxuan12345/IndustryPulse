from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import yaml

from industrypulse.ai_research import model_gateway as gateway
from industrypulse.services.research_mvp import WebTools
from industrypulse.services.workbench import ROOT


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("INDUSTRYPULSE_MODEL_CONFIG", str(tmp_path / "model.json"))
    for name in ("BASE_URL", "API_KEY", "NAME", "PROTOCOL"):
        monkeypatch.delenv("INDUSTRYPULSE_MODEL_" + name, raising=False)
    for name in ("KEY", "URL"):
        monkeypatch.delenv("INDUSTRYPULSE_SEARCH_" + name, raising=False)
    monkeypatch.delenv("INDUSTRYPULSE_FETCH_URL", raising=False)
    return yaml.safe_load((ROOT / "config/research/mvp.yaml").read_text(encoding="utf-8"))


def test_blank_example_and_environment_without_saved_state(isolated, monkeypatch):
    for name in ("BASE_URL", "API_KEY", "NAME", "PROTOCOL"):
        monkeypatch.setenv("INDUSTRYPULSE_MODEL_" + name, "")
    assert gateway.load_connection() is None
    monkeypatch.setenv("INDUSTRYPULSE_MODEL_BASE_URL", "https://vendor.example/v1")
    monkeypatch.setenv("INDUSTRYPULSE_MODEL_NAME", "my-model")
    monkeypatch.setenv("INDUSTRYPULSE_MODEL_API_KEY", "test-key")
    connection = gateway.load_connection()
    assert connection.model == "my-model" and connection.protocol == "chat_completions"
    assert not gateway.config_path().exists()
    assert "test-key" not in json.dumps(gateway.public_connection(connection))


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
def test_research_honors_custom_model_and_protocol(isolated, monkeypatch, protocol):
    monkeypatch.setenv("INDUSTRYPULSE_MODEL_BASE_URL", "https://vendor.example/v1")
    monkeypatch.setenv("INDUSTRYPULSE_MODEL_NAME", "my-model")
    monkeypatch.setenv("INDUSTRYPULSE_MODEL_API_KEY", "model-test-secret")
    monkeypatch.setenv("INDUSTRYPULSE_MODEL_PROTOCOL", protocol)
    calls = []

    def respond(request):
        calls.append(request)
        content = '```json\n{"query":"test query"}\n```'
        return httpx.Response(
            200,
            json={"output_text": content}
            if protocol == "responses"
            else {"choices": [{"message": {"content": content}}]},
        )

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **k: original(**k, transport=httpx.MockTransport(respond))
    )
    assert WebTools(isolated).ask("query") == {"query": "test query"}
    sent = json.loads(calls[0].content)
    assert sent["model"] == "my-model" and "thinking" not in sent
    assert calls[0].url.path.endswith(
        "responses" if protocol == "responses" else "chat/completions"
    )


def test_model_key_never_sent_to_different_search_origin(isolated, monkeypatch):
    monkeypatch.setenv("INDUSTRYPULSE_MODEL_BASE_URL", "https://vendor.example/v1")
    monkeypatch.setenv("INDUSTRYPULSE_MODEL_NAME", "my-model")
    monkeypatch.setenv("INDUSTRYPULSE_MODEL_API_KEY", "model-test-secret")
    tools = WebTools(isolated)
    with pytest.raises(ValueError, match="SearchKeyNotConfigured"):
        tools.search("query")
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"results": []})

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **k: original(**k, transport=httpx.MockTransport(respond))
    )
    monkeypatch.setenv("INDUSTRYPULSE_SEARCH_KEY", "search-test-secret")
    assert tools.search("query") == []
    assert calls[0].headers["authorization"] == "Bearer search-test-secret"
    assert "model-test-secret" not in str(calls[0].headers)


def test_portable_launcher_from_another_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/start_dashboard.py"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0 and b"--port" in result.stdout


def test_environment_host_change_does_not_reuse_saved_key(isolated, monkeypatch):
    gateway.save_connection(
        gateway.ModelConnection(
            base_url="https://old.example/v1", model="old", api_key="saved-test-secret"
        )
    )
    monkeypatch.setenv("INDUSTRYPULSE_MODEL_BASE_URL", "https://new.example/v1")
    assert gateway.load_connection().api_key.get_secret_value() == ""


def test_fresh_checkout_research_can_use_news_without_historical_database(tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    from industrypulse.contracts import TZ_SHANGHAI
    from industrypulse.services import research_mvp, research_news
    from industrypulse.services.workbench import Workbench

    shutil.copytree(ROOT / "config", tmp_path / "config")
    monkeypatch.delenv("INDUSTRYPULSE_DASHBOARD_DB", raising=False)
    service = Workbench(tmp_path)
    at = datetime.now(TZ_SHANGHAI)
    monkeypatch.setattr(
        research_news,
        "leads",
        lambda service, sector, now: (
            [
                {
                    "title": "合成首装测试线索",
                    "url": "https://www.stats.gov.cn/test-only",
                    "published_at": (at - timedelta(days=1)).isoformat(),
                }
            ]
            if sector == "CN_NBS_C06"
            else []
        ),
    )

    class OfflineTools:
        def ask(self, prompt):
            return {"query": "test"}

        def search(self, query):
            return []

    try:
        result = research_mvp.run(service, as_of=at, run_id="fresh", tools=OfflineTools())
        assert result["memo_count"] == 1 and result["status"] == "degraded"
        assert not service.path.exists()
        queue = json.loads(
            (Path(result["directory"]) / "opportunity_queue.json").read_text(encoding="utf-8")
        )
        assert queue["scanned_sectors"] == 20
        assert all(c["gaps"] for c in queue["coverage"])
    finally:
        service.pool.shutdown()
        service.runner.close()
