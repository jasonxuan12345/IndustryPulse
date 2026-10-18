"""Persistent idempotent local batch execution with bounded retries."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

from industrypulse.contracts import TZ_SHANGHAI, JobState, JobStatus

T = TypeVar("T")


class JobRunner:
    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS jobs (key TEXT PRIMARY KEY, status TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS job_events (trace_id TEXT, state TEXT, attempt INTEGER)"
        )
        self.connection.commit()
        self.lock = threading.RLock()
        self.max_attempts, self.sleep = max_attempts, sleep
        self.clock = clock or (lambda: datetime.now(TZ_SHANGHAI))

    def close(self) -> None:
        self.connection.close()

    def status(self, key: str) -> JobStatus | None:
        with self.lock:
            row = self.connection.execute("SELECT status FROM jobs WHERE key=?", [key]).fetchone()
            return JobStatus.model_validate_json(row[0]) if row else None

    def _save(self, key: str, status: JobStatus) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO jobs VALUES (?, ?)", [key, status.model_dump_json()]
        )
        self.connection.execute(
            "INSERT INTO job_events VALUES (?, ?, ?)",
            [status.job_id, status.state.value, status.retry_count],
        )
        self.connection.commit()

    def events(self, trace_id: str) -> list[dict[str, str | int]]:
        with self.lock:
            return [
                {"trace_id": trace_id, "state": state, "attempt": attempt}
                for state, attempt in self.connection.execute(
                    "SELECT state, attempt FROM job_events WHERE trace_id=? ORDER BY rowid",
                    [trace_id],
                )
            ]

    def run(
        self,
        name: str,
        key: str,
        action: Callable[[], T],
        *,
        retry_on: tuple[type[Exception], ...] = (TimeoutError, ConnectionError),
    ) -> tuple[JobStatus, T | None]:
        if not name or not key:
            raise ValueError("Job name and idempotency key required")
        with self.lock:
            # BEGIN IMMEDIATE serializes claims across local processes. Claim commits before work.
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute("SELECT status FROM jobs WHERE key=?", [key]).fetchone()
            prior = JobStatus.model_validate_json(row[0]) if row else None
            if prior and prior.state in {JobState.SUCCEEDED, JobState.RUNNING, JobState.RETRYING}:
                self.connection.rollback()
                return prior.model_copy(update={"state": JobState.SKIPPED}), None
            trace = uuid4().hex
            started = self.clock()
            status = JobStatus(
                job_id=trace,
                job_name=name,
                state=JobState.RUNNING,
                start_at=started,
                idempotency_key=key,
            )
            self._save(key, status)
        for attempt in range(self.max_attempts):
            try:
                result = action()
            except Exception as exc:
                retry = isinstance(exc, retry_on) and attempt + 1 < self.max_attempts
                # Exception text can include credentials or source content. Persist only its type.
                status = status.model_copy(
                    update={
                        "state": JobState.RETRYING if retry else JobState.FAILED,
                        "retry_count": attempt,
                        "error": type(exc).__name__,
                        "end_at": None if retry else self.clock(),
                    }
                )
                with self.lock:
                    self._save(key, status)
                if retry:
                    self.sleep(min(2**attempt, 30))
                    continue
                return status, None
            status = status.model_copy(
                update={
                    "state": JobState.SUCCEEDED,
                    "retry_count": attempt,
                    "error": None,
                    "end_at": self.clock(),
                }
            )
            with self.lock:
                self._save(key, status)
            return status, result
        raise AssertionError("Unreachable")

    def recover_interrupted(self, key: str) -> JobStatus:
        """Operator recovery after verifying the old worker is gone; never auto-steal claims."""
        with self.lock:
            current = self.status(key)
            if current is None or current.state not in {JobState.RUNNING, JobState.RETRYING}:
                raise ValueError("No interrupted job")
            failed = current.model_copy(
                update={
                    "state": JobState.FAILED,
                    "end_at": self.clock(),
                    "error": "InterruptedWorker",
                }
            )
            self._save(key, failed)
            return failed


def idempotency_key(source_id: str, period: str, artifact_sha256: str, parser_version: str) -> str:
    return json.dumps([source_id, period, artifact_sha256, parser_version], separators=(",", ":"))
