"""量化位置与高危预警分析：把价格位置、成交拥挤度、相对强弱、波动率合成客观标尺。

定位：不做无依据的涨跌预测，而是辅助回答两个问题：
1. 当前是不是“别人还没入场”的早期低位（LURKING）？
2. 是否已经进入“人声鼎沸/放量滞涨”的高危拥挤区（HIGH_RISK / CROWDED）？
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
    if not window:
        return 50.0
    if len(window) == 1:
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
    extra_catalyst_boost: float = 0.0,
) -> dict[str, Any]:
    """根据行业收盘价、成交额与信息热度计算量化位置、拥挤度及高危预警。"""
    clean_closes = [float(x) for x in closes if x and x > 0]
    if len(clean_closes) < 10:
        return {
            "available": False,
            "zone": "UNKNOWN",
            "zone_label": "数据不足",
            "position_score": None,
            "catalyst_score": round(_clamp((heat or 0.0) + extra_catalyst_boost), 1),
            "risk_flags": ["历史行情不足 10 个交易日，暂无法计算位置分位"],
        }

    window_closes = clean_closes[-60:]
    price_pct = _percentile(clean_closes[-1], window_closes)
    ret_5d = _pct_return(clean_closes, 5)
    ret_20d = _pct_return(clean_closes, 20)
    ret_60d = _pct_return(clean_closes, 59 if len(clean_closes) == 60 else 60)

    bench_ret_20d = _pct_return([float(x) for x in (benchmark_closes or []) if x and x > 0], 20)
    relative_20d = (
        round(ret_20d - bench_ret_20d, 2)
        if ret_20d is not None and bench_ret_20d is not None
        else None
    )

    clean_amounts = [float(x) for x in (amounts or []) if x is not None and x > 0]
    turnover_pct: float | None = None
    turnover_z: float | None = None
    if len(clean_amounts) >= 20:
        win_amt = clean_amounts[-60:]
        recent_amt = sum(win_amt[-5:]) / min(5, len(win_amt))
        turnover_pct = _percentile(recent_amt, win_amt)
        mean_amt = sum(win_amt) / len(win_amt)
        std_amt = math.sqrt(sum((a - mean_amt) ** 2 for a in win_amt) / len(win_amt)) or 1.0
        turnover_z = round((recent_amt - mean_amt) / std_amt, 2)

    vol_20d = _volatility_annual_pct(clean_closes, 20)

    # 综合市场位置与拥挤度评分 (0-100)
    slope_score = _clamp(50.0 + (ret_20d or 0.0) * 2.0)
    if turnover_pct is not None:
        position_score = round(0.5 * price_pct + 0.35 * turnover_pct + 0.15 * slope_score, 1)
    else:
        position_score = round(0.75 * price_pct + 0.25 * slope_score, 1)

    # 信息催化分 (0-100)
    news_scale = _clamp((math.log1p(news_count_24h) / math.log1p(60)) * 100.0)
    if news_count_prev_24h > 0:
        growth = (news_count_24h - news_count_prev_24h) / news_count_prev_24h
        news_growth = _clamp((growth + 0.5) * 50.0)
    else:
        news_growth = 55.0 if news_count_24h > 0 else 0.0
    base_catalyst = (
        0.5 * (heat if heat is not None else news_scale) + 0.3 * news_scale + 0.2 * news_growth
    )
    catalyst_score = round(_clamp(base_catalyst + extra_catalyst_boost), 1)

    flags: list[str] = []
    if price_pct >= 80:
        flags.append(f"近60日价格处于 {price_pct}% 高分位")
    elif price_pct <= 25:
        flags.append(f"近60日价格处于 {price_pct}% 低位区间")

    if turnover_pct is not None:
        if turnover_pct >= 85:
            flags.append(f"成交拥挤度达 {turnover_pct}% 分位")
        elif turnover_pct <= 30:
            flags.append(f"成交额处于 {turnover_pct}% 冷清低位")

    stall_divergence = (
        turnover_z is not None
        and turnover_z >= 1.5
        and ret_5d is not None
        and ret_5d <= 0.5
        and price_pct >= 70
    )
    if stall_divergence:
        flags.append("高位放量滞涨（量增价平，警惕资金兑现）")
    if vol_20d is not None and vol_20d >= 35:
        flags.append(f"20日年化波动率偏高（{vol_20d}%）")
    if ret_20d is not None and ret_20d >= 18:
        flags.append(f"近20日涨幅已达 {ret_20d}%，注意短期追高风险")

    # 四档位置判定
    high_risk = (
        position_score >= 80
        and (
            (turnover_pct is not None and turnover_pct >= 85)
            or stall_divergence
            or (vol_20d is not None and vol_20d >= 35)
        )
    ) or (stall_divergence and position_score >= 72)

    if high_risk:
        zone, label = "HIGH_RISK", "高危离场警示"
    elif position_score >= 68 or (ret_20d is not None and ret_20d >= 18):
        zone, label = "CROWDED", "拥挤警示"
    elif position_score <= 42 and catalyst_score >= 40:
        zone, label = "LURKING", "早期潜伏区"
    elif 42 < position_score < 68 and (relative_20d is None or relative_20d > 0):
        zone, label = "RISING", "温和启动"
    else:
        zone, label = "NEUTRAL", "冷门观望"

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
        "risk_flags": flags,
    }
