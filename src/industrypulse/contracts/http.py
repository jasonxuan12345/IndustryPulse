"""C-008 HTTP 合同：错误码 / 统一响应体 / 路由清单 / 示例工厂。

统一响应体与错误码来自 §14；路由清单来自 §14.2 第一批接口。
示例工厂产出的 JSON 由 scripts/export_contracts.py 落盘到
contracts/openapi/examples/，即"API mock 响应由 schema 生成"（C-008 验收）。
响应体中的数值均为展示合同用的虚构示例（§14.1）。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import Field

from .common import AwareDatetime, ContractBaseModel, NonEmptyStr
from .context import DataQuality, ResearchMode

ResultT = TypeVar("ResultT")


class ErrorCode(StrEnum):
    """§14.3 错误码全集：前端凭 code 而非字符串匹配做分支。"""

    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    LICENSE_NOT_GRANTED = "LICENSE_NOT_GRANTED"
    EVIDENCE_UNVERIFIED = "EVIDENCE_UNVERIFIED"
    PIT_NOT_ELIGIBLE = "PIT_NOT_ELIGIBLE"
    SERIES_NOT_FOUND = "SERIES_NOT_FOUND"
    INDUSTRY_MAPPING_MISSING = "INDUSTRY_MAPPING_MISSING"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    MODEL_NOT_VALIDATED = "MODEL_NOT_VALIDATED"
    UNSCORABLE_TARGET = "UNSCORABLE_TARGET"
    NO_TRADABLE_ETF = "NO_TRADABLE_ETF"
    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"
    SNAPSHOT_CORRUPT = "SNAPSHOT_CORRUPT"


#: 错误码 → HTTP 状态码（统一映射，避免各端点自行发明）。
ERROR_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.SOURCE_UNAVAILABLE: 503,
    ErrorCode.LICENSE_NOT_GRANTED: 451,
    ErrorCode.EVIDENCE_UNVERIFIED: 422,
    ErrorCode.PIT_NOT_ELIGIBLE: 422,
    ErrorCode.SERIES_NOT_FOUND: 404,
    ErrorCode.INDUSTRY_MAPPING_MISSING: 404,
    ErrorCode.INSUFFICIENT_HISTORY: 422,
    ErrorCode.MODEL_NOT_VALIDATED: 409,
    ErrorCode.UNSCORABLE_TARGET: 422,
    ErrorCode.NO_TRADABLE_ETF: 404,
    ErrorCode.CONTRACT_MISMATCH: 500,
    ErrorCode.SNAPSHOT_CORRUPT: 500,
}

#: 错误码 → 修复指引（§14.3 remediation）。
ERROR_REMEDIATION: dict[ErrorCode, str] = {
    ErrorCode.SOURCE_UNAVAILABLE: "检查来源健康页与上次成功快照；失败不得覆盖旧快照。",
    ErrorCode.LICENSE_NOT_GRANTED: "该源许可未确认或被拒，须人工完成授权判定后方可接入。",
    ErrorCode.EVIDENCE_UNVERIFIED: "候选证据仍在审核队列，通过人工复核前不可入 Silver。",
    ErrorCode.PIT_NOT_ELIGIBLE: "当前研究模式下证据等级不足；切换模式或补充首发证据。",
    ErrorCode.SERIES_NOT_FOUND: "核对 series_id 与 series_registry 注册表。",
    ErrorCode.INDUSTRY_MAPPING_MISSING: "补齐 industry_series_map 配置后再查询。",
    ErrorCode.INSUFFICIENT_HISTORY: "有效样本不足；输出 INSUFFICIENT_HISTORY 而非强行建模。",
    ErrorCode.MODEL_NOT_VALIDATED: "模型未通过 OOS 门禁，只可展示基线与失败报告。",
    ErrorCode.UNSCORABLE_TARGET: "目标不可评估（缺失/跨年重置），记录 UNSCORABLE 而非填充。",
    ErrorCode.NO_TRADABLE_ETF: "该行业在所查历史时点无可交易 ETF，回测区段不可用。",
    ErrorCode.CONTRACT_MISMATCH: "响应与契约不符，属系统缺陷，须修复后重放。",
    ErrorCode.SNAPSHOT_CORRUPT: "快照哈希校验失败；从备份恢复，禁止部分发布。",
}


class ApiWarning(ContractBaseModel):
    """响应内警告（§14.1 warnings）：非致命但必须向用户披露。"""

    code: NonEmptyStr  # 如 PARTIAL_VINTAGE / DEMO_DATA / NOT_PIT_VERIFIED
    message: NonEmptyStr


# Generic[ResultT] 语法保持对 pydantic>=2.7 的兼容（PEP 695 需 >=2.9），故豁免 UP046
class ApiResponse(ContractBaseModel, Generic[ResultT]):  # noqa: UP046
    """统一响应体（§14.1）：所有 GET 查询接口的公共外壳。"""

    request_id: NonEmptyStr
    as_of: AwareDatetime
    research_mode: ResearchMode
    snapshot_id: NonEmptyStr
    data_quality: DataQuality
    result: ResultT
    warnings: list[ApiWarning] = Field(default_factory=list)


class ErrorPayload(ContractBaseModel):
    """错误响应体（§14.3）：code + remediation，避免前端猜字符串。"""

    request_id: NonEmptyStr
    code: ErrorCode
    message: NonEmptyStr
    remediation: NonEmptyStr
    http_status: int

    @classmethod
    def build(cls, *, request_id: str, code: ErrorCode, message: str) -> ErrorPayload:
        return cls(
            request_id=request_id,
            code=code,
            message=message,
            remediation=ERROR_REMEDIATION[code],
            http_status=ERROR_HTTP_STATUS[code],
        )


class RouteSpec(ContractBaseModel):
    """§14.2 第一批接口的机器可读清单（mock 端点与 OpenAPI 样例的数据源）。"""

    method: NonEmptyStr
    path: NonEmptyStr
    params: list[str] = Field(default_factory=list)
    result_kind: NonEmptyStr
    owner_lane: NonEmptyStr  # 负责模块（§14.2 负责模块列）


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/sources",
        params=["priority", "status"],
        result_kind="SourceDescriptor[]",
        owner_lane="S/D",
    ),
    RouteSpec(
        method="GET",
        path="/v1/series/{id}/history",
        params=["as_of", "mode", "start", "end"],
        result_kind="ObservationFrame",
        owner_lane="D",
    ),
    RouteSpec(
        method="GET",
        path="/v1/evidence/{id}",
        params=["evidence_id"],
        result_kind="EvidenceCandidate",
        owner_lane="A",
    ),
    RouteSpec(
        method="POST",
        path="/v1/research/discover",
        params=["ResearchQuery"],
        result_kind="job_id",
        owner_lane="A",
    ),
    RouteSpec(
        method="GET",
        path="/v1/research/jobs/{id}",
        params=["job_id"],
        result_kind="JobStatus",
        owner_lane="A/O",
    ),
    RouteSpec(
        method="GET",
        path="/v1/industries",
        params=["taxonomy_version"],
        result_kind="IndustryNode[]",
        owner_lane="I",
    ),
    RouteSpec(
        method="GET",
        path="/v1/industries/{id}/state",
        params=["as_of", "mode"],
        result_kind="EconomyReport",
        owner_lane="E",
    ),
    RouteSpec(
        method="GET",
        path="/v1/industries/{id}/series",
        params=["dimension", "as_of"],
        result_kind="IndicatorFrame",
        owner_lane="E",
    ),
    RouteSpec(
        method="GET",
        path="/v1/industries/{id}/forecast",
        params=["target", "horizon", "as_of", "mode"],
        result_kind="ForecastResult",
        owner_lane="M",
    ),
    RouteSpec(
        method="GET",
        path="/v1/experiments",
        params=["industry", "target", "mode"],
        result_kind="ExperimentCard[]",
        owner_lane="R",
    ),
    RouteSpec(
        method="GET",
        path="/v1/experiments/{id}",
        params=["run_id"],
        result_kind="ExperimentDetail",
        owner_lane="R",
    ),
    RouteSpec(
        method="GET",
        path="/v1/investment/opportunities",
        params=["as_of", "mode"],
        result_kind="WatchList",
        owner_lane="V",
    ),
    RouteSpec(
        method="GET",
        path="/v1/backtests/{id}",
        params=["experiment_id"],
        result_kind="BacktestReport",
        owner_lane="V/R",
    ),
    RouteSpec(
        method="GET",
        path="/v1/jobs",
        params=["state", "period"],
        result_kind="JobStatus[]",
        owner_lane="O",
    ),
    RouteSpec(method="GET", path="/healthz", params=[], result_kind="Health", owner_lane="O"),
)


def sample_industry_state_payload() -> ApiResponse[dict[str, object]]:
    """§14.1 示例响应的合同化重建（数值为虚构展示样例）。

    E 线（EconomyReport）在 G1 完成后，此工厂改为引用真实合同类型；
    G0 阶段锁定的是外壳结构而非 result 内部形状。
    """
    demo_ts = "2026-09-30T17:00:00+08:00"
    return ApiResponse[dict[str, object]](
        request_id="req-example",
        as_of=datetime.fromisoformat(demo_ts),
        research_mode=ResearchMode.RECONSTRUCTED_PIT,
        snapshot_id="sha256:example",
        data_quality=DataQuality(coverage=0.82, vintage_verified_ratio=0.54),
        result={"industry_id": "CN_NBS_C31", "state": "demo", "dimensions": {}},
        warnings=[ApiWarning(code="PARTIAL_VINTAGE", message="部分指标为重建版本")],
    )


def sample_error_payload() -> ErrorPayload:
    """错误响应示例：PIT 门槛不足。"""
    return ErrorPayload.build(
        request_id="req-example-err",
        code=ErrorCode.PIT_NOT_ELIGIBLE,
        message="as_of=2025-08-31 下 STRICT_PIT 模式要求发布/数值证据 ≥B",
    )
