"""多模式 as-of 对照报告（§20.5）。

任务书要求："人工设置历史 as_of 回放，STRICT 与 RECONSTRUCTED 同时输出包含
行级可用性原因的对照表。严格模式数据不足时标红 coverage，不能偷偷提升数据等级。"

本模块只读仓储、不改任何数据；输出同时包含入选行与被排除行（含原因是硬要求，
"没用到某条数据"必须可解释）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field

from industrypulse.contracts.common import AwareDatetime, ContractBaseModel, NonEmptyStr
from industrypulse.contracts.context import ResearchMode
from industrypulse.contracts.observation import EvidenceGrade, ObservationVintage
from industrypulse.contracts.pit import SkipReason, effective_available_at
from industrypulse.repositories import DuckDBObservationRepository


class _Clock:
    """仓储 as-of 查询所需的最小上下文形状（不需要行业/映射版本）。

    ``AnalysisContextLike`` 协议把字段声明为可写属性，因此这里用普通类而非
    frozen dataclass——否则只读属性不满足协议。
    """

    def __init__(
        self, as_of: datetime, research_mode: ResearchMode, dataset_snapshot_id: str | None
    ) -> None:
        self.as_of = as_of
        self.research_mode = research_mode
        self.dataset_snapshot_id = dataset_snapshot_id


class PitRow(ContractBaseModel):
    """对照表的一行：某个 as_of + 模式下，某版本是入选还是被排除。"""

    as_of: AwareDatetime
    mode: ResearchMode
    series_id: NonEmptyStr
    observation_start: date
    vintage_id: int
    status: Literal["selected", "skipped"]
    reason: SkipReason | None = None
    value: Decimal | None = None
    release_evidence: EvidenceGrade | None = None
    value_evidence: EvidenceGrade | None = None
    available_at: AwareDatetime | None = None
    effective_available_at: AwareDatetime


class ModeSummary(ContractBaseModel):
    """单个 as_of + 模式的信息集概况。coverage 只统计"至少有一条入选"的序列。"""

    as_of: AwareDatetime
    mode: ResearchMode
    requested_series: int
    covered_series: int
    coverage: Annotated[float, Field(ge=0.0, le=1.0)]
    selected_rows: int
    skipped_rows: int


class PitComparison(ContractBaseModel):
    snapshot_id: NonEmptyStr
    series_ids: list[NonEmptyStr]
    rows: list[PitRow]
    summaries: list[ModeSummary]


def _effective(
    repository: DuckDBObservationRepository, row: ObservationVintage, mode: ResearchMode
) -> datetime:
    return effective_available_at(
        row,
        mode,
        trading_days=repository.trading_days,
        conservative_lag_days=repository.series_lag_days.get(
            row.series_id, repository.conservative_lag_days
        ),
    )


def compare_modes(
    repository: DuckDBObservationRepository,
    series_ids: Sequence[str],
    as_of_points: Sequence[datetime],
    modes: Sequence[ResearchMode] = (
        ResearchMode.STRICT_PIT,
        ResearchMode.RECONSTRUCTED_PIT,
    ),
    *,
    snapshot_id: str | None = None,
) -> PitComparison:
    """对每个 as_of × 模式做一次冻结快照查询，输出逐行原因。"""
    if not series_ids:
        raise ValueError("至少需要一个 series_id")
    if not as_of_points:
        raise ValueError("至少需要一个 as_of 时点")
    if not modes:
        raise ValueError("至少需要一个研究模式")
    if any(point.tzinfo is None for point in as_of_points):
        raise ValueError("as_of 必须带时区")
    if snapshot_id is None:
        snapshot_id = getattr(repository, "_last_snapshot_id", None)
        if snapshot_id is None:
            raise ValueError("必须显式给出冻结快照 ID（dataset_snapshot_id）")

    cache: dict[tuple[str, date], tuple[ObservationVintage, ...]] = {}

    def vintages(series_id: str, start: date) -> tuple[ObservationVintage, ...]:
        key = (series_id, start)
        if key not in cache:
            cache[key] = repository.vintages_for(series_id, start)
        return cache[key]

    rows: list[PitRow] = []
    summaries: list[ModeSummary] = []
    for as_of in as_of_points:
        for mode in modes:
            selection = repository.select(list(series_ids), _Clock(as_of, mode, snapshot_id))
            covered = {item.series_id for item in selection.selected}
            for item in selection.selected:
                rows.append(
                    PitRow(
                        as_of=as_of,
                        mode=mode,
                        series_id=item.series_id,
                        observation_start=item.observation_start,
                        vintage_id=item.vintage_id,
                        status="selected",
                        value=item.value,
                        release_evidence=item.release_evidence,
                        value_evidence=item.value_evidence,
                        available_at=item.available_at,
                        effective_available_at=_effective(repository, item, mode),
                    )
                )
            for skipped in selection.skipped:
                known = next(
                    (
                        row
                        for row in vintages(skipped.series_id, skipped.observation_start)
                        if row.vintage_id == skipped.vintage_id
                    ),
                    None,
                )
                rows.append(
                    PitRow(
                        as_of=as_of,
                        mode=mode,
                        series_id=skipped.series_id,
                        observation_start=skipped.observation_start,
                        vintage_id=skipped.vintage_id,
                        status="skipped",
                        reason=skipped.reason,
                        value=known.value if known else None,
                        release_evidence=known.release_evidence if known else None,
                        value_evidence=known.value_evidence if known else None,
                        available_at=known.available_at if known else None,
                        effective_available_at=(
                            _effective(repository, known, mode) if known else as_of
                        ),
                    )
                )
            summaries.append(
                ModeSummary(
                    as_of=as_of,
                    mode=mode,
                    requested_series=len(set(series_ids)),
                    covered_series=len(covered),
                    coverage=len(covered) / len(set(series_ids)),
                    selected_rows=len(selection.selected),
                    skipped_rows=len(selection.skipped),
                )
            )
    rows.sort(key=lambda row: (row.as_of, row.mode.value, row.series_id, row.observation_start))
    return PitComparison(
        snapshot_id=snapshot_id, series_ids=list(series_ids), rows=rows, summaries=summaries
    )


def format_table(comparison: PitComparison) -> str:
    """把对照表渲染成可粘贴进验收记录的纯文本表。"""
    lines = [
        f"snapshot={comparison.snapshot_id}",
        "",
        f"{'as_of':<26}{'mode':<20}{'covered':>8}{'cov':>7}{'sel':>5}{'skip':>6}",
        "-" * 72,
    ]
    for summary in comparison.summaries:
        lines.append(
            f"{summary.as_of.isoformat():<26}{summary.mode.value:<20}"
            f"{summary.covered_series:>8}{summary.coverage:>7.0%}"
            f"{summary.selected_rows:>5}{summary.skipped_rows:>6}"
            + ("   <-- 覆盖不足" if summary.coverage < 1.0 else "")
        )
    lines += ["", "行级明细：", ""]
    for row in comparison.rows:
        if row.status == "selected":
            detail = (
                f"SELECTED  v{row.vintage_id} value={row.value} "
                f"[{row.release_evidence}/{row.value_evidence}]"
            )
        else:
            reason = row.reason.value if row.reason else "unknown"
            detail = f"skipped   v{row.vintage_id} reason={reason}"
        lines.append(
            f"  {row.as_of.isoformat():<26}{row.mode.value:<20}"
            f"{row.series_id:<36}{row.observation_start}  {detail}"
        )
    return "\n".join(lines)


__all__ = ["ModeSummary", "PitComparison", "PitRow", "compare_modes", "format_table"]
