"""Candidate catalogue and explicit acquisition permission transitions."""

import csv
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from industrypulse.contracts.sources import (
    Frequency,
    LegalStatus,
    RevisionBehavior,
    SourceDescriptor,
    SourcePriority,
    SourceStatus,
)


@dataclass(frozen=True)
class RegistryEvent:
    source_id: str
    attribute: str
    previous: str
    current: str
    reason: str
    actor: str
    changed_at: str


class SourceRegistry:
    def __init__(self, sources: list[SourceDescriptor], audit_path: Path | None = None) -> None:
        self.sources = {source.source_id: source for source in sources}
        self.events: list[RegistryEvent] = []
        self.audit_path = audit_path
        if len(self.sources) != len(sources):
            raise ValueError("duplicate source_id")

    @classmethod
    def from_csv(cls, path: Path) -> "SourceRegistry":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        return cls(
            [
                SourceDescriptor(
                    source_id=row["source_id"].lower().replace("-", "."),
                    owner="S",
                    url=row["url"],
                    data_fields=[row["series_fields"]],
                    frequency=Frequency(row["frequency"])
                    if row["frequency"] in Frequency
                    else Frequency.IRREGULAR,
                    revision_behavior=RevisionBehavior.REVISED_LATER,
                    priority=SourcePriority(row["priority"]),
                    notes=f"{row['acquisition']}; {row['pit_risks']}; license not verified",
                )
                for row in rows
            ]
        )

    def filter(
        self, priority: SourcePriority | None = None, status: SourceStatus | None = None
    ) -> list[SourceDescriptor]:
        return [
            source
            for source in self.sources.values()
            if (priority is None or source.priority == priority)
            and (status is None or source.status == status)
        ]

    def _record(
        self, source_id: str, attribute: str, previous: str, current: str, reason: str, actor: str
    ) -> None:
        if not actor.strip() or not reason.strip():
            raise ValueError("transitions require an actor and reason")
        event = RegistryEvent(
            source_id, attribute, previous, current, reason, actor, datetime.now(UTC).isoformat()
        )
        if self.audit_path is not None:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
        self.events.append(event)

    def set_permission(
        self, source_id: str, status: LegalStatus, *, reason: str, actor: str = "local_operator"
    ) -> None:
        if not reason.strip():
            raise ValueError("permission changes require an audit reason")
        source = self.sources[source_id]
        if source.legal_status == LegalStatus.DENIED and status != LegalStatus.PENDING_REVIEW:
            raise ValueError(
                "denied sources must return to permission review before granting access"
            )
        self._record(source_id, "legal_status", source.legal_status, status, reason, actor)
        self.sources[source_id] = SourceDescriptor.model_validate(
            {
                **source.model_dump(),
                "legal_status": status,
                "notes": source.notes + "; " + reason,
            }
        )

    def set_status(
        self,
        source_id: str,
        status: SourceStatus,
        *,
        reason: str,
        actor: str,
        smoke_test_at: datetime | None = None,
    ) -> None:
        source = self.sources[source_id]
        transitions = {
            SourceStatus.CANDIDATE: {SourceStatus.ACTIVE, SourceStatus.RETIRED},
            SourceStatus.ACTIVE: {SourceStatus.DEGRADED, SourceStatus.RETIRED},
            SourceStatus.DEGRADED: {SourceStatus.ACTIVE, SourceStatus.RETIRED},
            SourceStatus.RETIRED: {SourceStatus.CANDIDATE},
        }
        if status not in transitions[source.status]:
            raise ValueError("invalid source lifecycle transition")
        if status == SourceStatus.ACTIVE:
            self.require_permission(source_id, manual=True)
            if smoke_test_at is None or smoke_test_at.tzinfo is None:
                raise ValueError("activation requires a timezone-aware acceptance smoke test")
        updated = SourceDescriptor.model_validate(
            {
                **source.model_dump(),
                "status": status,
                "last_smoke_test": smoke_test_at or source.last_smoke_test,
            }
        )
        self._record(source_id, "status", source.status, status, reason, actor)
        self.sources[source_id] = updated

    def require_permission(self, source_id: str, *, manual: bool = False) -> SourceDescriptor:
        source = self.sources[source_id]
        if source.status == SourceStatus.RETIRED:
            raise PermissionError("retired sources cannot acquire new data")
        allowed = {LegalStatus.PUBLIC_OFFICIAL}
        if manual:
            allowed.add(LegalStatus.MANUAL_EXPORT_ALLOWED)
        if source.legal_status not in allowed:
            raise PermissionError(f"acquisition blocked: {source.legal_status}")
        return source
