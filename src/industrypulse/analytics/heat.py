"""透明热度分（§10）：只使用真实输入，输出分数与可解释分量。

HeatScore = 40*新闻规模 + 20*新闻增速 + 30*成交活跃度 + 10*价格波动
各分量先归一到 0-1，再加权；输入缺失时对应分量记 0 并在 components 里注明。
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any


def _squash(value: float) -> float:
    return max(0.0, min(1.0, value))


def compute_heat(
    *,
    news_count_24h: int,
    news_count_prev_24h: int,
    amounts: list[float],
    change_pct: float | None,
) -> tuple[float, dict[str, Any]]:
    """返回 (0-100 分数, 分量明细)。amounts 为该行业近期日成交额序列（升序）。"""
    components: dict[str, Any] = {}

    news_scale = _squash(math.log1p(news_count_24h) / math.log1p(120))
    components["news_scale"] = {
        "value": round(news_scale, 3),
        "input": {"count_24h": news_count_24h},
        "weight": 0.4,
    }

    if news_count_prev_24h > 0:
        growth = (news_count_24h - news_count_prev_24h) / news_count_prev_24h
        news_growth = _squash((growth + 1) / 4)
    else:
        news_growth = 0.5 if news_count_24h > 0 else 0.0
    components["news_growth"] = {
        "value": round(news_growth, 3),
        "input": {"count_prev_24h": news_count_prev_24h},
        "weight": 0.2,
    }

    if len(amounts) >= 21:
        recent = amounts[-5:]
        window = amounts[-60:]
        mean = sum(window) / len(window)
        variance = sum((item - mean) ** 2 for item in window) / len(window)
        std = math.sqrt(variance) or 1.0
        latest = sum(recent) / len(recent)
        z = (latest - mean) / std
        activity = _squash((z + 2) / 4)
        components["turnover_activity"] = {
            "value": round(activity, 3),
            "input": {"recent_mean": round(latest, 2), "window_mean": round(mean, 2), "z": round(z, 2)},
            "weight": 0.3,
        }
    else:
        activity = 0.0
        components["turnover_activity"] = {
            "value": 0.0,
            "input": {"samples": len(amounts), "note": "成交额历史不足 21 日"},
            "weight": 0.3,
        }

    shock = _squash((abs(change_pct) / 4) if change_pct is not None else 0)
    components["price_shock"] = {
        "value": round(shock, 3),
        "input": {"change_pct": change_pct},
        "weight": 0.1,
    }

    score = (
        40 * news_scale + 20 * news_growth + 30 * activity + 10 * shock
    )
    return round(score, 1), components


def heat_change_series(
    history: dict[date, float], today: date | None = None
) -> float | None:
    """与前一日热度差（用于 heat_change）。"""
    today = today or date.today()
    current = history.get(today) or history.get(today - timedelta(days=3))
    for offset in range(1, 8):
        previous = history.get(today - timedelta(days=offset))
        if current is not None and previous is not None:
            return round(current - previous, 1)
    return None
