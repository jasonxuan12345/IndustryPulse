"""C-003 观测值 / 历史版本 / 证据合同。

对应任务书 §6.1（六时间属性）、§6.2（发布/数值双证据评级）、§7.2
（observation_vintage / evidence_candidate / raw_artifact 逻辑表）、§5.4
（EvidenceCandidate 字段清单）。

本模块只定义数据形状与跨字段不变式；as-of 选择语义见 :mod:`.pit`
（D 线仓储层必须与其保持一致，SQL 见 §7.3）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import Field, model_validator

from .common import AwareDatetime, ContractBaseModel, LowerId, NonEmptyStr, Sha256Hex, UrlStr
from .sources import Frequency


class EvidenceGrade(StrEnum):
    """证据等级（§6.2）：A/B/C/D 是证据强度，不是模型置信概率。

    发布证据与数值证据分别评级；STRICT 要求两者 ≥B。
    """

    A = "A"  # 载明发布时间的同期官方网页/档案；同期原始数字+定位+哈希
    B = "B"  # 当时官方日期但无时分；同期数值由可信副本复核
    C = "C"  # 依统计发布日历推算；今天获取的最新修订历史值
    D = "D"  # 无可靠时点；无真实数值


class MissingReason(StrEnum):
    """缺失原因（§7.5）：禁止把 0 当缺失，缺失必须携带原因。"""

    NOT_YET_PUBLISHED = "not_yet_published"
    NOT_COLLECTED = "not_collected"
    WITHDRAWN_BY_PUBLISHER = "withdrawn_by_publisher"
    LICENSE_NOT_GRANTED = "license_not_granted"
    FETCH_FAILED = "fetch_failed"
    PARSE_FAILED = "parse_failed"
    UNKNOWN = "unknown"


class AvailabilityBasis(StrEnum):
    """available_at 的语义依据（§6.1/§6.4）。

    - OBSERVED_PUBLICATION：有可核验的同期发布时点证据；
    - ASSUMED_POLICY_LAG：按发布日历保守推算（"通常月底发布"类）；
    - RETRIEVAL_TIME：只知本系统抓取时点——历史 as-of 查询一律不可见。
    """

    OBSERVED_PUBLICATION = "observed_publication"
    ASSUMED_POLICY_LAG = "assumed_policy_lag"
    RETRIEVAL_TIME = "retrieval_time"


class VerificationStatus(StrEnum):
    """AI/规则候选的核验状态（§5.2/§5.6 提示词合同）。"""

    VERIFIED = "verified"
    NEEDS_REVIEW = "needs_review"  # 冲突/低置信 → 人工审查队列（§5.5）
    FETCH_FAILED = "fetch_failed"  # 仅搜索摘要不得写入事实表
    RELEASE_UNVERIFIED = "release_unverified"  # 发布时间不明
    VALUE_VINTAGE_UNVERIFIED = "value_vintage_unverified"  # 只得现行修订值
    REJECTED = "rejected"


class EvidenceClass(StrEnum):
    """证据类别（§5.3 回退层次）：snippet 类只作线索，不作数值证据。"""

    OFFICIAL_PAGE = "official_page"
    OFFICIAL_ATTACHMENT = "official_attachment"
    OFFICIAL_MIRROR = "official_mirror"  # 须对照源链接
    OFFICIAL_NEWSLETTER = "official_newsletter"
    AUTHORIZED_EXPORT = "authorized_export"
    MANUAL_UPLOAD = "manual_upload"
    SEARCH_SNIPPET = "search_snippet"


class ExtractionEngine(StrEnum):
    """抽取引擎（§5.1：确定性解析优先，LLM 仅结构不规则时辅助）。"""

    DETERMINISTIC_HTML = "deterministic_html"
    DETERMINISTIC_TABLE = "deterministic_table"
    LLM_ASSISTED = "llm_assisted"  # 必须附片段定位并经确定性验证或人工复核
    MANUAL = "manual"


class RawArtifact(ContractBaseModel):
    """原始工件归档（§7.2 raw_artifact）：原件字节不覆写，只增不改。"""

    artifact_id: NonEmptyStr
    source_id: LowerId
    uri: NonEmptyStr  # bronze 层存储位置
    sha256: Sha256Hex
    mime: NonEmptyStr
    byte_size: Annotated[int, Field(ge=0)]
    retrieved_at: AwareDatetime
    source_url: UrlStr | None = None
    discovery_url: UrlStr | None = None  # §5.2：与 source_url 分开记录
    http_headers: dict[str, str] = Field(default_factory=dict)


class ObservationVintage(ContractBaseModel):
    """单个序列-所属期的一条数值版本（§7.2 observation_vintage）。

    六时间属性（§6.1）落位：``observation_start/end``（经济所属期）、
    ``publication_at``（官方公开时点）、``source_known_at``（公众可知时点）、
    ``retrieved_at``（本系统抓取时点——回填晚于发布是正常的）、
    ``vintage_valid_from/to``（本版本有效区间）；
    ``decision_as_of`` 属于 AnalysisContext，不在行上。

    修订不改旧行：任何修订新增一行、``vintage_id`` 递增（§5.5）。
    同一 (series_id, observation_start) 的多版本共存是本合同的核心不变式。
    """

    vintage_id: Annotated[int, Field(ge=1)]  # 同 series+period 内单调递增
    series_id: LowerId
    observation_start: date
    observation_end: date
    # —— 数值（§7.5）——
    value: Decimal | None
    missing_reason: MissingReason | None = None
    unit: NonEmptyStr
    unit_scale: Decimal = Decimal("1")
    currency: NonEmptyStr = "CNY"
    # —— 六时间属性中的五行 ——
    publication_at: AwareDatetime | None = None  # None ⇒ 发布时点未知
    source_known_at: AwareDatetime | None = None  # 缺省 = available_at
    retrieved_at: AwareDatetime
    available_at: AwareDatetime  # 本数值版本的可知时点（语义由 basis 决定）
    availability_basis: AvailabilityBasis
    vintage_valid_from: AwareDatetime
    vintage_valid_to: AwareDatetime | None = None  # None = 仍是现行版本
    # —— 双证据评级（§6.2）——
    release_evidence: EvidenceGrade
    value_evidence: EvidenceGrade
    # —— 溯源 ——
    artifact_id: NonEmptyStr | None = None
    evidence_candidate_id: NonEmptyStr | None = None
    source_url: UrlStr | None = None
    notes: str = ""

    @model_validator(mode="before")
    @classmethod
    def _default_source_known_at(cls, data: object) -> object:
        # frozen 模型不能在 after 阶段赋默认值，故在 before 阶段补齐
        if isinstance(data, dict) and data.get("source_known_at") is None:
            known = data.get("available_at")
            if known is not None:
                data = {**data, "source_known_at": known}
        return data

    @model_validator(mode="after")
    def _invariants(self) -> ObservationVintage:
        if self.observation_start > self.observation_end:
            raise ValueError("observation_start 不得晚于 observation_end")
        if (self.value is None) == (self.missing_reason is None):
            raise ValueError("value 与 missing_reason 必须恰好一者存在（禁止 0 当缺失）")
        if self.publication_at is None:
            if self.availability_basis == AvailabilityBasis.OBSERVED_PUBLICATION:
                raise ValueError("无发布时点证据不得声称 OBSERVED_PUBLICATION")
            if self.release_evidence in (EvidenceGrade.A, EvidenceGrade.B):
                raise ValueError("A/B 级发布证据必须有 publication_at")
        else:
            if self.availability_basis != AvailabilityBasis.OBSERVED_PUBLICATION:
                raise ValueError("有 publication_at 的行 basis 必须为 OBSERVED_PUBLICATION")
            if self.available_at < self.publication_at:
                raise ValueError("available_at 不得早于 publication_at")
        if self.release_evidence == EvidenceGrade.D and self.publication_at is not None:
            raise ValueError("D 级发布证据不得携带 publication_at")
        if self.vintage_valid_to is not None and self.vintage_valid_to < self.vintage_valid_from:
            raise ValueError("vintage_valid_to 不得早于 vintage_valid_from")
        return self

    @property
    def period_label(self) -> str:
        """所属期展示标签（YYYY-MM；日/旬序列取 observation_start 的年月）。"""
        return f"{self.observation_start.year:04d}-{self.observation_start.month:02d}"


class ResearchQuery(ContractBaseModel):
    """AI 检索任务（§5.2 步骤 1）：可复现的最小检索单元。"""

    query_id: NonEmptyStr
    series_id: LowerId
    observation_period: NonEmptyStr  # 如 "2025-07"
    target_source_host: NonEmptyStr  # 如 "stats.gov.cn"
    template_version: NonEmptyStr
    created_at: AwareDatetime
    max_results: Annotated[int, Field(gt=0)] = 20
    allowed_domains: Annotated[list[NonEmptyStr], Field(min_length=1)]


class EvidenceCandidate(ContractBaseModel):
    """证据候选（§5.4 全字段清单）：AI/规则抽取的输出，未审核不得进 Silver（A-012）。

    找不到的字段输出 null + missing_reason；严禁输出看似合理的数字。
    """

    candidate_id: NonEmptyStr
    query_id: NonEmptyStr
    source_url: UrlStr
    discovery_url: UrlStr | None = None
    publisher: NonEmptyStr
    document_title: NonEmptyStr
    publication_at: AwareDatetime | None
    retrieved_at: AwareDatetime
    raw_sha256: Sha256Hex | None = None  # FETCH_FAILED 时必须为 None
    table_locator: NonEmptyStr | None = None  # HTML table path / 工作表!单元格
    row_label: NonEmptyStr | None = None
    column_label: NonEmptyStr | None = None
    observed_period: NonEmptyStr | None = None
    raw_text: NonEmptyStr | None = None  # 原文片段（证据定位）
    parsed_value: Decimal | None = None
    unit: NonEmptyStr | None = None
    missing_reason: MissingReason | None = None
    extraction_engine: ExtractionEngine
    evidence_class: EvidenceClass
    verification_status: VerificationStatus
    reviewer: NonEmptyStr | None = None
    notes: str = ""

    @model_validator(mode="after")
    def _invariants(self) -> EvidenceCandidate:
        if self.verification_status == VerificationStatus.FETCH_FAILED:
            if self.raw_sha256 is not None or self.parsed_value is not None:
                raise ValueError("FETCH_FAILED 候选不得携带原件哈希或数值（§5.2 步骤4）")
        if self.verification_status == VerificationStatus.VERIFIED:
            if self.raw_sha256 is None:
                raise ValueError("VERIFIED 候选必须有原件哈希")
            if self.evidence_class == EvidenceClass.SEARCH_SNIPPET:
                raise ValueError("搜索摘要不可作为数值证据（§5.3）")
        if self.parsed_value is not None:
            if self.raw_text is None:
                raise ValueError("parsed_value 必须附原文片段定位（§5.4）")
            if self.unit is None:
                raise ValueError("parsed_value 必须附单位")
        if self.raw_text is None and self.missing_reason is None:
            raise ValueError("无法定位字段时必须给出 missing_reason，不得编造（§5.4）")
        return self


class ObservationFrame(ContractBaseModel):
    """as-of 查询返回的观测集合（§7.4 ObservationRepository.fetch 的返回形状）。"""

    series_ids: Annotated[list[LowerId], Field(min_length=1)]
    as_of: AwareDatetime
    research_mode: str  # ResearchMode 枚举值；避免与 context 模块循环引用
    frequency_hint: Frequency | None = None
    vintages: list[ObservationVintage] = Field(default_factory=list)

    @model_validator(mode="after")
    def _series_consistent(self) -> ObservationFrame:
        covered = {v.series_id for v in self.vintages}
        if not covered <= set(self.series_ids):
            raise ValueError("返回行包含未请求的 series_id")
        return self
