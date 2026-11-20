from __future__ import annotations

from industrypulse.analytics.position import compute_position_radar


def test_insufficient_history_returns_unknown() -> None:
    res = compute_position_radar(closes=[100.0, 101.0, 102.0], news_count_24h=5)
    assert res["available"] is False
    assert res["zone"] == "UNKNOWN"


def test_falling_knife_at_zero_percentile_is_not_lurking() -> None:
    # 持续下跌至 0% 分位且近5天仍在下跌：必须判定为 FALLING，绝不标成 LURKING
    closes = [120.0 - i * 0.6 for i in range(60)]
    amounts = [100.0 for _ in range(60)]
    bench = [100.0 for _ in range(60)]
    res = compute_position_radar(
        closes=closes,
        amounts=amounts,
        benchmark_closes=bench,
        news_count_24h=80,
        news_count_prev_24h=80,
    )
    assert res["available"] is True
    assert res["falling_knife"] is True
    assert res["zone"] == "FALLING"


def test_routine_news_alone_cannot_trigger_lurking() -> None:
    # 止跌低位但只有日常稳定数量新闻、没有硬异动和异常突增：不能进入 LURKING
    closes = [120.0 - i * 0.4 for i in range(50)] + [100.0 for _ in range(10)]
    amounts = [100.0 for _ in range(55)] + [50.0 for _ in range(5)]
    bench = [100.0 for _ in range(60)]
    res = compute_position_radar(
        closes=closes,
        amounts=amounts,
        benchmark_closes=bench,
        news_count_24h=50,
        news_count_prev_24h=50,
        hard_signal_count=0,
    )
    assert res["zone"] != "LURKING"
    assert res["catalyst_score"] < 40


def test_lurking_requires_hard_signal_and_stabilization() -> None:
    # 先跌后止跌横盘 + 有硬异动命中 + 成交未拥挤 -> 底部潜伏窗口
    closes = (
        [120.0 - i * 0.4 for i in range(50)] + [100.0, 100.2, 100.1, 100.4, 100.6] + [100.8] * 5
    )
    amounts = [100.0 for _ in range(55)] + [45.0, 46.0, 44.0, 47.0, 48.0]
    bench = [100.0 for _ in range(60)]
    res = compute_position_radar(
        closes=closes,
        amounts=amounts,
        benchmark_closes=bench,
        news_count_24h=20,
        news_count_prev_24h=10,
        hard_signal_count=2,
        overseas_lead_20d=8.5,
    )
    assert res["available"] is True
    assert res["falling_knife"] is False
    assert res["zone"] == "LURKING"
    assert res["catalyst_score"] >= 55


def test_anti_drop_in_weak_market() -> None:
    # 沪深300 下跌 5%，行业走平抗跌
    closes = [100.0 for _ in range(55)] + [100.2, 100.4, 100.3, 100.5, 100.8]
    bench = [100.0 for _ in range(40)] + [100.0 - i * 0.25 for i in range(20)]
    res = compute_position_radar(closes=closes, benchmark_closes=bench)
    assert res["anti_drop"] is True


def test_high_risk_zone_when_crowded_and_stalling() -> None:
    closes = [100.0 + i * 1.2 for i in range(55)] + [165.0, 165.2, 164.8, 165.1, 165.0]
    amounts = [50.0 for _ in range(55)] + [220.0, 240.0, 235.0, 250.0, 260.0]
    bench = [100.0 for _ in range(60)]
    res = compute_position_radar(
        closes=closes,
        amounts=amounts,
        benchmark_closes=bench,
        news_count_24h=8,
        news_count_prev_24h=12,
    )
    assert res["available"] is True
    assert res["zone"] == "HIGH_RISK"
    assert res["divergence"] is True
    assert any("滞涨" in flag for flag in res["risk_flags"])


def test_negative_overseas_avg_does_not_award_lead_bonus() -> None:
    closes = [100.0 for _ in range(60)]
    res_neg = compute_position_radar(
        closes=closes,
        overseas_lead_20d=7.5,
        overseas_avg_20d=-8.9,
    )
    res_pos = compute_position_radar(
        closes=closes,
        overseas_lead_20d=7.5,
        overseas_avg_20d=6.2,
    )
    assert res_neg["catalyst_score"] == 0.0
    assert res_pos["catalyst_score"] == 12.0


def test_commodity_crash_warning_blocks_lurking() -> None:
    closes = (
        [120.0 - i * 0.4 for i in range(50)] + [100.0, 100.2, 100.1, 100.4, 100.6] + [100.8] * 5
    )
    amounts = [100.0 for _ in range(55)] + [45.0, 46.0, 44.0, 47.0, 48.0]
    bench = [100.0 for _ in range(60)]
    res = compute_position_radar(
        closes=closes,
        amounts=amounts,
        benchmark_closes=bench,
        hard_signal_count=2,
        commodity_warnings=[{"title": "沪铜(CU0) -5.07%"}],
    )
    assert res["zone"] != "LURKING"
    assert res["commodity_warning_count"] == 1
    assert any("大宗期货大跌证伪" in flag for flag in res["risk_flags"])
