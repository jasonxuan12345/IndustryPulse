from __future__ import annotations

import hashlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from industrypulse.contracts import ObservationVintage, RawArtifact, ResearchMode
from industrypulse.contracts.pit import select_vintages_as_of
from industrypulse.contracts.sources import SourceDescriptor
from industrypulse.repositories import Database, DuckDBObservationRepository, PublicationError
from industrypulse.taxonomy import load_steel_taxonomy


def make_repository(tmp_path: Path) -> DuckDBObservationRepository:
    repo = DuckDBObservationRepository(tmp_path / "store.duckdb", bronze_root=tmp_path / "bronze")
    registry = load_steel_taxonomy()
    for source_id in {s.source_id for s in registry.series}:
        repo.register_source(
            SourceDescriptor(
                source_id=source_id,
                owner="test",
                url="https://example.org/official",
                data_fields=["synthetic"],
                frequency="M",
                legal_status="public_official",
                revision_behavior="revised_later",
                priority="S0",
            )
        )
    for series in registry.series:
        repo.register_series(series)
    repo.register_taxonomy(registry.nodes, registry.indicators)
    content = b"synthetic original publication"
    repo.archive(
        RawArtifact(
            artifact_id="raw",
            source_id="cn.nbs",
            uri="staging",
            mime="text/plain",
            sha256=hashlib.sha256(content).hexdigest(),
            byte_size=len(content),
            retrieved_at="2025-01-01T12:00:00+08:00",
        ),
        content,
    )
    return repo


@pytest.mark.parametrize("mode", list(ResearchMode))
@pytest.mark.parametrize(
    "as_of", ["2025-03-27T09:30:00+08:00", "2025-10-10T09:29:00+08:00", "2025-12-31T23:00:00+08:00"]
)
@pytest.mark.parametrize("lag", [5, 15])
def test_sql_matches_frozen_semantics(
    tmp_path: Path,
    steel_vintages: list[ObservationVintage],
    trading_days: list,
    mode: ResearchMode,
    as_of: str,
    lag: int,
) -> None:
    repo = make_repository(tmp_path)
    repo.trading_days = tuple(trading_days)
    repo.conservative_lag_days = lag
    rows = [
        r.model_copy(update={"artifact_id": "raw", "evidence_candidate_id": None})
        for r in steel_vintages
    ]
    repo.publish("all", rows)
    ctx = SimpleNamespace(as_of=datetime.fromisoformat(as_of), research_mode=mode)
    actual = repo.select([rows[0].series_id], ctx)
    expected = select_vintages_as_of(
        rows, as_of=ctx.as_of, mode=mode, trading_days=trading_days, conservative_lag_days=lag
    )
    assert actual == expected
    assert repo.fetch([rows[0].series_id], ctx).vintages == expected.selected


def test_atomic_idempotent_and_frozen_snapshot(
    tmp_path: Path, steel_vintages: list[ObservationVintage]
) -> None:
    repo = make_repository(tmp_path)
    rows = [
        r.model_copy(update={"artifact_id": "raw", "evidence_candidate_id": None})
        for r in steel_vintages
    ]
    digest = repo.publish("old", rows[:1])
    assert repo.publish("old", rows[:1]) == digest
    bad = rows[2].model_copy(update={"unit": "万元"})
    with pytest.raises(PublicationError, match="unit"):
        repo.publish("failed", [rows[1], bad])
    ctx = SimpleNamespace(
        as_of=datetime.fromisoformat("2026-01-01T00:00:00+08:00"),
        research_mode=ResearchMode.STRICT_PIT,
    )
    assert repo.fetch([rows[0].series_id], ctx).vintages == rows[:1]
    repo.publish("new", rows)
    ctx.dataset_snapshot_id = "old"
    assert repo.fetch([rows[0].series_id], ctx).vintages == rows[:1]
    with pytest.raises(PublicationError, match="snapshot"):
        repo.publish("old", rows)


def test_link_and_immutable_bronze(
    tmp_path: Path, steel_vintages: list[ObservationVintage]
) -> None:
    repo = make_repository(tmp_path)
    with pytest.raises(PublicationError, match="ancestor"):
        repo.publish("lost", steel_vintages[:1])
    with pytest.raises(PublicationError, match="dangling"):
        repo.publish("lost", [steel_vintages[0].model_copy(update={"artifact_id": "absent"})])
    archived = next((tmp_path / "bronze").glob("*/*"))
    archived.write_bytes(b"corruption")
    with pytest.raises(PublicationError, match="corrupted"):
        repo.publish(
            "bad",
            [
                steel_vintages[0].model_copy(
                    update={"artifact_id": "raw", "evidence_candidate_id": None}
                )
            ],
        )


def test_ancestry_and_exploratory_watermark(
    tmp_path: Path, steel_vintages: list[ObservationVintage]
) -> None:
    repo = make_repository(tmp_path)
    row = steel_vintages[0].model_copy(update={"artifact_id": "raw", "evidence_candidate_id": None})
    repo.publish("single", [row])
    repo.record_ancestry("f", [(row.series_id, row.observation_start, row.vintage_id)])
    ctx = SimpleNamespace(as_of=row.available_at, research_mode=ResearchMode.STRICT_PIT)
    ancestors, maximum = repo.ancestry("f", ctx)
    assert ancestors == [row]
    assert maximum == row.available_at
    ctx.as_of = datetime.fromisoformat("2024-01-01T00:00:00+08:00")
    with pytest.raises(PublicationError, match="unavailable"):
        repo.ancestry("f", ctx)
    assert "NOT investment proof" in repo.query_metadata(ResearchMode.EXPLORATORY)["watermark"]


def test_serialized_writers_and_repeatable_migration(tmp_path: Path) -> None:
    path = tmp_path / "concurrent.duckdb"

    def write(index: int) -> None:
        db = Database(path)
        db.migrate()
        with db.transaction() as conn:
            conn.execute("INSERT INTO catalog VALUES ('test',?,?)", [str(index), str(index)])

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(12)))
    with Database(path).transaction() as conn:
        assert conn.execute("SELECT count(*) FROM catalog").fetchone()[0] == 12
        assert conn.execute("SELECT count(*) FROM schema_migration").fetchone()[0] == 1


def test_cross_process_writer_lease(tmp_path: Path) -> None:
    path = tmp_path / "multi-process.duckdb"
    script = """
import sys
sys.path.insert(0, 'src')
from industrypulse.repositories import Database
database = Database(sys.argv[1])
with database.transaction() as conn:
    conn.execute("INSERT INTO catalog VALUES ('process',?,?)", [sys.argv[2], sys.argv[2]])
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(path), str(i)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for i in range(3)
    ]
    for process in processes:
        _, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr.decode(errors="replace")
    with Database(path).transaction() as conn:
        assert conn.execute("SELECT count(*) FROM catalog").fetchone()[0] == 3


def test_transitive_ancestors_and_snapshot_membership(
    tmp_path: Path, steel_vintages: list[ObservationVintage]
) -> None:
    repo = make_repository(tmp_path)
    rows = [
        r.model_copy(update={"artifact_id": "raw", "evidence_candidate_id": None})
        for r in steel_vintages[:2]
    ]
    repo.publish("first", rows[:1])
    repo.publish("both", rows)
    keys = [(r.series_id, r.observation_start, r.vintage_id) for r in rows]
    repo.record_ancestry("f1", keys[:1])
    repo.record_derived_ancestry("f2", ["f1"], keys[1:])
    repo.record_derived_ancestry("f3", ["f1", "f2"])
    ctx = SimpleNamespace(
        as_of=datetime.fromisoformat("2026-01-01T00:00:00+08:00"),
        research_mode=ResearchMode.STRICT_PIT,
        dataset_snapshot_id="both",
    )
    assert repo.ancestry("f3", ctx)[0] == rows
    ctx.dataset_snapshot_id = "first"
    with pytest.raises(PublicationError, match="snapshot"):
        repo.ancestry("f3", ctx)


def test_candidate_chain_rejects_unverified_or_mismatched_value(
    tmp_path: Path,
    steel_vintages: list[ObservationVintage],
    evidence_candidates: list,
) -> None:
    repo = make_repository(tmp_path)
    original = next(c for c in evidence_candidates if c.verification_status.value == "verified")
    raw_hash = hashlib.sha256(b"synthetic original publication").hexdigest()
    candidate = type(original).model_validate({**original.model_dump(), "raw_sha256": raw_hash})
    repo.register_candidate(candidate)
    row = next(r for r in steel_vintages if r.evidence_candidate_id == original.candidate_id)
    row = row.model_copy(update={"artifact_id": "raw"})
    repo.publish("verified", [row])
    bad = row.model_copy(update={"vintage_id": 20, "value": row.value + 1})
    with pytest.raises(PublicationError, match="value/unit"):
        repo.publish("mismatch", [bad])


def test_bronze_hash_deduplication_and_reimport(tmp_path: Path) -> None:
    repo = make_repository(tmp_path)
    with repo.database.transaction() as conn:
        artifact = RawArtifact.model_validate_json(
            conn.execute("SELECT payload FROM raw_artifact").fetchone()[0]
        )
    content = b"synthetic original publication"
    assert repo.archive(artifact, content) == artifact
    alias = repo.archive(artifact.model_copy(update={"artifact_id": "alias"}), content)
    assert alias.uri == artifact.uri
    assert len(list((tmp_path / "bronze").glob("*/*"))) == 1


def test_per_series_lag_and_query_watermark(
    tmp_path: Path, steel_vintages: list[ObservationVintage]
) -> None:
    repo = make_repository(tmp_path)
    c = next(r for r in steel_vintages if r.release_evidence.value == "C")
    c = c.model_copy(update={"artifact_id": "raw", "evidence_candidate_id": None})
    repo.publish("c", [c])
    tuned = DuckDBObservationRepository(
        repo.database,
        bronze_root=repo.bronze_root,
        series_lag_days={c.series_id: 15},
        policy_version="p99-v2",
    )
    ctx = SimpleNamespace(as_of=c.available_at, research_mode=ResearchMode.RECONSTRUCTED_PIT)
    query = tuned.query([c.series_id], ctx)
    assert not query.selection.selected
    assert query.policy_version == "p99-v2"
    assert query.series_lag_days[c.series_id] == 15
    ctx.research_mode = ResearchMode.EXPLORATORY
    query = tuned.query([c.series_id], ctx)
    assert query.selection.selected == [c]
    assert not query.pit_verified and "NOT investment proof" in query.watermark
