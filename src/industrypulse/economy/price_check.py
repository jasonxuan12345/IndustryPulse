"""行业改善信号发布之后，对口 ETF 相对沪深 300 的历史表现。

只做历史描述和检验，结论固定为 NOT_VALIDATED：
- 信号与 ``growth_hypothesis`` 同一条规则：利润累计同比较上一公布期上升，且营收累计同比为正。
- 入场取发布日之后的第一个共同交易日收盘，避免用到发布当天盘中信息。
- 超额收益 = ETF 总收益 - 基准总收益；总收益为不复权收盘价加回期间现金分红
  （分红不再投资），零费用：不计佣金、冲击成本与跟踪误差。
- 月度信号的持有窗口互相重叠，普通 t 检验会高估显著性。这里用循环平移检验：
  把信号序列整体平移再比较，保留两条序列各自的自相关。
- 多个行业和周期一起检验，用 Benjamini-Hochberg 控制错误发现率。

份额拆分/合并会机械地改写不复权价格。目前 corporate_action 里没有这类记录，
调用方用价格缺口找出疑似拆并日，作为 ``barriers`` 传入；跨越障碍日的收益
窗口整段剔除并计数，绝不用猜出来的比例去复权。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from statistics import fmean

#: 交易日数，约 3 个月与 6 个月。
HORIZONS = ((3, 63), (6, 126))
MIN_EVENTS = 6
#: 发布后超过这么多天才有第一根共同 bar，说明价格线当时还没上市，该次发布不计入。
MAX_ENTRY_GAP_DAYS = 10


@dataclass(frozen=True)
class Release:
    period: date
    available_at: datetime
    signal: bool | None


@dataclass(frozen=True)
class PriceLine:
    days: tuple[date, ...]
    closes: tuple[float, ...]
    dividends: tuple[tuple[date, float], ...] = ()

    def total_return(self, start: date, end: date) -> float:
        first = self.closes[self.days.index(start)]
        last = self.closes[self.days.index(end)]
        paid = sum(amount for ex_date, amount in self.dividends if start < ex_date <= end)
        return (last + paid) / first - 1


def adjacent_periods(earlier: date, later: date) -> bool:
    """相邻公布期。1-2 月合并发布，所以 12 月到 2 月也算相邻。"""
    months = (later.year - earlier.year) * 12 + later.month - earlier.month
    return 1 <= months <= 2


def signal_history(
    profit: Sequence[tuple[date, datetime, float | None]],
    revenue: dict[date, float | None],
) -> list[Release]:
    """profit 为按期排序的 (期, 可见时点, 累计同比)。中间缺期时不跨期比较。"""
    releases = []
    for index, (period, available_at, value) in enumerate(profit):
        previous = (
            profit[index - 1][2]
            if index and adjacent_periods(profit[index - 1][0], period)
            else None
        )
        revenue_value = revenue.get(period)
        signal = (
            None
            if value is None or previous is None or revenue_value is None
            else value - previous > 0 and revenue_value > 0
        )
        releases.append(Release(period, available_at, signal))
    return releases


def forward_excess(
    releases: Sequence[Release],
    asset: PriceLine,
    benchmark: PriceLine,
    trading_days: int,
    barriers: Sequence[date] = (),
) -> tuple[list[tuple[bool, float]], int, int]:
    """返回按时间排序的 (信号, 超额收益)、尚未走完窗口的发布数、跨越障碍日的窗口数。

    障碍日是疑似份额拆并日：入场后、退出前（含退出日）碰到即整段作废——
    拆并会把不复权价格机械地放大或缩小，不做复权就不得分。
    """
    common = sorted(set(asset.days) & set(benchmark.days))
    outcomes: list[tuple[bool, float]] = []
    pending = 0
    blocked = 0
    for release in releases:
        if release.signal is None:
            continue
        released = release.available_at.date()
        entry_index = next((i for i, day in enumerate(common) if day > released), None)
        if entry_index is None:
            pending += 1
            continue
        entry = common[entry_index]
        if (entry - released).days > MAX_ENTRY_GAP_DAYS:
            continue
        exit_index = entry_index + trading_days
        if exit_index >= len(common):
            pending += 1
            continue
        exit_day = common[exit_index]
        if any(entry < barrier <= exit_day for barrier in barriers):
            blocked += 1
            continue
        excess = asset.total_return(entry, exit_day) - benchmark.total_return(entry, exit_day)
        outcomes.append((release.signal, excess))
    return outcomes, pending, blocked


def _difference(signals: Sequence[bool], returns: Sequence[float]) -> float | None:
    on = [value for flag, value in zip(signals, returns, strict=True) if flag]
    off = [value for flag, value in zip(signals, returns, strict=True) if not flag]
    if not on or not off:
        return None
    return fmean(on) - fmean(off)


def compare(outcomes: Sequence[tuple[bool, float]]) -> dict[str, object]:
    signals = [flag for flag, _ in outcomes]
    returns = [value for _, value in outcomes]
    on = [value for flag, value in outcomes if flag]
    off = [value for flag, value in outcomes if not flag]
    result: dict[str, object] = {
        "n_on": len(on),
        "n_off": len(off),
        "mean_on": fmean(on) if on else None,
        "mean_off": fmean(off) if off else None,
        "hit_on": sum(value > 0 for value in on) / len(on) if on else None,
        "difference": None,
        "p_value": None,
        "q_value": None,
        "status": "INSUFFICIENT_SAMPLE",
    }
    if len(on) < MIN_EVENTS or len(off) < MIN_EVENTS:
        return result
    observed = _difference(signals, returns)
    assert observed is not None  # Both groups meet MIN_EVENTS above.
    count = len(signals)
    shifted = [
        _difference([signals[(i + shift) % count] for i in range(count)], returns)
        for shift in range(1, count)
    ]
    as_extreme = sum(value is not None and value >= observed for value in shifted)
    result.update(
        {
            "difference": observed,
            "p_value": (1 + as_extreme) / count,
            "status": "TESTED",
        }
    )
    return result


def benjamini_hochberg(p_values: Sequence[float]) -> list[float]:
    count = len(p_values)
    order = sorted(range(count), key=lambda index: p_values[index])
    adjusted = [1.0] * count
    running = 1.0
    for rank in range(count, 0, -1):
        index = order[rank - 1]
        running = min(running, p_values[index] * count / rank)
        adjusted[index] = running
    return adjusted


__all__ = [
    "HORIZONS",
    "MAX_ENTRY_GAP_DAYS",
    "MIN_EVENTS",
    "PriceLine",
    "Release",
    "adjacent_periods",
    "benjamini_hochberg",
    "compare",
    "forward_excess",
    "signal_history",
]
