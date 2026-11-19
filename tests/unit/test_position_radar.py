from __future__ import annotations

from industrypulse.analytics.position import compute_position_radar


def test_insufficient_history_returns_unknown() -> None:
    res = compute_position_radar(closes=[100.0, 101.0, 102.0], news_count_24h=5)
    assert res["available"] is False
    assert res["zone"] == "UNKNOWN"


def test_lurking_zone_when_low_position_and_high_catalyst() -> None:
    # 先高后低：近60日价格回落至低位区间，成交额冷清，但近24h新闻催化显著增加
    closes = [120.0 - i * 0.4 for i in range(55)] + [98.0, 98.2, 98.1, 98.3, 98.5]
    amounts = [100.0 for _ in range(55)] + [40.0, 42.0, 41.0, 39.0, 40.0]
    bench = [100.0 for _ in range(60)]
    res = compute_position_radar(
        closes=closes,
        amounts=amounts,
        benchmark_closes=bench,
        heat=75.0,
        news_count_24h=25,
        news_count_prev_24h=5,
    )
    assert res["available"] is True
    assert res["zone"] == "LURKING"
    assert res["position_score"] <= 42
    assert res["catalyst_score"] >= 40


def test_high_risk_zone_when_crowded_and_stalling() -> None:
    # 持续大涨至最高分位，最后5天放巨量但价格滞涨
    closes = [100.0 + i * 1.2 for i in range(55)] + [165.0, 165.2, 164.8, 165.1, 165.0]
    amounts = [50.0 for _ in range(55)] + [220.0, 240.0, 235.0, 250.0, 260.0]
    bench = [100.0 for _ in range(60)]
    res = compute_position_radar(
        closes=closes,
        amounts=amounts,
        benchmark_closes=bench,
        heat=60.0,
        news_count_24h=8,
        news_count_prev_24h=12,
    )
    assert res["available"] is True
    assert res["zone"] == "HIGH_RISK"
    assert res["divergence"] is True
    assert any("滞涨" in flag for flag in res["risk_flags"])
