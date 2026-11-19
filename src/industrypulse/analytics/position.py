"""量化位置与高危预警分析（V2 CheckList 硬门槛版）。

核心原则：
1. 横轴（催化强度）看【真实硬异动 + 盘面逆势抗跌 + 新闻异常增量】，不给普通日常新闻虚高打分；
2. 纵轴（大周期位置）拉长至 180 个交易日，彻底拆开【左侧急跌 (FALLING)】与【低位企稳 (LURKING)】；
3. 允许大多数行业在大环境差时处于 FALLING 或 NEUTRAL，不强行凑出潜伏机会。
"""

from __future__ import annotations

import math
from typing import Any


def _pct_return(series: list[float], days: int) -> float | None:
    if len(series) <= days:
        return None
    base = series[-1 - days]
    if base <= 0:
        return None
    return round((series[-1] / base - 1.0) * 100.0, 2)


def _percentile(value: float, window: list[float]) -> float:
    if len(window) <= 1:
        return 50.0
    below = sum(1 for item in window if item < value)
    equal = sum(1 for item in window if item == value)
    return round(((below + 0.5 * (equal - 1)) / max(1, len(window) - 1)) * 100.0, 1)


def _volatility_annual_pct(closes: list[float], window: int = 20) -> float | None:
    if len(closes) < window + 1:
        return None
    sample = closes[-(window + 1) :]
    returns: list[float] = []
    for idx in range(1, len(sample)):
        prev, curr = sample[idx - 1], sample[idx]
        if prev > 0:
            returns.append(curr / prev - 1.0)
    if len(returns) < 10:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return round(math.sqrt(variance) * math.sqrt(252.0) * 100.0, 2)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def compute_position_radar(
    *,
    closes: list[float],
    amounts: list[float] | None = None,
    benchmark_closes: list[float] | None = None,
    heat: float | None = None,
    news_count_24h: int = 0,
    news_count_prev_24h: int = 0,
    hard_signal_count: int = 0,
    overseas_lead_20d: float | None = None,
    overseas_avg_20d: float | None = None,
    extra_catalyst_boost: float = 0.0,
) -> dict[str, Any]:
    """根据 180 日行情、量价结构与真实硬异动计算量化状态与四象限坐标。"""
    _ = heat  # 保留参数签名兼容调用方，不再用日常水文总量虚高抬催化分
    clean_closes = [float(x) for x in closes if x and x > 0]
    if len(clean_closes) < 10:
        base_cat = _clamp(hard_signal_count * 22.0 + extra_catalyst_boost)
        return {
            "available": False,
            "zone": "UNKNOWN",
            "zone_label": "数据不足",
            "position_score": None,
            "catalyst_score": round(base_cat, 1),
            "hard_signal_count": hard_signal_count,
            "anti_drop": False,
            "falling_knife": False,
            "risk_flags": ["历史行情不足 10 个交易日，暂无法计算位置分位"],
        }

    window_closes = clean_closes[-180:]
    price_pct = _percentile(clean_closes[-1], window_closes)
    ret_5d = _pct_return(clean_closes, 5)
    ret_20d = _pct_return(clean_closes, 20)
    ret_60d = _pct_return(clean_closes, 59 if len(clean_closes) == 60 else 60)

    clean_bench = [float(x) for x in (benchmark_closes or []) if x and x > 0]
    bench_ret_5d = _pct_return(clean_bench, 5)
    bench_ret_20d = _pct_return(clean_bench, 20)
    relative_5d = (
        round(ret_5d - bench_ret_5d, 2) if ret_5d is not None and bench_ret_5d is not None else None
    )
    relative_20d = (
        round(ret_20d - bench_ret_20d, 2)
        if ret_20d is not None and bench_ret_20d is not None
        else None
    )

    clean_amounts = [float(x) for x in (amounts or []) if x is not None and x > 0]
    turnover_pct: float | None = None
    turnover_z: float | None = None
    if len(clean_amounts) >= 20:
        win_amt = clean_amounts[-180:]
        recent_amt = sum(win_amt[-5:]) / min(5, len(win_amt))
        turnover_pct = _percentile(recent_amt, win_amt)
        mean_amt = sum(win_amt) / len(win_amt)
        std_amt = math.sqrt(sum((a - mean_amt) ** 2 for a in win_amt) / len(win_amt)) or 1.0
        turnover_z = round((recent_amt - mean_amt) / std_amt, 2)

    vol_20d = _volatility_annual_pct(clean_closes, 20)

    # 纵轴：综合市场位置与拥挤度评分 (0-100)
    slope_score = _clamp(50.0 + (ret_20d or 0.0) * 1.8)
    if turnover_pct is not None:
        position_score = round(0.55 * price_pct + 0.30 * turnover_pct + 0.15 * slope_score, 1)
    else:
        position_score = round(0.75 * price_pct + 0.25 * slope_score, 1)

    # 盘面微观行为异动：逆势抗跌 / 左侧急跌
    anti_drop = bool(
        (relative_20d is not None and relative_20d >= 3.0 and (ret_5d is None or ret_5d >= -1.0))
        or (
            bench_ret_5d is not None
            and bench_ret_5d <= -1.5
            and relative_5d is not None
            and relative_5d >= 2.0
        )
    )
    falling_knife = bool(
        (ret_20d is not None and ret_20d <= -5.0 and (ret_5d is None or ret_5d < -0.5))
        or (ret_60d is not None and ret_60d <= -12.0 and (ret_5d is None or ret_5d < 0.0))
    )

    # 横轴：异常催化分（以硬异动和盘面逆势为主，常规新闻数量只给低基线）
    if news_count_prev_24h > 0:
        surge_ratio = (news_count_24h - news_count_prev_24h) / news_count_prev_24h
        news_anomaly = _clamp(8.0 + max(0.0, surge_ratio) * 18.0, 0.0, 26.0)
    else:
        news_anomaly = 12.0 if news_count_24h > 0 else 0.0
    hard_score = _clamp(hard_signal_count * 22.0, 0.0, 66.0)
    overseas_score = (
        12.0
        if (
            overseas_lead_20d is not None
            and overseas_lead_20d >= 6.0
            and (overseas_avg_20d is None or overseas_avg_20d >= 2.0)
        )
        else 0.0
    )
    anti_drop_score = 22.0 if anti_drop else 0.0
    catalyst_score = round(
        _clamp(news_anomaly + hard_score + overseas_score + anti_drop_score + extra_catalyst_boost),
        1,
    )

    flags: list[str] = []
    if price_pct >= 80:
        flags.append(f"180日价格处于 {price_pct}% 高位区间")
    elif price_pct <= 25:
        flags.append(f"180日价格处于 {price_pct}% 低位区间")

    if turnover_pct is not None:
        if turnover_pct >= 85:
            flags.append(f"成交拥挤度达 {turnover_pct}% 分位")
        elif turnover_pct <= 25:
            flags.append(f"成交处于 {turnover_pct}% 极冷区间")

    if anti_drop:
        flags.append("大盘偏弱背景下出现逆势抗跌/相对走强信号")
    if falling_knife:
        flags.append(f"近20日下跌 {ret_20d}% 且未见止跌，谨防左侧接飞刀")

    stall_divergence = (
        turnover_z is not None
        and turnover_z >= 1.5
        and ret_5d is not None
        and ret_5d <= 0.5
        and price_pct >= 70
    )
    if stall_divergence:
        flags.append("高位放量滞涨（量增价平，警惕资金兑现）")
    if vol_20d is not None and vol_20d >= 40:
        flags.append(f"20日年化波动率偏高（{vol_20d}%）")
    if ret_20d is not None and ret_20d >= 18:
        flags.append(f"近20日涨幅已达 {ret_20d}%，短期不宜追高")

    # 6 档硬门槛 CheckList 判定
    weak_market = bench_ret_20d is not None and bench_ret_20d <= -2.5
    high_risk = (
        position_score >= 78
        and (
            stall_divergence
            or (turnover_pct is not None and turnover_pct >= 85 and (ret_20d or 0.0) >= 15)
            or (vol_20d is not None and vol_20d >= 40 and price_pct >= 85)
        )
    ) or (stall_divergence and position_score >= 70)

    has_real_catalyst = hard_signal_count >= 1 or anti_drop or catalyst_score >= 52.0
    low_and_stable = position_score <= 48.0 and not falling_knife
    if weak_market:
        low_and_stable = low_and_stable and (
            anti_drop or (relative_20d is not None and relative_20d >= 0.0)
        )
    not_crowded = turnover_pct is None or turnover_pct <= 75.0

    if high_risk:
        zone, label = "HIGH_RISK", "高危离场警示"
    elif position_score >= 70.0 or (ret_20d is not None and ret_20d >= 18.0):
        zone, label = "CROWDED", "高位拥挤"
    elif falling_knife and not anti_drop:
        zone, label = "FALLING", "左侧阴跌（慎接飞刀）"
    elif has_real_catalyst and low_and_stable and not_crowded:
        zone, label = "LURKING", "底部潜伏窗口"
    elif (
        position_score < 70.0
        and (relative_20d is None or relative_20d > 0.0)
        and (ret_5d or 0.0) >= 0.0
    ):
        zone, label = "RISING", "趋势走强"
    else:
        zone, label = "NEUTRAL", "观望"

    return {
        "available": True,
        "zone": zone,
        "zone_label": label,
        "position_score": position_score,
        "catalyst_score": catalyst_score,
        "price_percentile_60d": price_pct,
        "turnover_percentile_60d": turnover_pct,
        "turnover_z": turnover_z,
        "return_5d": ret_5d,
        "return_20d": ret_20d,
        "return_60d": ret_60d,
        "relative_20d": relative_20d,
        "volatility_20d": vol_20d,
        "divergence": stall_divergence,
        "anti_drop": anti_drop,
        "falling_knife": falling_knife,
        "hard_signal_count": hard_signal_count,
        "overseas_lead_20d": overseas_lead_20d,
        "overseas_avg_20d": overseas_avg_20d,
        "risk_flags": flags,
    }
