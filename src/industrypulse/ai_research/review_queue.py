"""Conflict detection preserves all originals and queues decisions for humans."""

import json
from datetime import datetime
from pathlib import Path

from industrypulse.contracts.observation import EvidenceCandidate, VerificationStatus


class ReviewQueue:
    def __init__(self, path: Path) -> None:
        self.path = path

    def reconcile(self, candidates: list[EvidenceCandidate]) -> list[EvidenceCandidate]:
        journal = self.path.with_suffix(".candidates.jsonl")
        all_candidates: dict[str, EvidenceCandidate] = {}
        if journal.exists():
            for line in journal.read_text(encoding="utf-8").splitlines():
                previous = EvidenceCandidate.model_validate_json(line)
                all_candidates[previous.candidate_id] = previous
        for candidate in candidates:
            existing = all_candidates.get(candidate.candidate_id)
            if existing and (
                existing.raw_sha256 != candidate.raw_sha256
                or existing.parsed_value != candidate.parsed_value
            ):
                raise ValueError("a candidate id cannot overwrite an existing source version")
            all_candidates[candidate.candidate_id] = candidate
        groups: dict[
            tuple[str, str | None, str | None, str | None, str | None], list[EvidenceCandidate]
        ] = {}
        for candidate in all_candidates.values():
            key = (
                candidate.query_id,
                candidate.observed_period,
                candidate.row_label,
                candidate.column_label,
                candidate.unit,
            )
            groups.setdefault(key, []).append(candidate)
        result = []
        for group in groups.values():
            values = {item.parsed_value for item in group if item.parsed_value is not None}
            for item in group:
                conflict = len(values) > 1
                updated = EvidenceCandidate.model_validate(
                    {
                        **item.model_dump(),
                        "verification_status": VerificationStatus.NEEDS_REVIEW
                        if conflict
                        else item.verification_status,
                        "notes": item.notes
                        + ("; conflicting values: preserve all vintages" if conflict else ""),
                    }
                )
                result.append(updated)
                if updated.verification_status == VerificationStatus.NEEDS_REVIEW:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with self.path.open("a", encoding="utf-8") as stream:
                        stream.write(updated.model_dump_json() + "\n")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with journal.open("a", encoding="utf-8") as stream:
            for item in result:
                stream.write(item.model_dump_json() + "\n")
        ids = {candidate.candidate_id for candidate in candidates}
        return [item for item in result if item.candidate_id in ids]

    def record_decision(
        self, candidate_id: str, reviewer: str, approved: bool, reason: str, decided_at: datetime
    ) -> None:
        if not reviewer.strip() or not reason.strip() or decided_at.tzinfo is None:
            raise ValueError("review decisions require reviewer, reason and aware timestamp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".decisions.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "candidate_id": candidate_id,
                        "reviewer": reviewer,
                        "approved": approved,
                        "reason": reason,
                        "decided_at": decided_at.isoformat(),
                    }
                )
                + "\n"
            )
