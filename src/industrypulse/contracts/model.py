"""C-006 模型层合同：特征帧 / 目标帧 / 预测（M 线与 R 线的共同边界）。

反泄漏三要素（§6.6/C-006 验收）：

- 特征行携带 ``max_ancestor_available_at``（特征链最晚祖先数据可用日）；
- 目标行携带 ``label_available_at``（标签可评估日）——晚于训练截止的标签
  禁止进入训练集（PIT-006）；
- 帧行按 ``decision_as_of`` 时间排序，随机切分在合同层即被排除（§10.5）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Protocol, runtime_checkable

from pydantic import Field, model_validator

from .common import AwareDatetime, ContractBaseModel, NonEmptyStr, UpperId
from .context import ResearchMode
from .observation import ObservationFrame, ObservationVintage
from .research import ExperimentSpec


class ModelStatus(StrEnum):
    """模型状态（§11.6/§2.3）：degraded 必须显式展示，禁止静默冒充今日预测。"""

    OK = "ok"
    DEGRADED = "degraded"  # 已回退到简单基线
    UNCALIBRATED = "uncalibrated"  # 样本不足以校准，禁展示虚假精度
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"  # §11.7 低样本/无双类
    NO_PREDICTIVE_EDGE = "no_predictive_edge"  # §12.6 失败也是有效结果
    NOT_VALIDATED = "not_validated"


class CalibrationStatus(StrEnum):
    CALIBRATED = "calibrated"  # Platt/isotonic，独立校准时间窗且样本足够
    UNCALIBRATED = "uncalibrated"
    NOT_APPLICABLE = "not_applicable"  # 非概率模型


class FeatureRow(ContractBaseModel):
    """单期特征行：``decision_as_of`` 为模拟决策时点（月末快照，§10.2）。"""

    decision_as_of: AwareDatetime
    industry_id: UpperId
    features: dict[str, float | None]  # 值或缺失（缺失必须有 missing 标识列，§10.3）
    missing_features: Annotated[list[str], Field(default_factory=list)]
    max_ancestor_available_at: AwareDatetime  # §6.6：不得晚于 decision_as_of
    latest_source_release_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _ancestor_not_future(self) -> FeatureRow:
        if self.max_ancestor_available_at > self.decision_as_of:
            raise ValueError(
                f"max_ancestor_available_at({self.max_ancestor_available_at}) "
                f"晚于 decision_as_of({self.decision_as_of})：特征链泄漏（§6.6）"
            )
        return self


class FeatureFrame(ContractBaseModel):
    """特征快照（§7.2 feature_snapshot / §10.3）：外存工件携带哈希与规格版本。"""

    industry_id: UpperId
    snapshot_id: NonEmptyStr  # feature_snapshot.snapshot_id
    feature_spec_version: NonEmptyStr
    schema_hash: NonEmptyStr  # 特征 schema 指纹
    research_mode: ResearchMode
    built_at: AwareDatetime
    rows: Annotated[list[FeatureRow], Field(min_length=1)]

    @model_validator(mode="after")
    def _sorted_in_time(self) -> FeatureFrame:
        times = [r.decision_as_of for r in self.rows]
        if times != sorted(times):
            raise ValueError("特征行必须按 decision_as_of 升序（§10.5 随机切分禁用）")
        if any(r.industry_id != self.industry_id for r in self.rows):
            raise ValueError("特征帧内混入其他行业的行")
        return self


class TargetSpec(ContractBaseModel):
    """预测目标定义（§10.1 T1/T2）：跨年、分母异常、缺目标的处理必须显式声明。"""

    target_id: NonEmptyStr  # 如 T1_IVA_DIRECTION / T2_MARGIN_IMPROVE
    definition_version: NonEmptyStr
    industry_id: UpperId | None  # None = 跨行业通用定义
    horizon_months: Annotated[int, Field(ge=1, le=6)]
    label_kind: NonEmptyStr  # direction_up / margin_change / ...
    formula: NonEmptyStr  # 领域公式（如 §10.1 的 Y_t3 定义）
    missing_policy: NonEmptyStr  # UNSCORABLE 处置声明
    baselines: Annotated[list[NonEmptyStr], Field(min_length=1)]  # 必须对照的基线（§11.1）
    description: NonEmptyStr


class TargetRow(ContractBaseModel):
    """单期标签：``label_available_at`` 为 None 表示尚未可评估，禁止当训练标签。"""

    industry_id: UpperId
    decision_as_of: AwareDatetime  # 该标签对应的预测时点
    target_period: NonEmptyStr  # 目标所属日历月 YYYY-MM（§10.2）
    value: float | None
    label_available_at: AwareDatetime | None  # None ⇒ 不可用于训练（PIT-006）
    unscorable_reason: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _value_availability(self) -> TargetRow:
        if self.value is None and self.unscorable_reason is None:
            raise ValueError("value 为 None 必须给出 unscorable_reason（UNSCORABLE，§10.1）")
        if self.value is not None and self.label_available_at is None:
            raise ValueError("有值的标签必须声明 label_available_at（PIT-006）")
        return self


class TargetFrame(ContractBaseModel):
    """目标帧：行按 decision_as_of 升序。"""

    spec: TargetSpec
    rows: Annotated[list[TargetRow], Field(min_length=1)]

    @model_validator(mode="after")
    def _sorted_in_time(self) -> TargetFrame:
        times = [r.decision_as_of for r in self.rows]
        if times != sorted(times):
            raise ValueError("目标行必须按 decision_as_of 升序（§10.5）")
        return self


class Prediction(ContractBaseModel):
    """冻结预测（§11.6 ForecastResult 全字段）。预测存档不可回改。"""

    prediction_id: NonEmptyStr
    model_id: NonEmptyStr
    model_version: NonEmptyStr
    industry_id: UpperId
    target_id: NonEmptyStr
    as_of: AwareDatetime
    horizon_months: Annotated[int, Field(ge=1, le=6)]
    research_mode: ResearchMode
    predicted_probability: Annotated[float, Field(ge=0.0, le=1.0)] | None
    point_estimate: float | None
    prediction_interval: tuple[float, float] | None
    baseline_probability: Annotated[float, Field(ge=0.0, le=1.0)] | None
    calibration_status: CalibrationStatus
    top_features: list[str] = Field(default_factory=list)
    driver_evidence_ids: list[str] = Field(default_factory=list)
    missing_features: list[str] = Field(default_factory=list)
    model_status: ModelStatus
    oos_metrics_by_year: dict[str, dict[str, float]] = Field(default_factory=dict)
    expiry_at: AwareDatetime | None = None  # 过期预测不得冒充今日预测（§2.3）
    run_id: NonEmptyStr


class ModelArtifact(ContractBaseModel):
    """训练产物（M-014）：确定性哈希，重复运行可复核。"""

    model_id: NonEmptyStr
    model_version: NonEmptyStr
    artifact_uri: NonEmptyStr
    sha256: NonEmptyStr
    trained_at: AwareDatetime
    feature_spec_version: NonEmptyStr
    target_id: NonEmptyStr
    training_rows: Annotated[int, Field(ge=0)]


class LabeledFeatureFrame(ContractBaseModel):
    """训练帧：特征与标签逐行对齐（§11.1 规程步骤 2）。"""

    features: FeatureFrame
    labels: list[float | None]  # 与 features.rows 等长对齐；None=标签未公布
    label_available_at: list[AwareDatetime | None]

    @model_validator(mode="after")
    def _aligned(self) -> LabeledFeatureFrame:
        n = len(self.features.rows)
        if not (len(self.labels) == n and len(self.label_available_at) == n):
            raise ValueError("labels/label_available_at 必须与特征行等长对齐")
        for lab, avail in zip(self.labels, self.label_available_at, strict=True):
            if lab is not None and avail is None:
                raise ValueError("有标签的行必须携带 label_available_at（PIT-006）")
        return self


class PredictionFrame(ContractBaseModel):
    """一次 run 的预测集合。"""

    run_id: NonEmptyStr
    predictions: Annotated[list[Prediction], Field(min_length=1)]

    @model_validator(mode="after")
    def _single_run(self) -> PredictionFrame:
        if any(p.run_id != self.run_id for p in self.predictions):
            raise ValueError("PredictionFrame 混入其他 run 的预测")
        return self


@runtime_checkable
class ObservationRepository(Protocol):
    """§7.4：economy 只经此协议消费观测，禁止跨层直连数据库（§2.2）。"""

    def fetch(self, series_ids: Sequence[str], ctx: AnalysisContextLike) -> ObservationFrame: ...


class AnalysisContextLike(Protocol):
    """ObservationRepository 所需的最小上下文形状（结构化类型，避免实现耦合）。"""

    as_of: datetime
    research_mode: ResearchMode


@runtime_checkable
class ForecastModel(Protocol):
    """§7.4 模型协议：fit 只见训练帧，predict 只见特征帧。"""

    def fit(self, train: LabeledFeatureFrame, config: ExperimentSpec) -> ModelArtifact: ...

    def predict(self, features: FeatureFrame, artifact: ModelArtifact) -> PredictionFrame: ...


__all__ = [
    "AnalysisContextLike",
    "CalibrationStatus",
    "FeatureFrame",
    "FeatureRow",
    "ForecastModel",
    "LabeledFeatureFrame",
    "ModelArtifact",
    "ModelStatus",
    "ObservationRepository",
    "Prediction",
    "PredictionFrame",
    "TargetFrame",
    "TargetRow",
    "TargetSpec",
    "ObservationVintage",
]
