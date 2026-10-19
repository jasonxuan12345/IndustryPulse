"""组合根摄取链：解析后的官方文档 -> 不可变 Bronze -> 原子 Silver 发布。

P0 之前这条链只存在于测试里：``tests/integration/test_g1_pipeline.py`` 手工写了几十行
注册/归档/发布样板，仓库里没有任何可复用的摄取入口。本模块把它收敛成一个动作：

    解析结果 + 原件字节
        -> repository.archive()    许可、哈希、字节数、不可变 Bronze、内容寻址去重
        -> 依 SeriesSpec 构造 ObservationVintage   证据分级、可用时点、单位校验
        -> repository.publish()    单事务、全部或全无、snapshot 内容寻址幂等

三条纪律（与 §6.2/§6.4 一致，任何一条都不允许为了"多出数据"而放宽）：

1. **不猜发布时点**。没有 ``publication_at`` 且 SeriesSpec 未声明 ``release_policy`` 时，
   可用时点只能取 ``retrieved_at`` 并标 ``RETRIEVAL_TIME``；STRICT/RECONSTRUCTED
   会把它排除（``BASIS_NOT_ALLOWED``），这是正确的，不是缺陷。
2. **不自动升格证据**。``value_evidence`` 默认 C（"今天取到的现行修订值"），
   只有调用方显式主张 ``contemporary=True``（已核验是同期原件）才可能到 A/B。
3. **重放幂等**。同一份原件重复摄取时内容签名一致则复用既有 vintage，不新增版本；
   修订必须产生新 ``vintage_id``，旧行永不被改写。

``ingest_batch`` 是 O-006（离线批量历史公告回填）的落地：多份公告先各自归档并构造
版本，最后**一次性**发布到同一个 snapshot，从而得到可跨期回放的冻结信息集。
"""

from __future__ import annotations

import hashlib
import json
import re
from calendar import monthrange
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import Field

from industrypulse.contracts.common import ContractBaseModel, NonEmptyStr, Sha256Hex
from industrypulse.contracts.observation import AvailabilityBasis, ObservationVintage, RawArtifact
from industrypulse.contracts.sources import SeriesSpec
from industrypulse.core.evidence import release_grade, value_grade
from industrypulse.datahub.adapters import ParsedObservation
from industrypulse.repositories import DuckDBObservationRepository

_PERIOD = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")

#: 未声明 intraday_time 时的默认发布时刻（§6.4 只知日期时统一 09:30 口径）。
_DEFAULT_RELEASE_TIME = time(9, 30)


class IngestError(ValueError):
    """未发布任何数据；Bronze 可能已合法保留原件（只增不改的预期行为）。"""


class IngestReport(ContractBaseModel):
    """一次文档摄取的审计结果：用了哪份原件、产生了哪几个版本。"""

    snapshot_id: NonEmptyStr
    digest: Sha256Hex
    artifact_id: NonEmptyStr
    created: Annotated[int, Field(ge=0)]
    reused: Annotated[int, Field(ge=0)]
    vintages: Annotated[list[ObservationVintage], Field(min_length=1)]


class BatchIngestReport(ContractBaseModel):
    """批量回填结果：一个冻结快照 + 多份原件 + 全部版本。"""

    snapshot_id: NonEmptyStr
    digest: Sha256Hex
    artifact_ids: Annotated[list[NonEmptyStr], Field(min_length=1)]
    created: Annotated[int, Field(ge=0)]
    reused: Annotated[int, Field(ge=0)]
    vintages: Annotated[list[ObservationVintage], Field(min_length=1)]


@dataclass(frozen=True)
class BatchItem:
    """批量回填的一份文档：原件 + 字节 + 解析结果 + 相关序列规格。"""

    artifact: RawArtifact
    content: bytes
    parsed: Sequence[ParsedObservation]
    specs: Mapping[str, SeriesSpec]


def period_bounds(period: str) -> tuple[date, date]:
    """``YYYY-MM`` -> (所属期首日, 所属期末日)。拒绝任何模糊写法。"""
    match = _PERIOD.fullmatch(period.strip())
    if match is None:
        raise IngestError(f"所属期必须是 YYYY-MM：{period!r}")
    year, month = int(match.group(1)), int(match.group(2))
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


class IngestService:
    """把"一份已解析的官方文档"变成可 PIT 查询的 Silver 观测。"""

    def __init__(
        self, repository: DuckDBObservationRepository, *, parser_version: str = "g2-v1"
    ) -> None:
        if not parser_version.strip():
            raise IngestError("parser_version 必须显式声明（幂等键的组成部分）")
        self.repository = repository
        self.parser_version = parser_version

    def snapshot_id(
        self,
        *,
        source_id: str,
        period: str,
        content: bytes,
        series_ids: Sequence[str] = (),
        contemporary: bool = False,
        trusted_copy: bool = False,
        policy_date: bool = False,
    ) -> str:
        """摄取参数指纹：同一字节 + 同一选项重放得到同一 ID。

        选项（解析器版本、证据主张、涉及的序列）必须进入指纹，否则"同一原件
        重新定级"会撞上不可变快照冲突。重新定级本就应留下新版本而不是改写旧快照。
        """
        options = json.dumps(
            {
                "parser_version": self.parser_version,
                "series": sorted(set(series_ids)),
                "contemporary": contemporary,
                "trusted_copy": trusted_copy,
                "policy_date": policy_date,
            },
            sort_keys=True,
        ).encode()
        digest = hashlib.sha256(content + options).hexdigest()
        return f"{source_id}:{period}:{digest[:16]}"

    def _stage(
        self,
        *,
        artifact: RawArtifact,
        content: bytes,
        parsed: Sequence[ParsedObservation],
        specs: Mapping[str, SeriesSpec],
        contemporary: bool,
        trusted_copy: bool,
        policy_date: bool,
    ) -> tuple[RawArtifact, list[ObservationVintage], int, int]:
        """归档原件并构造版本（不发布）。供单文档与批量两条路径共用。"""
        if not parsed:
            raise IngestError("没有任何解析结果；拒绝发布空文档")
        archived = self.repository.archive(artifact, content)
        built: list[ObservationVintage] = []
        created = reused = 0
        for item in parsed:
            spec = specs.get(item.series_id)
            if spec is None:
                raise IngestError(f"未登记 SeriesSpec：{item.series_id}（先运行 init）")
            candidate = self._build(
                item,
                spec,
                archived,
                vintage_id=1,
                contemporary=contemporary,
                trusted_copy=trusted_copy,
                policy_date=policy_date,
            )
            existing = self.repository.vintages_for(
                candidate.series_id, candidate.observation_start
            )
            signature = self._signature(candidate)
            match = next((row for row in existing if self._signature(row) == signature), None)
            if match is not None:
                built.append(match)
                reused += 1
                continue
            next_id = max((row.vintage_id for row in existing), default=0) + 1
            built.append(candidate.model_copy(update={"vintage_id": next_id}))
            created += 1
        return archived, built, created, reused

    def ingest_document(
        self,
        *,
        artifact: RawArtifact,
        content: bytes,
        parsed: Sequence[ParsedObservation],
        specs: Mapping[str, SeriesSpec],
        snapshot_id: str | None = None,
        contemporary: bool = False,
        trusted_copy: bool = False,
        policy_date: bool = False,
    ) -> IngestReport:
        """归档原件并发布该文档的全部观测；任一行失败则全部回滚。

        ``contemporary`` 只有在人工核验过"这份文件是当期原件而非现行修订版"后才能置 True。
        """
        archived, built, created, reused = self._stage(
            artifact=artifact,
            content=content,
            parsed=parsed,
            specs=specs,
            contemporary=contemporary,
            trusted_copy=trusted_copy,
            policy_date=policy_date,
        )
        periods = sorted({item.period for item in parsed})
        label = periods[0] if len(periods) == 1 else f"{periods[0]}..{periods[-1]}"
        snapshot = snapshot_id or self.snapshot_id(
            source_id=artifact.source_id,
            period=label,
            content=content,
            series_ids=[item.series_id for item in parsed],
            contemporary=contemporary,
            trusted_copy=trusted_copy,
            policy_date=policy_date,
        )
        digest = self.repository.publish(snapshot, built)
        return IngestReport(
            snapshot_id=snapshot,
            digest=digest,
            artifact_id=archived.artifact_id,
            created=created,
            reused=reused,
            vintages=built,
        )

    def ingest_batch(
        self,
        items: Sequence[BatchItem],
        *,
        snapshot_id: str,
        contemporary: bool = False,
        trusted_copy: bool = False,
        policy_date: bool = False,
    ) -> BatchIngestReport:
        """批量回填：多份公告 -> 同一个冻结快照（O-006）。

        先在内存中把全部文档归档并构造版本，最后一次性 ``publish``；任何一份失败
        都不会产生部分快照。快照 ID 必须由调用方给出（它代表"这一批是什么"）。
        """
        if not items:
            raise IngestError("批量回填至少需要一份文档")
        if not snapshot_id.strip():
            raise IngestError("批量回填必须显式给出冻结快照 ID")
        artifact_ids: list[str] = []
        staged: dict[tuple[str, date, int], ObservationVintage] = {}
        created = reused = 0
        for item in items:
            archived, built, item_created, item_reused = self._stage(
                artifact=item.artifact,
                content=item.content,
                parsed=item.parsed,
                specs=item.specs,
                contemporary=contemporary,
                trusted_copy=trusted_copy,
                policy_date=policy_date,
            )
            artifact_ids.append(archived.artifact_id)
            created += item_created
            reused += item_reused
            for row in built:
                staged.setdefault((row.series_id, row.observation_start, row.vintage_id), row)
        rows = [staged[key] for key in sorted(staged)]
        digest = self.repository.publish(snapshot_id, rows)
        return BatchIngestReport(
            snapshot_id=snapshot_id,
            digest=digest,
            artifact_ids=artifact_ids,
            created=created,
            reused=reused,
            vintages=rows,
        )

    @staticmethod
    def _signature(row: ObservationVintage) -> tuple[object, ...]:
        """内容指纹：刻意不含 vintage_id 与 notes，使措辞变化不会被误判为修订。"""
        return (
            row.series_id,
            row.observation_start,
            row.value,
            row.missing_reason,
            row.unit,
            row.unit_scale,
            row.currency,
            row.publication_at,
            row.available_at,
            row.availability_basis,
            row.release_evidence,
            row.value_evidence,
            row.artifact_id,
            row.source_url,
        )

    def _build(
        self,
        parsed: ParsedObservation,
        spec: SeriesSpec,
        artifact: RawArtifact,
        *,
        vintage_id: int,
        contemporary: bool,
        trusted_copy: bool,
        policy_date: bool,
    ) -> ObservationVintage:
        if parsed.series_id != spec.series_id:
            raise IngestError(f"解析序列 {parsed.series_id} 与规格 {spec.series_id} 不符")
        if parsed.unit != spec.unit:
            raise IngestError(
                f"单位不一致：解析得到 {parsed.unit!r}，SeriesSpec 声明 {spec.unit!r}；禁止静默换算"
            )
        if parsed.observation_start is not None and parsed.observation_end is not None:
            # 日频等非月度序列：按真实所属期边界，不按月近似
            start, end = parsed.observation_start, parsed.observation_end
        else:
            start, end = period_bounds(parsed.period)
        retrieved_at = parsed.retrieved_at or artifact.retrieved_at
        publication_at = parsed.publication_at
        policy = spec.release_policy
        if publication_at is not None:
            basis = AvailabilityBasis.OBSERVED_PUBLICATION
            available_at = publication_at
        elif policy is not None:
            basis = AvailabilityBasis.ASSUMED_POLICY_LAG
            zone = ZoneInfo(policy.timezone)
            estimate = datetime.combine(
                end + timedelta(days=policy.release_lag_days + policy.safety_days),
                policy.intraday_time or _DEFAULT_RELEASE_TIME,
                tzinfo=zone,
            )
            # 政策滞后是"通常发布日"上界；实际抓取时点是更紧的上界。
            available_at = min(estimate, retrieved_at)
        else:
            basis = AvailabilityBasis.RETRIEVAL_TIME
            available_at = retrieved_at
        release = release_grade(
            has_publication=publication_at is not None,
            precision=parsed.timestamp_precision,
            policy_date=policy_date or (policy is not None and publication_at is None),
        )
        value = value_grade(
            has_value=parsed.value is not None,
            contemporary=contemporary,
            has_raw_hash=bool(artifact.sha256),
            has_locator=bool(parsed.locator),
            trusted_copy=trusted_copy,
        )
        return ObservationVintage(
            vintage_id=vintage_id,
            series_id=parsed.series_id,
            observation_start=start,
            observation_end=end,
            value=parsed.value,
            missing_reason=parsed.missing_reason,
            unit=spec.unit,
            unit_scale=spec.unit_scale,
            currency=spec.currency,
            publication_at=publication_at,
            retrieved_at=retrieved_at,
            available_at=available_at,
            availability_basis=basis,
            vintage_valid_from=available_at,
            release_evidence=release,
            value_evidence=value,
            artifact_id=artifact.artifact_id,
            source_url=artifact.source_url,
            notes=f"ingested via {self.parser_version}; locator={parsed.locator}",
        )


__all__ = [
    "BatchIngestReport",
    "BatchItem",
    "IngestError",
    "IngestReport",
    "IngestService",
    "period_bounds",
]
