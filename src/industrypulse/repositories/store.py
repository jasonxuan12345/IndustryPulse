"""Immutable Bronze, atomic Silver publication, PIT selection and ancestry."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from industrypulse.contracts.common import ContractBaseModel
from industrypulse.contracts.context import ResearchMode
from industrypulse.contracts.domain import IndicatorSpec, IndustryNode
from industrypulse.contracts.model import AnalysisContextLike
from industrypulse.contracts.observation import (
    EvidenceCandidate,
    ObservationFrame,
    ObservationVintage,
    RawArtifact,
    VerificationStatus,
)
from industrypulse.contracts.pit import (
    AsOfSelection,
    effective_available_at,
    select_vintages_as_of,
)
from industrypulse.contracts.sources import LegalStatus, SeriesSpec, SourceDescriptor

from .database import Database


class PublicationError(ValueError):
    """The complete batch is rejected; existing snapshots remain unchanged."""


class RepositoryQuery(ContractBaseModel):
    selection: AsOfSelection
    policy_version: str
    conservative_lag_days: int
    series_lag_days: dict[str, int]
    pit_verified: bool
    watermark: str


class DuckDBObservationRepository:
    """ObservationRepository implementation with mandatory registered provenance.

    A missing value still needs an archived source: absence is an evidenced fact.
    Registry entries and publications are immutable under their versioned IDs.
    """

    def __init__(
        self,
        database: Database | str | Path = ":memory:",
        *,
        bronze_root: str | Path | None = None,
        trading_days: Sequence[date] = (),
        conservative_lag_days: int = 5,
        policy_version: str = "default-v1",
        series_lag_days: Mapping[str, int] | None = None,
    ) -> None:
        if conservative_lag_days < 0 or not policy_version:
            raise ValueError("lag must be nonnegative and policy_version must be present")
        self.database = database if isinstance(database, Database) else Database(database)
        default_root = (
            Path(self.database.path).parent / "bronze"
            if self.database.path != ":memory:"
            else Path("data/bronze")
        )
        self.bronze_root = Path(bronze_root or default_root).resolve()
        self.bronze_root.mkdir(parents=True, exist_ok=True)
        self.trading_days = tuple(sorted(set(trading_days)))
        self.conservative_lag_days = conservative_lag_days
        self.policy_version = policy_version
        if any(lag < 0 for lag in (series_lag_days or {}).values()):
            raise ValueError("series lags must be nonnegative")
        self.series_lag_days = MappingProxyType(dict(series_lag_days or {}))

    def _effective(self, row: ObservationVintage, mode: ResearchMode) -> datetime:
        return effective_available_at(
            row,
            mode,
            trading_days=self.trading_days,
            conservative_lag_days=self.series_lag_days.get(
                row.series_id, self.conservative_lag_days
            ),
        )

    def _selection(
        self, rows: Sequence[ObservationVintage], ctx: AnalysisContextLike
    ) -> AsOfSelection:
        parts = [
            select_vintages_as_of(
                [r for r in rows if r.series_id == sid],
                as_of=ctx.as_of,
                mode=ctx.research_mode,
                trading_days=self.trading_days,
                conservative_lag_days=self.series_lag_days.get(sid, self.conservative_lag_days),
            )
            for sid in sorted({r.series_id for r in rows})
        ]
        return AsOfSelection(
            as_of=ctx.as_of,
            mode=ctx.research_mode,
            selected=[r for part in parts for r in part.selected],
            skipped=[r for part in parts for r in part.skipped],
        )

    @staticmethod
    def _put(conn: Any, kind: str, identifier: str, model: BaseModel) -> None:
        payload = model.model_dump_json()
        old = conn.execute(
            "SELECT payload FROM catalog WHERE kind=? AND id=?", [kind, identifier]
        ).fetchone()
        if old and old[0] != payload:
            raise PublicationError(f"immutable registry conflict: {kind}/{identifier}")
        conn.execute(
            "INSERT INTO catalog VALUES (?,?,?) ON CONFLICT DO NOTHING",
            [kind, identifier, payload],
        )

    @staticmethod
    def _get(conn: Any, kind: str, identifier: str) -> str:
        row = conn.execute(
            "SELECT payload FROM catalog WHERE kind=? AND id=?", [kind, identifier]
        ).fetchone()
        if row is None:
            raise PublicationError(f"unregistered {kind}: {identifier}")
        return str(row[0])

    def register_source(self, source: SourceDescriptor) -> None:
        with self.database.transaction() as conn:
            self._put(conn, "source", source.source_id, source)

    def register_series(self, series: SeriesSpec) -> None:
        with self.database.transaction() as conn:
            self._get(conn, "source", series.source_id)
            self._put(conn, "series", series.series_id, series)

    def register_taxonomy(
        self, nodes: Sequence[IndustryNode], indicators: Sequence[IndicatorSpec]
    ) -> None:
        with self.database.transaction() as conn:
            for node in nodes:
                self._put(conn, "industry", node.industry_id, node)
            for spec in indicators:
                for industry_id in spec.industry_ids:
                    self._get(conn, "industry", industry_id)
                self._put(conn, "indicator", spec.indicator_id, spec)

    def register_candidate(self, candidate: EvidenceCandidate) -> None:
        with self.database.transaction() as conn:
            self._put(conn, "candidate", candidate.candidate_id, candidate)

    def archive(self, artifact: RawArtifact, content: bytes) -> RawArtifact:
        digest = hashlib.sha256(content).hexdigest()
        if digest != artifact.sha256 or len(content) != artifact.byte_size:
            raise PublicationError("Bronze hash/byte_size mismatch")
        path = self.bronze_root / digest[:2] / digest
        archived = artifact.model_copy(update={"uri": str(path)})
        with self.database.transaction() as conn:
            source = SourceDescriptor.model_validate_json(
                self._get(conn, "source", artifact.source_id)
            )
            if source.legal_status in (
                LegalStatus.DENIED,
                LegalStatus.PENDING_REVIEW,
                LegalStatus.LICENSE_REQUIRED,
            ):
                raise PublicationError("source license does not permit archival")
            old = conn.execute(
                "SELECT payload FROM raw_artifact WHERE artifact_id=?", [artifact.artifact_id]
            ).fetchone()
            if old:
                # artifact_id 是内容哈希：同一字节的重复归档是幂等操作。
                # 只有"同 ID 不同内容/不同来源"才算冲突；retrieved_at、source_url 等
                # 捕获元数据保持首次记录——首次抓取时间才是诚实的时间证据。
                stored = RawArtifact.model_validate_json(old[0])
                if (stored.sha256, stored.byte_size, stored.source_id) != (
                    digest,
                    artifact.byte_size,
                    artifact.source_id,
                ):
                    raise PublicationError("immutable artifact_id conflict")
                return stored
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise PublicationError("archived Bronze bytes changed")
            else:
                with path.open("xb") as stream:
                    stream.write(content)
            conn.execute(
                "INSERT INTO raw_artifact VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                [artifact.artifact_id, artifact.source_id, digest, archived.model_dump_json()],
            )
        return archived

    def _validate(self, conn: Any, row: ObservationVintage) -> None:
        spec = SeriesSpec.model_validate_json(self._get(conn, "series", row.series_id))
        source = SourceDescriptor.model_validate_json(self._get(conn, "source", spec.source_id))
        if source.legal_status not in (
            LegalStatus.PUBLIC_OFFICIAL,
            LegalStatus.MANUAL_EXPORT_ALLOWED,
        ):
            raise PublicationError("source license does not permit publication")
        if (row.unit, row.unit_scale, row.currency) != (spec.unit, spec.unit_scale, spec.currency):
            raise PublicationError(f"unit/scale/currency mismatch: {row.series_id}")
        indicators = [
            IndicatorSpec.model_validate_json(r[0])
            for r in conn.execute("SELECT payload FROM catalog WHERE kind='indicator'").fetchall()
        ]
        matches = [i for i in indicators if row.series_id in i.series_ids]
        if not matches:
            raise PublicationError(f"missing industry/dimension mapping: {row.series_id}")
        for indicator in matches:
            for industry_id in indicator.industry_ids:
                node = IndustryNode.model_validate_json(self._get(conn, "industry", industry_id))
                if (
                    node.valid_from > row.observation_start
                    or (node.valid_to is not None and row.observation_start > node.valid_to)
                    or indicator.valid_from > row.observation_start
                ):
                    raise PublicationError(
                        "industry/indicator mapping not valid in observation period"
                    )
                if spec.industry_standard_version and (
                    spec.industry_standard_version != node.standard.value
                ):
                    raise PublicationError("industry standard mismatch")
        if row.artifact_id is None:
            raise PublicationError("observation has no Bronze ancestor")
        found = conn.execute(
            "SELECT payload FROM raw_artifact WHERE artifact_id=?", [row.artifact_id]
        ).fetchone()
        if found is None:
            raise PublicationError("dangling artifact reference")
        artifact = RawArtifact.model_validate_json(found[0])
        if artifact.source_id != spec.source_id:
            raise PublicationError("series/artifact source mismatch")
        path = Path(artifact.uri)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256:
            raise PublicationError("missing or corrupted Bronze artifact")
        if row.evidence_candidate_id:
            candidate = EvidenceCandidate.model_validate_json(
                self._get(conn, "candidate", row.evidence_candidate_id)
            )
            if candidate.verification_status != VerificationStatus.VERIFIED:
                raise PublicationError("candidate not verified")
            if candidate.raw_sha256 != artifact.sha256:
                raise PublicationError("candidate hash does not match Bronze")
            if (candidate.parsed_value, candidate.unit) != (row.value, row.unit):
                raise PublicationError("candidate value/unit differs from observation")

    def publish(self, snapshot_id: str, rows: Sequence[ObservationVintage]) -> str:
        """Validate staging and publish all rows in one transaction; idempotent by content."""
        if not snapshot_id or not rows:
            raise PublicationError("snapshot ID and nonempty staging batch required")
        ordered = sorted(rows, key=lambda r: (r.series_id, r.observation_start, r.vintage_id))
        payloads = [r.model_dump_json() for r in ordered]
        digest = hashlib.sha256(json.dumps(payloads).encode()).hexdigest()
        with self.database.transaction() as conn:
            old = conn.execute(
                "SELECT sha256 FROM publication WHERE snapshot_id=?", [snapshot_id]
            ).fetchone()
            if old:
                if old[0] != digest:
                    raise PublicationError("immutable snapshot_id conflict")
                return digest
            keys: set[tuple[str, date, int]] = set()
            for row in ordered:
                key = (row.series_id, row.observation_start, row.vintage_id)
                if key in keys:
                    raise PublicationError("duplicate vintage key in staging")
                keys.add(key)
                self._validate(conn, row)
                existing = conn.execute(
                    "SELECT payload FROM observation_vintage WHERE series_id=? "
                    "AND observation_start=? AND vintage_id=?",
                    list(key),
                ).fetchone()
                if existing and existing[0] != row.model_dump_json():
                    raise PublicationError("immutable vintage conflict")
                conn.execute(
                    "INSERT INTO observation_vintage VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                    [*key, row.model_dump_json()],
                )
                conn.execute("INSERT INTO publication_row VALUES (?,?,?,?)", [snapshot_id, *key])
            conn.execute("INSERT INTO publication VALUES (?,?)", [snapshot_id, digest])
        return digest

    def vintages_for(
        self, series_id: str, observation_start: date
    ) -> tuple[ObservationVintage, ...]:
        """某序列-所属期已知的全部版本，按 vintage_id 升序。

        摄取层用它做两件事：判断重放是否幂等（内容签名一致则复用），
        以及为修订分配下一个 vintage_id。也是 G2 D-012 修订差异报告的基础。
        """
        with self.database.transaction() as conn:
            rows = conn.execute(
                "SELECT payload FROM observation_vintage WHERE series_id=? "
                "AND observation_start=? ORDER BY vintage_id",
                [series_id, observation_start],
            ).fetchall()
        return tuple(ObservationVintage.model_validate_json(row[0]) for row in rows)

    def all_vintages(
        self, series_ids: Sequence[str], *, snapshot_id: str | None = None
    ) -> list[ObservationVintage]:
        """这些序列的全部版本（不分所属期、不做 as-of 过滤）。

        修订可见性重放需要原始版本全集：先取全量，再按模式在外面套
        as-of 选择，避免按 (序列, 所属期) 逐对查询。
        """
        if not series_ids:
            return []
        with self.database.transaction() as conn:
            marks = ",".join("?" for _ in series_ids)
            params: list[Any] = list(series_ids)
            snapshot_filter = ""
            if snapshot_id is not None:
                if not conn.execute(
                    "SELECT 1 FROM publication WHERE snapshot_id=?", [snapshot_id]
                ).fetchone():
                    raise PublicationError("unregistered frozen dataset snapshot")
                snapshot_filter = (
                    "AND (series_id,observation_start,vintage_id) IN "
                    "(SELECT series_id,observation_start,vintage_id FROM publication_row "
                    "WHERE snapshot_id=?) "
                )
                params.append(snapshot_id)
            rows = conn.execute(
                f"SELECT payload FROM observation_vintage WHERE series_id IN ({marks}) "
                + snapshot_filter
                + "ORDER BY series_id,observation_start,vintage_id",
                params,
            ).fetchall()
        return [ObservationVintage.model_validate_json(row[0]) for row in rows]

    def next_vintage_id(self, series_id: str, observation_start: date) -> int:
        """下一个单调递增的 vintage_id（修订只新增、绝不改写旧行，§5.5）。"""
        existing = self.vintages_for(series_id, observation_start)
        return max((item.vintage_id for item in existing), default=0) + 1

    def series_spec(self, series_id: str) -> SeriesSpec:
        """读取已登记的序列规格；未登记则抛 PublicationError（不返回默认值）。"""
        with self.database.transaction() as conn:
            return SeriesSpec.model_validate_json(self._get(conn, "series", series_id))

    def source_descriptor(self, source_id: str) -> SourceDescriptor:
        """读取已登记的数据源描述符（含许可状态）。"""
        with self.database.transaction() as conn:
            return SourceDescriptor.model_validate_json(self._get(conn, "source", source_id))

    def select(self, series_ids: Sequence[str], ctx: AnalysisContextLike) -> AsOfSelection:
        if not series_ids:
            raise ValueError("at least one series ID required")
        with self.database.transaction() as conn:
            marks = ",".join("?" for _ in series_ids)
            snapshot_id = getattr(ctx, "dataset_snapshot_id", None)
            snapshot_filter = ""
            params: list[Any] = list(series_ids)
            if snapshot_id is not None:
                if not conn.execute(
                    "SELECT 1 FROM publication WHERE snapshot_id=?", [snapshot_id]
                ).fetchone():
                    raise PublicationError("unregistered frozen dataset snapshot")
                snapshot_filter = (
                    "AND (series_id,observation_start,vintage_id) IN "
                    "(SELECT series_id,observation_start,vintage_id FROM publication_row "
                    "WHERE snapshot_id=?) "
                )
                params.append(snapshot_id)
            rows = [
                ObservationVintage.model_validate_json(r[0])
                for r in conn.execute(
                    f"SELECT payload FROM observation_vintage WHERE series_id IN ({marks}) "
                    + snapshot_filter
                    + "ORDER BY series_id,observation_start,vintage_id",
                    params,
                ).fetchall()
            ]
            # Apply frozen gates and availability, then execute the production SQL window.
            reference = self._selection(rows, ctx)
            rejected = {(s.series_id, s.observation_start, s.vintage_id) for s in reference.skipped}
            eligible = [
                r for r in rows if (r.series_id, r.observation_start, r.vintage_id) not in rejected
            ]
            conn.execute(
                "CREATE TEMP TABLE pit_selection(series_id VARCHAR, period DATE, "
                "vintage_id BIGINT, effective_at TIMESTAMPTZ, payload VARCHAR)"
            )
            if eligible:
                conn.executemany(
                    "INSERT INTO pit_selection VALUES (?,?,?,?,?)",
                    [
                        [
                            r.series_id,
                            r.observation_start,
                            r.vintage_id,
                            self._effective(r, ctx.research_mode),
                            r.model_dump_json(),
                        ]
                        for r in eligible
                    ],
                )
            selected = [
                ObservationVintage.model_validate_json(r[0])
                for r in conn.execute(
                    "SELECT payload FROM pit_selection QUALIFY row_number() OVER "
                    "(PARTITION BY series_id,period ORDER BY effective_at DESC,vintage_id DESC)=1 "
                    "ORDER BY series_id,period,vintage_id"
                ).fetchall()
            ]
            conn.execute("DROP TABLE pit_selection")
            return reference.model_copy(update={"selected": selected})

    def fetch(self, series_ids: Sequence[str], ctx: AnalysisContextLike) -> ObservationFrame:
        selection = self.select(series_ids, ctx)
        return ObservationFrame(
            series_ids=list(series_ids),
            as_of=ctx.as_of,
            research_mode=ctx.research_mode.value,
            vintages=selection.selected,
        )

    def query_metadata(self, mode: ResearchMode) -> dict[str, str | int | bool]:
        return {
            "research_mode": mode.value,
            "policy_version": self.policy_version,
            "conservative_lag_days": self.conservative_lag_days,
            "pit_verified": mode == ResearchMode.STRICT_PIT,
            "watermark": "not PIT verified; NOT investment proof"
            if mode == ResearchMode.EXPLORATORY
            else "",
        }

    def query(self, series_ids: Sequence[str], ctx: AnalysisContextLike) -> RepositoryQuery:
        """Auditable query result with mandatory EXPLORATORY watermark and assumption version."""
        return RepositoryQuery(
            selection=self.select(series_ids, ctx),
            policy_version=self.policy_version,
            conservative_lag_days=self.conservative_lag_days,
            series_lag_days=dict(self.series_lag_days),
            pit_verified=ctx.research_mode == ResearchMode.STRICT_PIT,
            watermark="not PIT verified; NOT investment proof"
            if ctx.research_mode == ResearchMode.EXPLORATORY
            else "",
        )

    def record_derived_ancestry(
        self,
        feature_id: str,
        parent_features: Sequence[str],
        direct_ancestors: Sequence[tuple[str, date, int]] = (),
    ) -> None:
        """Freeze the transitive leaf closure of all parent features and direct inputs."""
        ancestors = set(direct_ancestors)
        with self.database.transaction() as conn:
            for parent in parent_features:
                if parent == feature_id:
                    raise PublicationError("feature cannot depend on itself")
                row = conn.execute(
                    "SELECT payload FROM ancestry WHERE feature_id=?", [parent]
                ).fetchone()
                if row is None:
                    raise PublicationError("dangling parent feature")
                ancestors.update((s, date.fromisoformat(d), v) for s, d, v in json.loads(row[0]))
        self.record_ancestry(feature_id, sorted(ancestors))

    def record_ancestry(self, feature_id: str, ancestors: Sequence[tuple[str, date, int]]) -> None:
        if not feature_id or not ancestors:
            raise PublicationError("feature and all input ancestors required")
        payload = json.dumps([[s, d.isoformat(), v] for s, d, v in sorted(set(ancestors))])
        with self.database.transaction() as conn:
            for key in ancestors:
                if not conn.execute(
                    "SELECT 1 FROM observation_vintage WHERE series_id=? "
                    "AND observation_start=? AND vintage_id=?",
                    list(key),
                ).fetchone():
                    raise PublicationError("dangling feature ancestor")
            old = conn.execute("SELECT payload FROM ancestry WHERE feature_id=?", [feature_id])
            found = old.fetchone()
            if found and found[0] != payload:
                raise PublicationError("immutable feature ancestry conflict")
            conn.execute(
                "INSERT INTO ancestry VALUES (?,?) ON CONFLICT DO NOTHING", [feature_id, payload]
            )

    def ancestry(
        self, feature_id: str, ctx: AnalysisContextLike
    ) -> tuple[list[ObservationVintage], datetime]:
        with self.database.transaction() as conn:
            found = conn.execute("SELECT payload FROM ancestry WHERE feature_id=?", [feature_id])
            item = found.fetchone()
            if item is None:
                raise PublicationError("unregistered feature ancestry")
            rows = []
            for series, period, vintage in json.loads(item[0]):
                snapshot_id = getattr(ctx, "dataset_snapshot_id", None)
                if (
                    snapshot_id is not None
                    and not conn.execute(
                        "SELECT 1 FROM publication_row WHERE snapshot_id=? AND series_id=? "
                        "AND observation_start=? AND vintage_id=?",
                        [snapshot_id, series, date.fromisoformat(period), vintage],
                    ).fetchone()
                ):
                    raise PublicationError("ancestor not present in frozen dataset snapshot")
                record = conn.execute(
                    "SELECT payload FROM observation_vintage WHERE series_id=? "
                    "AND observation_start=? AND vintage_id=?",
                    [series, date.fromisoformat(period), vintage],
                ).fetchone()
                rows.append(ObservationVintage.model_validate_json(record[0]))
        selection = self._selection(rows, ctx)
        if selection.skipped:
            raise PublicationError("feature ancestor unavailable under requested PIT mode")
        latest = max(self._effective(r, ctx.research_mode) for r in rows)
        return rows, latest
