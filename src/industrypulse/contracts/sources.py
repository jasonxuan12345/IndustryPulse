"""C-002 数据源与指标序列合同（SourceDescriptor / SeriesSpec）。

对应任务书 §4.3（SourceDescriptor 字段清单）、§7.2（series_registry）、
§7.5（输入输出语义字段）。这两类合同是 S 线与 D 线的共同边界。
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import Field, model_validator

from .common import AwareDatetime, ContractBaseModel, LowerId, NonEmptyStr, UrlStr


class Frequency(StrEnum):
    """发布频率（§4.1 表）。EVENT 指事件文本，不构成数值序列。"""

    DAILY = "D"
    WEEKLY = "W"
    TEN_DAY = "10D"  # 旬度（中钢协会员数据）
    MONTHLY = "M"
    QUARTERLY = "Q"
    EVENT = "EVENT"
    IRREGULAR = "IRREGULAR"


class SourcePriority(StrEnum):
    """数据源分层（§4.1）：S0 必须首批可运行；S3 事件/付费候选不作核心硬依赖。"""

    S0 = "S0"
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"


class LegalStatus(StrEnum):
    """许可状态机（S-001 验收：license denied 必须阻断采集）。"""

    PUBLIC_OFFICIAL = "public_official"  # 公开官方页面/附件，允许按条款保存
    MANUAL_EXPORT_ALLOWED = "manual_export_allowed"  # 合法人工导出优先（§4.4）
    PENDING_REVIEW = "pending_review"  # 未完成许可核验——默认按不可自动采集处理
    LICENSE_REQUIRED = "license_required"  # 付费/授权接口，须用户明示授权
    DENIED = "denied"  # 明确不允许——必须阻断


class SourceStatus(StrEnum):
    """§4.1 核验声明：接入验收通过前一律 CANDIDATE，不得当作"已完整获取"。"""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    DEGRADED = "degraded"  # §2.3：源失败降级，不覆盖上次合格快照
    RETIRED = "retired"


class RevisionBehavior(StrEnum):
    """修订行为（§7.2 source/series registry 共用口径）。"""

    FIRST_RELEASE_FINAL = "first_release_final"  # 首发=终值
    REVISED_LATER = "revised_later"  # 首发后存在有限次官方修订
    CONTINUOUSLY_REVISED = "continuously_revised"  # 数据平台式持续修订（当前值≠首发值）


class TimestampPrecision(StrEnum):
    """发布时点精度（§7.5）：影响 release_evidence 分级与保守滞后。"""

    SECOND = "second"
    MINUTE = "minute"
    DAY = "day"  # 只有日期——B 级证据，次一交易日 09:30 后可用（§6.4）
    MONTH = "month"  # 只知月份——C 级证据，走保守滞后
    UNKNOWN = "unknown"  # D 级：无可靠时点


class ReleasePolicy(ContractBaseModel):
    """发布政策（版本化）：驱动 RECONSTRUCTED 模式的保守可用日计算（§6.4）。

    ``release_lag_days`` 为所属期期末到通常发布日的自然日上限（应取可验证
    历史年份的 p99，不允许拍脑袋常数）；``safety_days`` 为额外安全垫。
    ``release_policy_version`` 不得回写到已冻结实验（§6.4）。
    """

    policy_version: NonEmptyStr
    description: NonEmptyStr
    timezone: NonEmptyStr = "Asia/Shanghai"
    release_lag_days: Annotated[int, Field(ge=0)]
    safety_days: Annotated[int, Field(ge=0)] = 0
    # 已知当日具体发布时刻（如统计局 09:30）时填写；None 表示只知日期或更粗。
    intraday_time: time | None = None


class SourceDescriptor(ContractBaseModel):
    """数据源描述符（§4.3 逐源验收规范要求的全部字段）。

    每个源接入验收前必须填写本合同并登记于 source_registry；
    ``last_smoke_test`` 为最近一次连通性验证时间（离线 parser 不受其约束）。
    """

    source_id: LowerId
    owner: NonEmptyStr  # 责任工作线/人
    url: UrlStr
    data_fields: Annotated[list[NonEmptyStr], Field(min_length=1)]
    frequency: Frequency
    timezone: NonEmptyStr = "Asia/Shanghai"
    legal_status: LegalStatus = LegalStatus.PENDING_REVIEW
    revision_behavior: RevisionBehavior
    release_policy: ReleasePolicy | None = None
    last_smoke_test: AwareDatetime | None = None
    priority: SourcePriority
    status: SourceStatus = SourceStatus.CANDIDATE
    source_version: NonEmptyStr = "1"
    notes: str = ""

    @model_validator(mode="after")
    def _policy_matches_revision(self) -> SourceDescriptor:
        # 持续修订的源必须有可版本化的发布政策，否则 RECONSTRUCTED 无法计算保守可用日
        if self.revision_behavior == RevisionBehavior.CONTINUOUSLY_REVISED:
            if self.release_policy is None:
                raise ValueError(
                    "continuously_revised 源必须提供 release_policy（保守可用日计算依据）"
                )
        return self


class SeriesSpec(ContractBaseModel):
    """指标序列定义（§7.2 series_registry + §7.5 语义字段）。

    ``series_id`` 是经济指标唯一身份（如 ``cn.nbs.industry_c31.profit_ytd``），
    与展示中文名无关（§7.1）。所有口径字段入库时必须齐备，禁止靠猜。
    """

    series_id: LowerId
    source_id: LowerId
    unit: NonEmptyStr  # 元/吨/亿元/百分比（§7.5）
    unit_scale: Decimal = Decimal("1")  # 记录值 × unit_scale = 标准单位值
    currency: NonEmptyStr = "CNY"
    frequency: Frequency
    transform: NonEmptyStr = "none"  # 声明式变换标识（none/yoy/diff_pp/...）
    is_ytd: bool = False  # 累计值：跨年口径陷阱见 §7.6
    is_seasonally_adjusted: bool = False
    statistical_population: NonEmptyStr | None = None  # 如"规模以上工业企业"
    region: NonEmptyStr = "CN"
    industry_standard_version: NonEmptyStr | None = None  # 如 GB/T 4754-2017
    revision_policy: RevisionBehavior
    release_policy: ReleasePolicy | None = None
    timestamp_precision: TimestampPrecision
    valid_from: str | None = None  # 序列启用的所属期（YYYY-MM）
    description: NonEmptyStr
    tags: Annotated[list[str], Field(default_factory=list)]

    @model_validator(mode="after")
    def _scale_positive(self) -> SeriesSpec:
        if self.unit_scale <= 0:
            raise ValueError("unit_scale 必须为正数（§7.5 单位换算可追溯）")
        return self
