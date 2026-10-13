"""C-007 研究合同：实验规格 / 回测事件 / 任务状态（R 线与 V/O 线的共同边界）。

可复现性合同（§12.4）：实验 manifest 只读保存；任何配置改动必须创建新 run，
而不是改写已冻结报告。BacktestEvent 的时间链 signal→order→fill 单调递增，
从合同层堵住"信号日前成交"（INVEST-001）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import Field, model_validator

from .common import AwareDatetime, ContractBaseModel, NonEmptyStr, UpperId
from .context import ResearchMode


class ApprovalState(StrEnum):
    """实验审批状态（§12.4 approval_state）：FROZEN 之后改动即须新建 run。"""

    DRAFT = "draft"
    FROZEN = "frozen"  # 已冻结：参数/特征/切分不可改
    APPROVED = "approved"  # 已通过门禁并出具报告
    ARCHIVED = "archived"


class SplitKind(StrEnum):
    """时间折类型（§12.1：expanding 为主、fixed 60 月 rolling 做稳健性）。"""

    EXPANDING = "expanding"
    ROLLING = "rolling"


class JobState(StrEnum):
    """任务状态（§7.2 job_run.state）。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"  # 幂等重放：上次成功则本次跳过（O-002）
    RETRYING = "retrying"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


#: JSON 标量（网格取值等）；不允许嵌套对象，保证 manifest 可 diff。
JsonScalar = str | int | float | bool


class SplitSpec(ContractBaseModel):
    """单条时间折（§12.1/§12.2）：purge/embargo 月数显式声明。"""

    split_id: NonEmptyStr
    kind: SplitKind
    train_start: date
    train_end: date  # train_cutoff：必须晚于全部训练标签公布日
    test_start: date
    test_end: date
    purge_months: Annotated[int, Field(ge=0)]  # 标签观察期重叠清除（§12.2）
    embargo_months: Annotated[int, Field(ge=0)]  # 清除后的附加静默期

    @model_validator(mode="after")
    def _ordering(self) -> SplitSpec:
        if not (self.train_start <= self.train_end < self.test_start <= self.test_end):
            raise ValueError("折边界必须满足 train_start<=train_end<test_start<=test_end")
        return self


class ExperimentSpec(ContractBaseModel):
    """冻结实验 manifest（§12.4 全字段）：预注册，先冻结后跑数。"""

    experiment_id: NonEmptyStr
    code_sha: NonEmptyStr  # git commit
    dependencies_hash: NonEmptyStr  # 依赖锁指纹
    dataset_manifest_hash: NonEmptyStr  # 输入数据快照指纹
    industry_map_version: NonEmptyStr
    pit_mode: ResearchMode
    train_cutoff: AwareDatetime
    outer_splits: Annotated[list[SplitSpec], Field(min_length=1)]
    inner_splits: list[SplitSpec] = Field(default_factory=list)  # 内层超参评估
    feature_list: Annotated[list[NonEmptyStr], Field(min_length=1)]
    model_grid: dict[str, dict[str, list[JsonScalar]]]  # model_id → 参数 → 候选值
    random_seed: int
    target_definition: NonEmptyStr  # TargetSpec.target_id@version
    baselines: Annotated[list[NonEmptyStr], Field(min_length=1)]
    primary_metric: NonEmptyStr  # 如 brier_score（§11.7 预注册门槛）
    secondary_metrics: list[NonEmptyStr] = Field(default_factory=list)
    approval_state: ApprovalState = ApprovalState.DRAFT
    created_at: AwareDatetime
    notes: str = ""


class BacktestEvent(ContractBaseModel):
    """回测事件（§7.2 backtest_event / §13.3）：signal→order→fill 完整链路。"""

    event_id: NonEmptyStr
    experiment_id: NonEmptyStr
    asset_id: UpperId
    signal_at: AwareDatetime  # 信号可知时点（快照生成后）
    order_at: AwareDatetime  # 下单时点：不得早于信号（下一可交易日，§13.3）
    fill_at: AwareDatetime  # 成交时点：执行价模型对应的时点
    side: OrderSide
    quantity: Annotated[Decimal, Field(gt=0)]
    price: Annotated[Decimal, Field(gt=0)] | None  # None = 未成交（HOLD/拒单）
    fees: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    pnl: Decimal | None = None
    prediction_id: NonEmptyStr | None = None  # 信号溯源
    notes: str = ""

    @model_validator(mode="after")
    def _causality(self) -> BacktestEvent:
        if self.order_at < self.signal_at:
            raise ValueError("order_at 早于 signal_at：未来函数（INVEST-001）")
        if self.price is not None and self.fill_at < self.order_at:
            raise ValueError("fill_at 早于 order_at（INVEST-001）")
        return self


class JobStatus(ContractBaseModel):
    """任务运行状态（§7.2 job_run）。幂等键见 §16。"""

    job_id: NonEmptyStr
    job_name: NonEmptyStr
    state: JobState
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    retry_count: Annotated[int, Field(ge=0)] = 0
    error: NonEmptyStr | None = None
    idempotency_key: NonEmptyStr | None = None  # source_id+period+artifact_sha256+parser_version

    @model_validator(mode="after")
    def _state_times(self) -> JobStatus:
        if self.state in (JobState.SUCCEEDED, JobState.FAILED):
            if self.start_at is None or self.end_at is None:
                raise ValueError("终态任务必须同时有 start_at 与 end_at")
            if self.end_at < self.start_at:
                raise ValueError("end_at 不得早于 start_at")
        if self.state == JobState.FAILED and self.error is None:
            raise ValueError("失败任务必须携带 error")
        return self
