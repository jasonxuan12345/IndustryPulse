"""数据源健康监控：每个源记录 last_success / last_attempt / 延迟 / 状态。

热路径使用纯 dict（多线程 + pydantic-core 首建曾触发 GIL 崩溃），
SourceStatus 模型仅在 snapshot() 导出时构建；id 预注册避免运行期新建。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from industrypulse.contracts import TZ_SHANGHAI

from .models import SourceStatus

_BLOCKED_NOTES = {
    "nbs-easyquery": "国家统计局结构化查询接口对本网络返回 403（WAF UrlACL），标记 BLOCKED",
}

_DEFAULT_KIND = {
    "quotes": "quotes",
    "kline": "kline",
    "moneyflow": "moneyflow",
    "macro": "macro",
}


class SourceHealthMonitor:
    """线程安全的数据源健康记录；持久化到 data/market/health.json。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._states: dict[str, dict[str, Any]] = {}
        self._load()

    def _default(self, source_id: str) -> dict[str, Any]:
        provider, _, kind = source_id.partition(":")
        if kind not in _DEFAULT_KIND.values():
            kind = "sector" if provider == "sector" else "quotes"
        return {
            "source_id": source_id,
            "provider": provider,
            "kind": kind,
            "status": "idle",
            "last_success": None,
            "last_attempt": None,
            "latency_ms": None,
            "note": _BLOCKED_NOTES.get(source_id, ""),
        }

    def ensure(self, source_ids: list[str]) -> None:
        with self._lock:
            for source_id in source_ids:
                self._states.setdefault(source_id, self._default(source_id))

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for key, item in raw.items():
                state = self._default(key)
                state.update(item)
                self._states[key] = state
        except (OSError, ValueError):
            self._states = {}

    def persist(self) -> None:
        with self._lock:
            payload = {key: dict(item) for key, item in self._states.items()}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )

    def record_success(self, source_id: str, latency_ms: int, note: str = "") -> None:
        now = datetime.now(TZ_SHANGHAI)
        with self._lock:
            state = self._states.setdefault(source_id, self._default(source_id))
            state["status"] = "active"
            state["last_success"] = now
            state["last_attempt"] = now
            state["latency_ms"] = latency_ms
            if note:
                state["note"] = note
            elif source_id not in _BLOCKED_NOTES:
                state["note"] = ""

    def record_failure(self, source_id: str, note: str, *, blocked: bool = False) -> None:
        now = datetime.now(TZ_SHANGHAI)
        with self._lock:
            state = self._states.setdefault(source_id, self._default(source_id))
            state["status"] = "blocked" if blocked else "failed"
            state["last_attempt"] = now
            if note:
                state["note"] = note

    def mark_degraded(self, source_id: str, note: str) -> None:
        with self._lock:
            state = self._states.setdefault(source_id, self._default(source_id))
            state["status"] = "degraded"
            if note:
                state["note"] = note

    def snapshot(self) -> list[SourceStatus]:
        with self._lock:
            items = [dict(item) for item in self._states.values()]
        return [SourceStatus.model_validate(item) for item in items]
