"""Persistent user-selected model connection shared by AI features."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field, SecretStr

from industrypulse.core import TZ_SHANGHAI, replace_file

ROOT = Path(__file__).resolve().parents[3]


def normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username:
        raise ValueError("请填写完整的 http:// 或 https:// API 地址")
    if parts.query or parts.fragment:
        raise ValueError("API 地址请使用基础路径，不包含查询参数或 #")
    path = parts.path.rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/models"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunsplit((parts.scheme, parts.netloc, path or "/v1", "", ""))


class ModelConnection(BaseModel):
    base_url: str
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    model: str = ""
    protocol: Literal["chat_completions", "responses"] = "chat_completions"
    enabled: bool = True


def config_path() -> Path:
    return Path(os.getenv("INDUSTRYPULSE_MODEL_CONFIG", str(ROOT / "data/model-provider.json")))


def load_connection() -> ModelConnection | None:
    path = config_path()
    saved = (
        ModelConnection.model_validate_json(path.read_text(encoding="utf-8"))
        if path.exists()
        else None
    )
    prefix = "INDUSTRYPULSE_MODEL_"
    if not any(os.getenv(prefix + k) for k in ("BASE_URL", "API_KEY", "NAME", "PROTOCOL")):
        return saved
    base = os.getenv(prefix + "BASE_URL") or (saved.base_url if saved else "")
    model = os.getenv(prefix + "NAME") or (saved.model if saved else "")
    if not base or not model:
        raise ValueError("环境变量需配置 INDUSTRYPULSE_MODEL_BASE_URL 和 INDUSTRYPULSE_MODEL_NAME")
    key = os.getenv(prefix + "API_KEY")
    if key is None:
        key = (
            saved.api_key.get_secret_value()
            if saved and normalize_url(base) == normalize_url(saved.base_url)
            else ""
        )
    return ModelConnection.model_validate(
        {
            "base_url": normalize_url(base),
            "model": model,
            "api_key": key,
            "protocol": os.getenv(prefix + "PROTOCOL")
            or (saved.protocol if saved else "chat_completions"),
            "enabled": saved.enabled if saved else True,
        }
    )


def resolve_connection(connection: ModelConnection) -> ModelConnection:
    base_url = normalize_url(connection.base_url)
    previous = load_connection()
    key = connection.api_key
    if not key.get_secret_value() and previous and normalize_url(previous.base_url) == base_url:
        key = previous.api_key
    return connection.model_copy(update={"base_url": base_url, "api_key": key})


def public_connection(connection: ModelConnection | None) -> dict[str, Any]:
    if connection is None:
        return {
            "configured": False,
            "enabled": False,
            "base_url": "",
            "model": "",
            "protocol": "chat_completions",
            "has_api_key": False,
        }
    return {
        "configured": bool(connection.model),
        "enabled": connection.enabled,
        "base_url": connection.base_url,
        "model": connection.model,
        "protocol": connection.protocol,
        "has_api_key": bool(connection.api_key.get_secret_value()),
    }


def save_connection(connection: ModelConnection) -> dict[str, Any]:
    resolved = resolve_connection(connection)
    if not resolved.model.strip():
        raise ValueError("请选择或填写一个模型 ID")
    resolved = resolved.model_copy(update={"model": resolved.model.strip()})
    payload = {
        **resolved.model_dump(mode="json", exclude={"api_key"}),
        "api_key": resolved.api_key.get_secret_value(),
        "updated_at": datetime.now(TZ_SHANGHAI).isoformat(),
    }
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    replace_file(temporary, path)
    return public_connection(resolved)


def headers(connection: ModelConnection) -> dict[str, str]:
    key = connection.api_key.get_secret_value()
    return {"Authorization": f"Bearer {key}"} if key else {}


def _raise_gateway_error(exc: Exception, effective_timeout: float) -> None:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        message = {
            401: "API Key 无效或已过期",
            403: "当前 API Key 没有访问权限",
            404: "接口或模型不存在，请核对地址和调用协议",
            429: "额度不足或请求过于频繁",
        }.get(status, f"服务商返回 HTTP {status}")
        raise ValueError(message) from exc
    if isinstance(exc, httpx.TimeoutException):
        raise ValueError(
            f"模型响应超时（已等待 {int(effective_timeout)}s），可稍后重试或换用响应更快的模型"
        ) from exc
    if isinstance(exc, httpx.ConnectError):
        raise ValueError("无法连接到模型服务器，请检查 API 地址、本机网络或代理设置") from exc
    if isinstance(exc, httpx.RemoteProtocolError):
        detail = str(exc).strip()[:180]
        suffix = f"（{detail}）" if detail else ""
        raise ValueError(f"模型服务器连接中断{suffix}，请重试") from exc
    raise ValueError(f"模型接口请求或响应解析异常（{type(exc).__name__}）") from exc


def provider_request(
    connection: ModelConnection,
    method: str,
    endpoint: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float | None = None,
) -> Any:
    effective_timeout = timeout if timeout is not None else (20.0 if method == "GET" else 180.0)
    try:
        with httpx.Client(timeout=effective_timeout, headers=headers(connection)) as client:
            response = client.request(method, connection.base_url + endpoint, json=payload)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        _raise_gateway_error(exc, effective_timeout)
        raise


def _extract_chat_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices", [])
    content = choices[0].get("message", {}).get("content") if choices else None
    if isinstance(content, str):
        return content
    return "\n".join(part.get("text", "") for part in (content or []) if isinstance(part, dict))


def _extract_responses_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    return "\n".join(
        part.get("text", "")
        for item in payload.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    )


def _extract_sse_delta(
    event: dict[str, Any], protocol: Literal["chat_completions", "responses"]
) -> str:
    if isinstance(event.get("error"), dict):
        msg = event["error"].get("message") or str(event["error"])
        raise ValueError(f"模型服务返回错误：{msg}")
    if protocol == "responses":
        etype = event.get("type")
        if etype == "response.output_text.delta":
            delta = event.get("delta")
            return str(delta) if isinstance(delta, str) else ""
        if etype == "response.completed" and isinstance(event.get("response"), dict):
            return ""
        return ""
    choices = event.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    delta_obj = choices[0].get("delta")
    if isinstance(delta_obj, dict):
        content = delta_obj.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    msg_obj = choices[0].get("message")
    if isinstance(msg_obj, dict) and isinstance(msg_obj.get("content"), str):
        return str(msg_obj["content"])
    return ""


def provider_stream_text(
    connection: ModelConnection,
    endpoint: str,
    payload: dict[str, Any],
    *,
    protocol: Literal["chat_completions", "responses"],
    timeout: float | None = None,
) -> str:
    effective_timeout = timeout if timeout is not None else 180.0
    client_timeout = httpx.Timeout(connect=20.0, read=effective_timeout, write=30.0, pool=20.0)
    stream_payload = {**payload, "stream": True}
    chunks: list[str] = []
    completed_fallback = ""
    try:
        with (
            httpx.Client(timeout=client_timeout, headers=headers(connection)) as client,
            client.stream("POST", connection.base_url + endpoint, json=stream_payload) as response,
        ):
            if response.status_code >= 400:
                response.read()
                response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "application/json" in content_type and "text/event-stream" not in content_type:
                raw_bytes = response.read()
                data = json.loads(raw_bytes.decode("utf-8"))
                return (
                    _extract_responses_text(data)
                    if protocol == "responses"
                    else _extract_chat_text(data)
                )

            saw_sse = "text/event-stream" in content_type
            raw_lines: list[str] = []
            for raw_line in response.iter_lines():
                line = raw_line.strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    saw_sse = True
                    continue
                if line.startswith("data:"):
                    saw_sse = True
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    if not data_str:
                        continue
                    event = json.loads(data_str)
                    if isinstance(event, dict):
                        if (
                            protocol == "responses"
                            and event.get("type") == "response.completed"
                            and isinstance(event.get("response"), dict)
                        ):
                            completed_fallback = _extract_responses_text(event["response"])
                        piece = _extract_sse_delta(event, protocol)
                        if piece:
                            chunks.append(piece)
                elif not saw_sse:
                    raw_lines.append(raw_line)

            if not saw_sse and raw_lines:
                data = json.loads("\n".join(raw_lines))
                return (
                    _extract_responses_text(data)
                    if protocol == "responses"
                    else _extract_chat_text(data)
                )
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        _raise_gateway_error(exc, effective_timeout)
        raise

    return "".join(chunks) if chunks else completed_fallback


def list_models(connection: ModelConnection) -> list[dict[str, str]]:
    payload = provider_request(resolve_connection(connection), "GET", "/models")
    rows = payload.get("data", payload.get("models", [])) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("模型列表格式无法识别，可在下方手动填写模型 ID")
    models = {}
    for item in rows:
        if not isinstance(item, (str, dict)):
            continue
        identifier = item if isinstance(item, str) else item.get("id", item.get("name", ""))
        if identifier:
            models[str(identifier)] = {
                "id": str(identifier),
                "owner": str(item.get("owned_by", "")) if isinstance(item, dict) else "",
            }
    return [models[key] for key in sorted(models)]


def complete(
    connection: ModelConnection,
    prompt: str,
    *,
    system: str = "你是行业研究助手。基于提供的资料回答，不编造新闻或数值。",
    timeout: float | None = None,
) -> str:
    if not connection.enabled or not connection.model:
        raise ValueError("请先在设置中启用并保存模型")
    if connection.protocol == "responses":
        text = provider_stream_text(
            connection,
            "/responses",
            {"model": connection.model, "instructions": system, "input": prompt},
            protocol="responses",
            timeout=timeout,
        )
    else:
        text = provider_stream_text(
            connection,
            "/chat/completions",
            {
                "model": connection.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
            protocol="chat_completions",
            timeout=timeout,
        )
    if not text.strip():
        raise ValueError("模型未返回文本；请检查是否选择了可用于文本对话的模型")
    return str(text).strip()


def parse_json_reply(raw: str) -> dict[str, Any]:
    """Extract a JSON object from raw model text (supports fenced code blocks)."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        brace = re.search(r"\{[\s\S]*\}", text)
        if not brace:
            raise ValueError("模型响应中未找到有效 JSON 对象") from None
        parsed = json.loads(brace.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("模型响应 JSON 必须是对象")
    return cast(dict[str, Any], parsed)


def complete_json(
    connection: ModelConnection,
    prompt: str,
    *,
    system: str,
    timeout: float | None = None,
) -> dict[str, Any]:
    raw = complete(connection, prompt, system=system, timeout=timeout)
    return parse_json_reply(raw)
