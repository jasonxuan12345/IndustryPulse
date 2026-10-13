"""as-of 选择语义（§7.3 SQL 的合同级参考实现）。

本模块用纯函数冻结 PIT 选择规则；D 线仓储层（D-005/D-006/D-007）的 SQL
实现必须与这里的语义一致，tests/pit 的负例对两者一视同仁。

规则要点（对应任务书 §6.3/§6.4/§7.3）：

1. 证据门槛按模式动态判断（``pit_mode_eligible``），不是行的静态属性；
2. 可用时点过滤：``effective_available_at <= as_of``；
   - release 为 B 级（只知日期）→ 次一交易日 09:30（Asia/Shanghai）后可用；
   - RECONSTRUCTED 对 C 级发布证据追加保守滞后自然日；
3. 同一 (series_id, observation_start) 多版本共存时取
   ``ORDER BY available_at DESC, vintage_id DESC`` 的第一行——
   稳定 tie-break，禁止静默取"最新修订"（§7.3）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from pydantic import Field

from .common import TRADING_DAY_AVAILABLE_TIME, TZ_SHANGHAI, AwareDatetime, ContractBaseModel
from .context import (
    DEFAULT_CONSERVATIVE_LAG_DAYS,
    MODE_EVIDENCE_GATES,
    ResearchMode,
)
from .observation import AvailabilityBasis, EvidenceGrade, ObservationVintage

_TRADING_AVAILABLE_TS = time(*TRADING_DAY_AVAILABLE_TIME, tzinfo=TZ_SHANGHAI)


class SkipReason(StrEnum):
    """as-of 选择中被排除的原因（覆盖报告/审计用）。"""

    FUTURE_RELEASE = "future_release"  # 发布时点晚于 as_of（PIT-001/006）
    SAME_DAY_TIME_UNKNOWN = "same_day_time_unknown"  # B 级时点不明，顺延次交易日未到
    CONSERVATIVE_LAG_NOT_ELAPSED = "conservative_lag_not_elapsed"  # C 级保守滞后未过（PIT-005）
    INSUFFICIENT_RELEASE_EVIDENCE = "insufficient_release_evidence"  # 发布证据低于模式门槛
    INSUFFICIENT_VALUE_EVIDENCE = "insufficient_value_evidence"  # 数值证据低于模式门槛
    BASIS_NOT_ALLOWED = "basis_not_allowed"  # RETRIEVAL_TIME 等依据不满足模式要求


class SkippedVintage(ContractBaseModel):
    """被排除版本及其原因（行级可用性原因，§20.5 对照表）。"""

    series_id: str
    observation_start: date
    vintage_id: int
    reason: SkipReason
    detail: str = ""


class AsOfSelection(ContractBaseModel):
    """一次 as-of 查询的完整结果：入选 + 被排除原因（不静默丢弃信息）。"""

    as_of: AwareDatetime
    mode: ResearchMode
    selected: list[ObservationVintage] = Field(default_factory=list)
    skipped: list[SkippedVintage] = Field(default_factory=list)

    def selected_for(self, series_id: str, observation_start: date) -> ObservationVintage | None:
        for v in self.selected:
            if v.series_id == series_id and v.observation_start == observation_start:
                return v
        return None


def next_trading_day(d: date, trading_days: Sequence[date]) -> date | None:
    """严格晚于 d 的第一个交易日；日历耗尽返回 None（调用方自行回退 +1 自然日）。"""
    for td in trading_days:  # trading_days 须升序
        if td > d:
            return td
    return None


def pit_mode_eligible(v: ObservationVintage, mode: ResearchMode) -> SkipReason | None:
    """证据门槛判定（§6.3 伪码）。返回 None 表示通过，否则返回排除原因。"""
    if mode == ResearchMode.EXPLORATORY:
        return None
    # 依据判定：RETRIEVAL_TIME（只知抓取时点）在 STRICT/RECONSTRUCTED 一律
    # BASIS_NOT_ALLOWED。ASSUMED_POLICY_LAG 行按模型不变式发布证据只能是
    # C/D，交给证据门槛给出更准确的原因（INSUFFICIENT_RELEASE_EVIDENCE）。
    if v.availability_basis == AvailabilityBasis.RETRIEVAL_TIME:
        return SkipReason.BASIS_NOT_ALLOWED
    release_gate, value_gate = MODE_EVIDENCE_GATES[mode]
    if v.release_evidence not in release_gate:
        return SkipReason.INSUFFICIENT_RELEASE_EVIDENCE
    if v.value_evidence not in value_gate:
        return SkipReason.INSUFFICIENT_VALUE_EVIDENCE
    return None


def effective_available_at(
    v: ObservationVintage,
    mode: ResearchMode,
    *,
    trading_days: Sequence[date] = (),
    conservative_lag_days: int = DEFAULT_CONSERVATIVE_LAG_DAYS,
) -> datetime:
    """按模式计算本版本的"可知时点"。

    - B 级发布证据（只知日期）：STRICT/RECONSTRUCTED 下顺延到次一交易日
      09:30（无日历时回退 +1 自然日 00:00）；EXPLORATORY 用原始 available_at。
    - C 级发布证据在 RECONSTRUCTED 下追加保守滞后自然日（§6.4）。
    """
    eff = v.available_at
    if mode == ResearchMode.EXPLORATORY:
        return eff
    if v.release_evidence == EvidenceGrade.B and v.publication_at is not None:
        pub_date = v.publication_at.astimezone(TZ_SHANGHAI).date()
        ntd = next_trading_day(pub_date, trading_days)
        if ntd is None:
            candidate = datetime.combine(
                pub_date + timedelta(days=1), time(0, 0), tzinfo=TZ_SHANGHAI
            )
        else:
            candidate = datetime.combine(ntd, _TRADING_AVAILABLE_TS)
        eff = max(eff, candidate)
    if (
        mode == ResearchMode.RECONSTRUCTED_PIT
        and v.release_evidence == EvidenceGrade.C
        and conservative_lag_days > 0
    ):
        eff = eff + timedelta(days=conservative_lag_days)
    return eff


def _time_skip_reason(v: ObservationVintage, mode: ResearchMode, lag_days: int) -> SkipReason:
    """时点排除时按成因归类，供覆盖报告区分"没发布"与"不敢用"。"""
    if v.release_evidence == EvidenceGrade.B:
        return SkipReason.SAME_DAY_TIME_UNKNOWN
    if mode == ResearchMode.RECONSTRUCTED_PIT and v.release_evidence == EvidenceGrade.C:
        return SkipReason.CONSERVATIVE_LAG_NOT_ELAPSED
    return SkipReason.FUTURE_RELEASE


def select_vintages_as_of(
    vintages: Sequence[ObservationVintage],
    *,
    as_of: datetime,
    mode: ResearchMode,
    trading_days: Sequence[date] = (),
    conservative_lag_days: int = DEFAULT_CONSERVATIVE_LAG_DAYS,
) -> AsOfSelection:
    """§7.3 as-of 查询的参考实现（D 线 SQL 必须语义一致）。

    每个 (series_id, observation_start) 至多入选一行；被门槛或时点排除的
    版本全部记录 ``skipped`` 及原因，供覆盖率与审计使用。
    """
    selected: dict[tuple[str, date], tuple[datetime, ObservationVintage]] = {}
    skipped: list[SkippedVintage] = []
    for v in vintages:
        gate_reason = pit_mode_eligible(v, mode)
        if gate_reason is not None:
            skipped.append(
                SkippedVintage(
                    series_id=v.series_id,
                    observation_start=v.observation_start,
                    vintage_id=v.vintage_id,
                    reason=gate_reason,
                )
            )
            continue
        eff = effective_available_at(
            v, mode, trading_days=trading_days, conservative_lag_days=conservative_lag_days
        )
        if eff > as_of:
            skipped.append(
                SkippedVintage(
                    series_id=v.series_id,
                    observation_start=v.observation_start,
                    vintage_id=v.vintage_id,
                    reason=_time_skip_reason(v, mode, conservative_lag_days),
                    detail=f"effective_available_at={eff.isoformat()}",
                )
            )
            continue
        key = (v.series_id, v.observation_start)
        current = selected.get(key)
        # §7.3 稳定 tie-break：available_at DESC, vintage_id DESC
        if current is None or (eff, v.vintage_id) > (current[0], current[1].vintage_id):
            selected[key] = (eff, v)
    return AsOfSelection(
        as_of=as_of,
        mode=mode,
        selected=sorted(
            (v for _, v in selected.values()),
            key=lambda v: (v.series_id, v.observation_start, v.vintage_id),
        ),
        skipped=sorted(skipped, key=lambda s: (s.series_id, s.observation_start, s.vintage_id)),
    )
