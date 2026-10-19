"""DuckDB transactions serialized across threads and processes by a SQLite writer lease."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any

import duckdb

_LOCKS: dict[str, RLock] = {}
_LOCKS_GUARD = RLock()

_DDL = (
    "CREATE TABLE IF NOT EXISTS schema_migration(version INTEGER PRIMARY KEY)",
    "CREATE TABLE IF NOT EXISTS catalog(kind VARCHAR, id VARCHAR, payload VARCHAR, "
    "PRIMARY KEY(kind,id))",
    "CREATE TABLE IF NOT EXISTS raw_artifact(artifact_id VARCHAR PRIMARY KEY, "
    "source_id VARCHAR, sha256 VARCHAR, payload VARCHAR)",
    "CREATE TABLE IF NOT EXISTS observation_vintage(series_id VARCHAR, "
    "observation_start DATE, vintage_id BIGINT, payload VARCHAR, "
    "PRIMARY KEY(series_id,observation_start,vintage_id))",
    "CREATE TABLE IF NOT EXISTS publication(snapshot_id VARCHAR PRIMARY KEY, sha256 VARCHAR)",
    "CREATE TABLE IF NOT EXISTS publication_row(snapshot_id VARCHAR, series_id VARCHAR, "
    "observation_start DATE, vintage_id BIGINT, "
    "PRIMARY KEY(snapshot_id,series_id,observation_start,vintage_id))",
    "CREATE TABLE IF NOT EXISTS ancestry(feature_id VARCHAR PRIMARY KEY, payload VARCHAR)",
    "CREATE TABLE IF NOT EXISTS asset_master(asset_id VARCHAR PRIMARY KEY, payload VARCHAR)",
    "CREATE TABLE IF NOT EXISTS market_bar(asset_id VARCHAR, trade_date DATE, payload VARCHAR, "
    "PRIMARY KEY(asset_id,trade_date))",
    "CREATE TABLE IF NOT EXISTS corporate_action(asset_id VARCHAR, ex_date DATE, "
    "action_type VARCHAR, payload VARCHAR, PRIMARY KEY(asset_id,ex_date,action_type))",
)


class Database:
    """All reads/writes use the same lease; no exposed arbitrary concurrent writers.

    File connections are closed before releasing the cross-process lease. This avoids
    DuckDB's multi-process connection/file-lock conflict, including readers.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(Path(path).resolve()) if str(path) != ":memory:" else ":memory:"
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        key = self.path if self.path != ":memory:" else str(id(self))
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(key, RLock())
        self._memory: Any = duckdb.connect() if self.path == ":memory:" else None
        self.migrate()

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self._lock:
            lease = None
            conn = None
            try:
                if self.path != ":memory:":
                    lease = sqlite3.connect(self.path + ".writer.sqlite", timeout=30)
                    lease.execute("CREATE TABLE IF NOT EXISTS writer_lease(id INTEGER PRIMARY KEY)")
                    lease.execute("BEGIN IMMEDIATE")
                conn = self._memory if self._memory is not None else duckdb.connect(self.path)
                conn.execute("BEGIN TRANSACTION")
                try:
                    yield conn
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
            finally:
                if conn is not None and self._memory is None:
                    conn.close()
                if lease is not None:
                    lease.rollback()
                    lease.close()

    def migrate(self) -> None:
        with self.transaction() as conn:
            for statement in _DDL:
                conn.execute(statement)
            conn.execute("INSERT INTO schema_migration VALUES (1) ON CONFLICT DO NOTHING")

    def close(self) -> None:
        with self._lock:
            if self._memory is not None:
                self._memory.close()
