from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from industrypulse.ai_research import model_gateway as gateway
from industrypulse.ai_research.http_provider import HttpResearchProvider
from industrypulse.api import create_app


@pytest.fixture
def configured_api(tmp_path, monkeypatch):
    monkeypatch.setenv("INDUSTRYPULSE_MODEL_CONFIG", str(tmp_path / "model.json"))
    calls = []

    def reply(request):
        calls.append(request)
        if request.url.path.endswith("/models"):
            if request.headers.get("authorization") == "Bearer invalid":
                return httpx.Response(401, json={"error": "bad key"})
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "model-z", "owned_by": "vendor"},
                        {"id": "model-a"},
                        {"id": "model-a"},
                    ]
                },
            )
        if request.url.path.endswith("/responses"):
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "OK responses"}],
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK chat"}}]})

    client_type = httpx.Client

    def client_factory(**kwargs):
        return client_type(**kwargs, transport=httpx.MockTransport(reply))

    monkeypatch.setattr(gateway.httpx, "Client", client_factory)
    return TestClient(create_app()), calls


def test_discover_save_restart_and_shared_runtime(configured_api):
    client, calls = configured_api
    options = {
        "base_url": "https://provider.example/v1/chat/completions",
        "api_key": "example-secret",
        "model": "model-a",
    }
    discovered = client.post("/api/dashboard/model/models", json=options)
    assert [item["id"] for item in discovered.json()["models"]] == ["model-a", "model-z"]
    saved = client.post("/api/dashboard/model/settings", json=options)
    assert saved.status_code == 200 and "example-secret" not in saved.text
    fresh_client = TestClient(create_app())
    settings = fresh_client.get("/api/dashboard/model/settings").json()
    assert settings["model"] == "model-a" and settings["has_api_key"]
    assert "api_key" not in settings
    tested = fresh_client.post("/api/dashboard/model/test", json={**options, "api_key": ""})
    assert tested.json()["ok"]
    assert calls[-1].headers["authorization"] == "Bearer example-secret"
    client.post(
        "/api/dashboard/model/settings", json={**options, "api_key": "", "model": "model-z"}
    )
    provider = HttpResearchProvider("", "https://legacy.example/extract", "old")
    assert provider.can_extract
    assert provider.extract("source text", {}) == "OK chat"
    assert json.loads(calls[-1].content)["model"] == "model-z"
    assert calls[-1].url.host == "provider.example"
    analysis = client.post("/api/dashboard/model/analyze", json={"text": "summarize"})
    assert analysis.json() == {"model": "model-z", "text": "OK chat"}


def test_discovery_failure_keeps_saved_settings_and_key_scoped_to_host(configured_api):
    client, calls = configured_api
    options = {
        "base_url": "https://provider.example/v1",
        "api_key": "example-secret",
        "model": "model-a",
    }
    client.post("/api/dashboard/model/settings", json=options)
    before = gateway.config_path().read_bytes()
    assert (
        client.post(
            "/api/dashboard/model/models", json={**options, "api_key": "invalid"}
        ).status_code
        == 422
    )
    assert gateway.config_path().read_bytes() == before
    client.post(
        "/api/dashboard/model/models",
        json={**options, "base_url": "https://other.example/v1", "api_key": ""},
    )
    assert "authorization" not in calls[-1].headers
    assert (
        client.post("/api/dashboard/model/settings", json={**options, "model": ""}).status_code
        == 422
    )
    assert gateway.config_path().read_bytes() == before


def test_responses_and_disable_take_effect_immediately(configured_api):
    client, calls = configured_api
    options = {
        "base_url": "https://provider.example/v1",
        "model": "model-a",
        "protocol": "responses",
    }
    client.post("/api/dashboard/model/settings", json=options)
    response = client.post("/api/dashboard/model/analyze", json={"text": "analysis"})
    assert response.json()["text"] == "OK responses"
    assert calls[-1].url.path == "/v1/responses"
    client.post("/api/dashboard/model/settings", json={**options, "enabled": False})
    assert client.post("/api/dashboard/model/analyze", json={"text": "analysis"}).status_code == 422
    assert not HttpResearchProvider("", "https://legacy.example/extract").can_extract


@pytest.mark.parametrize(
    "url",
    [
        "https://provider.example",
        "https://provider.example/v1/",
        "https://provider.example/v1/models",
        "https://provider.example/v1/responses",
    ],
)
def test_normalize_base_urls(url):
    assert gateway.normalize_url(url) == "https://provider.example/v1"


def test_no_provider_and_invalid_address(configured_api):
    client, _ = configured_api
    assert not client.get("/api/dashboard/model/settings").json()["configured"]
    assert client.post("/api/dashboard/model/analyze", json={"text": "hello"}).status_code == 422
    with pytest.raises(ValueError):
        gateway.resolve_connection(
            gateway.ModelConnection(base_url="invalid", api_key=SecretStr(""))
        )
