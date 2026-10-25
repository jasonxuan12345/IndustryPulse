"""增长信号的联合可见时间与历史决策日重放（阶段 1 · 可信基线）。

基线要求："历史每个信号日分别查询可见版本，检查利润、收入、旧期值的
联合可见时间。"

改善信号用到三个部件：当期利润累计同比、上一公布期利润累计同比（改善
基准）、当期营收累计同比。此前价格比较只拿当期利润的 available_at 当
入场时点；如果收入或上一期值更晚可见，入场就用了当时还看不到的数据。

本模块只读仓储，回答三件事：

1. 联合可见时间：每个相邻公布期对上，三个部件各自首个通过模式门槛版本
   的 effective_available_at，取最大值并标出拖后腿的部件；
2. 决策日重放：给定 as_of，用仓储 as-of 选择重放当时可见的值，重算信号
   与增长假设，和用最终数据算出的候选对照，不一致即修订翻转了信号；
3. 旧期值修订：信号用到的前期值在首次可见之后又出现新版本的情况。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from industrypulse.contracts.context import ResearchMode
from industrypulse.contracts.observation import ObservationVintage
from industrypulse.contracts.pit import pit_mode_eligible
from industrypulse.economy.price_check import adjacent_periods
from industrypulse.repositories import DuckDBObservationRepository

_MODE = ResearchMode.RECONSTRUCTED_PIT


def _effective_for(
    repository: DuckDBObservationRepository,
    vintage: ObservationVintage,
    mode: ResearchMode,
) -> datetime:
    from industrypulse.contracts.pit import effective_available_at

    return effective_available_at(
        vintage,
        mode,
        trading_days=repository.trading_days,
        conservative_lag_days=repository.series_lag_days.get(
            vintage.series_id, repository.conservative_lag_days
        ),
    )


def first_visible(
    vintages: Sequence[ObservationVintage],
    repository: DuckDBObservationRepository,
    mode: ResearchMode,
) -> tuple[ObservationVintage, datetime] | None:
    """最早通过模式门槛且可知的版本；一个都没有则 None。"""
    best: tuple[ObservationVintage, datetime] | None = None
    for vintage in vintages:
        if pit_mode_eligible(vintage, mode) is not None or vintage.value is None:
            continue
        eff = _effective_for(repository, vintage, mode)
        if best is None or eff < best[1]:
            best = (vintage, eff)
    return best


def _number(vintage: ObservationVintage | None) -> float | None:
    return None if vintage is None or vintage.value is None else float(vintage.value)


def improvement_signal(
    profit_now: float | None,
    profit_previous: float | None,
    revenue_now: float | None,
) -> bool | None:
    """与 price_check.signal_history 同一条规则：改善且收入为正。"""
    if profit_now is None or profit_previous is None or revenue_now is None:
        return None
    return profit_now - profit_previous > 0 and revenue_now > 0


def joint_visibility(
    profit: Sequence[ObservationVintage],
    revenue: Sequence[ObservationVintage],
    repository: DuckDBObservationRepository,
    mode: ResearchMode,
) -> list[dict[str, Any]]:
    """每个相邻公布期对的联合可见时间；部件缺任一就不产出该期。"""
    periods = sorted({row.observation_start for row in profit})
    revenue_first = {
        period: first
        for period, first in (
            (
                period,
                first_visible(
                    [v for v in revenue if v.observation_start == period], repository, mode
                ),
            )
            for period in periods
        )
        if first is not None
    }
    rows: list[dict[str, Any]] = []
    for index, period in enumerate(periods):
        if index == 0:
            continue
        earlier = periods[index - 1]
        if not adjacent_periods(earlier, period):
            continue
        current = first_visible(
            [v for v in profit if v.observation_start == period], repository, mode
        )
        previous = first_visible(
            [v for v in profit if v.observation_start == earlier], repository, mode
        )
        revenue_row = revenue_first.get(period)
        if current is None or previous is None or revenue_row is None:
            continue
        parts: list[tuple[str, tuple[ObservationVintage, datetime]]] = [
            ("当期利润", current),
            ("上期利润", previous),
            ("当期营收", revenue_row),
        ]
        joint_at = max(part[1][1] for part in parts)
        lagging = max(parts, key=lambda part: part[1][1])[0]
        rows.append(
            {
                "period": period.isoformat(),
                "previous_period": earlier.isoformat(),
                "release_at": current[1].isoformat(),
                "joint_visible_at": joint_at.isoformat(),
                "lagging_component": lagging,
                "delay_days": round((joint_at - current[1]).total_seconds() / 86400, 2),
                "components": {
                    name: {
                        "available_at": eff.isoformat(),
                        "vintage_id": vint.vintage_id,
                        "value": _number(vint),
                    }
                    for name, (vint, eff) in parts
                },
                "visible_signal": improvement_signal(
                    _number(current[0]), _number(previous[0]), _number(revenue_row[0])
                ),
            }
        )
    return rows


def revision_report(
    vintages: Sequence[ObservationVintage],
    repository: DuckDBObservationRepository,
    mode: ResearchMode,
) -> list[dict[str, Any]]:
    """同所属期出现多个版本即修订；给出首次可见与最新修订的时间与值。"""
    grouped: dict[tuple[str, date], list[ObservationVintage]] = {}
    for vintage in vintages:
        grouped.setdefault((vintage.series_id, vintage.observation_start), []).append(vintage)
    rows: list[dict[str, Any]] = []
    for (series_id, period), items in sorted(grouped.items()):
        if len(items) < 2:
            continue
        ordered = sorted(items, key=lambda item: item.vintage_id)
        first = first_visible(ordered, repository, mode)
        latest = ordered[-1]
        rows.append(
            {
                "series_id": series_id,
                "period": period.isoformat(),
                "versions": len(ordered),
                "first_value": _number(ordered[0]),
                "latest_value": _number(latest),
                "first_visible_at": first[1].isoformat() if first else None,
                "latest_available_at": _effective_for(repository, latest, mode).isoformat(),
                "value_changed": _number(ordered[0]) != _number(latest),
            }
        )
    return rows


def replay_day(
    repository: DuckDBObservationRepository,
    *,
    snapshot_id: str | None,
    profit_id: str,
    revenue_id: str,
    as_of: datetime,
    mode: ResearchMode,
) -> dict[str, Any]:
    """重放一个历史决策日：当时可见的值、重算的信号与最终候选的对照。"""
    from types import SimpleNamespace

    clock = SimpleNamespace(as_of=as_of, research_mode=mode, dataset_snapshot_id=snapshot_id)
    selection = repository.select([profit_id, revenue_id], clock)
    visible = {(row.series_id, row.observation_start): row for row in selection.selected}

    def latest(series_id: str) -> ObservationVintage | None:
        rows = [row for (sid, _), row in visible.items() if sid == series_id]
        return max(rows, key=lambda item: item.observation_start) if rows else None

    def previous_adjacent(
        series_id: str, current: ObservationVintage | None
    ) -> ObservationVintage | None:
        if current is None:
            return None
        rows = [
            row
            for (sid, start), row in visible.items()
            if sid == series_id and adjacent_periods(start, current.observation_start)
        ]
        return max(rows, key=lambda item: item.observation_start) if rows else None

    profit_now = latest(profit_id)
    profit_prev = previous_adjacent(profit_id, profit_now)
    revenue_now = latest(revenue_id)
    signal = improvement_signal(_number(profit_now), _number(profit_prev), _number(revenue_now))

    # 最终数据（全部版本取最新值）下的同一期候选，用来对照修订的影响。
    full = repository.all_vintages([profit_id, revenue_id])

    def final_latest(series_id: str) -> ObservationVintage | None:
        rows = [row for row in full if row.series_id == series_id and row.value is not None]
        return max(rows, key=lambda item: item.observation_start) if rows else None

    def final_previous(
        series_id: str, current: ObservationVintage | None
    ) -> ObservationVintage | None:
        if current is None:
            return None
        rows = [
            row
            for row in full
            if row.series_id == series_id
            and row.value is not None
            and adjacent_periods(row.observation_start, current.observation_start)
        ]
        return max(rows, key=lambda item: item.observation_start) if rows else None

    final_profit = final_latest(profit_id)
    final_signal = improvement_signal(
        _number(final_profit),
        _number(final_previous(profit_id, final_profit)),
        _number(final_latest(revenue_id)),
    )
    return {
        "as_of": as_of.isoformat(),
        "visible_profit_period": profit_now.observation_start.isoformat() if profit_now else None,
        "visible_profit": _number(profit_now),
        "visible_profit_previous": _number(profit_prev),
        "visible_revenue": _number(revenue_now),
        "visible_vintage_ids": {row.series_id: row.vintage_id for row in selection.selected},
        "skipped": len(selection.skipped),
        "replayed_signal": signal,
        "final_signal": final_signal,
        "matches_final": signal == final_signal,
    }


def run_signal_replay(
    repository: DuckDBObservationRepository,
    *,
    snapshot_id: str,
    industries: dict[str, dict[str, str]],
    as_of: datetime,
    mode: ResearchMode = _MODE,
) -> dict[str, Any]:
    """对每个行业（profit/revenue series 映射）产出联合可见、修订与重放。"""
    payload: dict[str, Any] = {
        "as_of": as_of.isoformat(),
        "mode": mode.value,
        "snapshot_id": snapshot_id,
        "industries": [],
    }
    for name, wanted in industries.items():
        profit_id, revenue_id = wanted["profit"], wanted["revenue"]
        vintages = repository.all_vintages([profit_id, revenue_id])
        profit = [row for row in vintages if row.series_id == profit_id]
        revenue = [row for row in vintages if row.series_id == revenue_id]
        if not profit or not revenue:
            continue
        payload["industries"].append(
            {
                "industry": name,
                "profit_series": profit_id,
                "revenue_series": revenue_id,
                "joint_visibility": joint_visibility(profit, revenue, repository, mode),
                "revisions": revision_report(vintages, repository, mode),
                "replay": replay_day(
                    repository,
                    snapshot_id=snapshot_id,
                    profit_id=profit_id,
                    revenue_id=revenue_id,
                    as_of=as_of,
                    mode=mode,
                ),
            }
        )
    return payload


def format_report(payload: dict[str, Any]) -> str:
    """渲染成可粘贴进验收记录的纯文本。"""
    lines = [
        f"信号重放 as_of={payload['as_of']} mode={payload['mode']} "
        f"snapshot={payload['snapshot_id']}",
        "",
    ]
    for item in payload["industries"]:
        lines.append(f"## {item['industry']}")
        lines.append("")
        lines.append("联合可见时间（signal_history 应使用的入场下界）：")
        lines.append(f"{'所属期':<12}{'发布':<22}{'联合可见':<22}{'拖后':<8}{'延迟':>7}{'信号':>6}")
        for row in item["joint_visibility"]:
            signal = (
                "—" if row["visible_signal"] is None else ("多" if row["visible_signal"] else "空")
            )
            lines.append(
                f"{row['period']:<12}{row['release_at']:<22}{row['joint_visible_at']:<22}"
                f"{row['lagging_component']:<8}{row['delay_days']:>7}{signal:>6}"
            )
        if item["revisions"]:
            lines.append("")
            lines.append("旧期值修订（信号用过首次可见值后又出现的版本）：")
            for row in item["revisions"]:
                changed = "值变" if row["value_changed"] else "仅证据变"
                lines.append(
                    f"  {row['series_id']} {row['period']} v1={row['first_value']} -> "
                    f"v{row['versions']}={row['latest_value']}（{changed}，"
                    f"最新版本 {row['latest_available_at']}）"
                )
        replay = item["replay"]
        verdict = "一致" if replay["matches_final"] else "不一致（修订翻转了信号）"
        lines.append("")
        lines.append(
            f"决策日重放：可见利润期 {replay['visible_profit_period']} "
            f"利润 {replay['visible_profit']} 上期 {replay['visible_profit_previous']} "
            f"营收 {replay['visible_revenue']} → 信号 {replay['replayed_signal']}；"
            f"与最终数据{verdict}"
        )
        lines.append("")
    return "\n".join(lines)


__all__ = [
    "first_visible",
    "improvement_signal",
    "joint_visibility",
    "replay_day",
    "revision_report",
    "run_signal_replay",
    "format_report",
]
