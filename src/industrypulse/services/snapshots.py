"""Atomic single-file publication and monthly forecast freshness."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from industrypulse.contracts import ContractBaseModel, PredictionFrame


class SnapshotView(ContractBaseModel):
    frame: PredictionFrame | None
    stale: bool
    age_days: float | None
    reason: str | None


class ForecastSnapshotStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> PredictionFrame | None:
        if not self.path.exists():
            return None
        envelope = json.loads(self.path.read_text(encoding="utf-8"))
        payload = str(envelope["payload"])
        if hashlib.sha256(payload.encode()).hexdigest() != envelope["sha256"]:
            raise ValueError("SNAPSHOT_CORRUPT")
        return PredictionFrame.model_validate_json(payload)

    def publish(self, frame: PredictionFrame) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A local SQLite lease serializes publication across independent job workers.
        with (
            closing(sqlite3.connect(str(self.path) + ".lease.sqlite3", timeout=30)) as lease,
            lease,
        ):
            lease.execute(
                "CREATE TABLE IF NOT EXISTS forecasts (id TEXT PRIMARY KEY, payload TEXT)"
            )
            lease.execute("BEGIN IMMEDIATE")
            for prediction in frame.predictions:
                payload = prediction.model_dump_json()
                previous = lease.execute(
                    "SELECT payload FROM forecasts WHERE id=?", [prediction.prediction_id]
                ).fetchone()
                if previous and previous[0] != payload:
                    raise ValueError("Frozen prediction ID cannot be modified")
                lease.execute(
                    "INSERT OR IGNORE INTO forecasts VALUES (?,?)",
                    [prediction.prediction_id, payload],
                )
            self._publish(frame)

    def _publish(self, frame: PredictionFrame) -> None:
        existing = self.load()
        if existing:
            previous = {(p.industry_id, p.target_id, p.model_id): p for p in existing.predictions}
            for prediction in frame.predictions:
                prior = previous.get(
                    (prediction.industry_id, prediction.target_id, prediction.model_id)
                )
                if prior and prediction.as_of < prior.as_of:
                    raise ValueError("Older prediction cannot replace a newer snapshot")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = frame.model_dump_json()
        digest = hashlib.sha256(payload.encode()).hexdigest()
        envelope = {"payload": payload, "sha256": digest}
        # Retain every complete frame even after the active pointer advances.
        archive_dir = self.path.parent / f"{self.path.stem}-history"
        archive_dir.mkdir(exist_ok=True)
        archive_path = archive_dir / f"{digest}.json"
        if archive_path.exists():
            if archive_path.read_text(encoding="utf-8") != payload:
                raise ValueError("SNAPSHOT_CORRUPT")
        else:
            with archive_path.open("x", encoding="utf-8") as archive:
                archive.write(payload)
                archive.flush()
                os.fsync(archive.fileno())
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(envelope, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def monthly(
        self, at: datetime, *, inputs_available: bool, produce: Callable[[], PredictionFrame]
    ) -> SnapshotView:
        if at.tzinfo is None:
            raise ValueError("Timezone required")
        old = self.load()
        reason = None
        if inputs_available:
            try:
                new = produce()
                if any(p.as_of != at for p in new.predictions):
                    raise ValueError("Prediction clock mismatch")
                self.publish(new)
                return SnapshotView(frame=new, stale=False, age_days=0, reason=None)
            except Exception as exc:
                reason = type(exc).__name__
        else:
            reason = "NOT_YET_PUBLISHED"
        age = None
        if old:
            age = (at - min(p.as_of for p in old.predictions)).total_seconds() / 86400
            if age < 0:
                raise ValueError("Future stored snapshot")
        return SnapshotView(frame=old, stale=True, age_days=age, reason=reason)
