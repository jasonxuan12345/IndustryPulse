"""T-002 强制 PIT 发布日期/数值版本泄漏测试（§19 PIT-001..PIT-006、PIT-010）。

反向插未来公告必然失败：本套全部用合成 fixture（C-009）驱动合同级
as-of 语义（industrypulse.contracts.pit）。D 线仓储实现上线后，本套
负例必须原样对 SQL 实现重放（§7.3 语义一致）。
"""

from __future__ import annotations

from datetime import date, datetime

from industrypulse.contracts import (
    ResearchMode,
    SkipReason,
    select_vintages_as_of,
)

SERIES = "cn.nbs.industry_c31.profit_ytd"


def _as_of(s: str) -> datetime:
    return datetime.fromisoformat(s)


def test_pit_001_publication_minute_invisible(steel_vintages: list) -> None:
    """PIT-001：公告 2025-09-28 09:30，as_of 09:29 → 该值不可见。"""
    sel = select_vintages_as_of(
        steel_vintages, as_of=_as_of("2025-09-28T09:29:00+08:00"), mode=ResearchMode.STRICT_PIT
    )
    assert sel.selected_for(SERIES, date(2025, 8, 1)) is None
    skip = [
        s for s in sel.skipped if s.series_id == SERIES and s.observation_start == date(2025, 8, 1)
    ]
    assert skip and skip[0].reason == SkipReason.FUTURE_RELEASE


def test_pit_002_next_day_visible(steel_vintages: list, trading_days: list) -> None:
    """PIT-002：同一公告，as_of 2025-09-29 17:00（日期证据已确认）→ 可见。"""
    sel = select_vintages_as_of(
        steel_vintages,
        as_of=_as_of("2025-09-29T17:00:00+08:00"),
        mode=ResearchMode.STRICT_PIT,
        trading_days=trading_days,
    )
    row = sel.selected_for(SERIES, date(2025, 8, 1))
    assert row is not None and str(row.value) == "1301.7"


def test_future_release_invisible_at_month_end_snapshot(steel_vintages: list) -> None:
    """未来发布：2025-10 期（11-27 发布）在 10-31 月末快照不可见，且不给零值。"""
    sel = select_vintages_as_of(
        steel_vintages, as_of=_as_of("2025-10-31T17:00:00+08:00"), mode=ResearchMode.STRICT_PIT
    )
    assert sel.selected_for(SERIES, date(2025, 10, 1)) is None
    skip = [s for s in sel.skipped if s.observation_start == date(2025, 10, 1)]
    assert skip and skip[0].reason == SkipReason.FUTURE_RELEASE


def test_pit_004_revised_value_with_old_release_date(
    steel_vintages: list, trading_days: list
) -> None:
    """PIT-004：只有今日修订值+旧发布日期 → STRICT 拒收；RECONSTRUCTED 允许并可见。"""
    period = date(2025, 4, 1)
    strict = select_vintages_as_of(
        steel_vintages,
        as_of=_as_of("2025-06-30T17:00:00+08:00"),
        mode=ResearchMode.STRICT_PIT,
        trading_days=trading_days,
    )
    assert strict.selected_for(SERIES, period) is None
    skip = [s for s in strict.skipped if s.observation_start == period]
    assert skip and skip[0].reason == SkipReason.INSUFFICIENT_VALUE_EVIDENCE

    recon = select_vintages_as_of(
        steel_vintages,
        as_of=_as_of("2025-06-30T17:00:00+08:00"),
        mode=ResearchMode.RECONSTRUCTED_PIT,
        trading_days=trading_days,
    )
    row = recon.selected_for(SERIES, period)
    # B 级（只知日期）发布证据：次一交易日 2025-05-28 09:30 后可用
    assert row is not None and str(row.value) == "980.2"


def test_pit_005_policy_assumption_needs_conservative_lag(steel_vintages: list) -> None:
    """PIT-005：只有"通常月底发布"假设 → STRICT 拒收；重建模式按保守滞后放行。"""
    period = date(2025, 6, 1)
    strict = select_vintages_as_of(
        steel_vintages, as_of=_as_of("2025-09-30T17:00:00+08:00"), mode=ResearchMode.STRICT_PIT
    )
    assert strict.selected_for(SERIES, period) is None
    skip = [s for s in strict.skipped if s.observation_start == period]
    assert skip and skip[0].reason == SkipReason.INSUFFICIENT_RELEASE_EVIDENCE

    # 保守滞后 5 日：推算可用日 2025-08-01 + 5d = 08-06
    before = select_vintages_as_of(
        steel_vintages,
        as_of=_as_of("2025-08-05T23:59:00+08:00"),
        mode=ResearchMode.RECONSTRUCTED_PIT,
        conservative_lag_days=5,
    )
    assert before.selected_for(SERIES, period) is None
    lag_skip = [s for s in before.skipped if s.observation_start == period]
    assert lag_skip and lag_skip[0].reason == SkipReason.CONSERVATIVE_LAG_NOT_ELAPSED

    after = select_vintages_as_of(
        steel_vintages,
        as_of=_as_of("2025-08-07T00:00:00+08:00"),
        mode=ResearchMode.RECONSTRUCTED_PIT,
        conservative_lag_days=5,
    )
    row = after.selected_for(SERIES, period)
    assert row is not None and str(row.value) == "1095.0"


def test_pit_006_style_retrieval_only_row_never_historical(steel_vintages: list) -> None:
    """只知抓取时点（release D / basis retrieval_time）：STRICT 与 RECONSTRUCTED
    一律 BASIS_NOT_ALLOWED，只有 EXPLORATORY 可见——防"抓到即当历史可知"。"""
    period = date(2025, 11, 1)
    for mode in (ResearchMode.STRICT_PIT, ResearchMode.RECONSTRUCTED_PIT):
        sel = select_vintages_as_of(
            steel_vintages, as_of=_as_of("2026-12-31T23:00:00+08:00"), mode=mode
        )
        assert sel.selected_for(SERIES, period) is None
        skip = [s for s in sel.skipped if s.observation_start == period]
        assert skip and skip[0].reason == SkipReason.BASIS_NOT_ALLOWED
    expl = select_vintages_as_of(
        steel_vintages, as_of=_as_of("2026-12-31T23:00:00+08:00"), mode=ResearchMode.EXPLORATORY
    )
    assert expl.selected_for(SERIES, period) is not None


def test_pit_010_snippet_candidate_not_publishable(evidence_candidates: list) -> None:
    """PIT-010：摘要有数值但原网页抓取失败 → 候选 FETCH_FAILED，不得进事实表。"""
    snippet = [c for c in evidence_candidates if c.candidate_id == "cand-profit-202507-snippet"]
    assert len(snippet) == 1
    cand = snippet[0]
    assert cand.verification_status.value == "fetch_failed"
    assert cand.raw_sha256 is None and cand.parsed_value is None
    # 没有任何 vintage 行引用该候选（fixture 中 2025-07 v1 引用的是 verified 候选）
    conflict = [c for c in evidence_candidates if c.verification_status.value == "needs_review"]
    assert conflict, "冲突对必须留在人工队列"


def test_three_modes_return_distinct_information_sets(
    steel_vintages: list, trading_days: list
) -> None:
    """C-004 验收：同一 fixture 三模式输出不同信息集（§6.5 行为一致）。"""
    as_of = _as_of("2025-09-30T17:00:00+08:00")
    strict = select_vintages_as_of(
        steel_vintages, as_of=as_of, mode=ResearchMode.STRICT_PIT, trading_days=trading_days
    )
    recon = select_vintages_as_of(
        steel_vintages, as_of=as_of, mode=ResearchMode.RECONSTRUCTED_PIT, trading_days=trading_days
    )
    expl = select_vintages_as_of(
        steel_vintages, as_of=as_of, mode=ResearchMode.EXPLORATORY, trading_days=trading_days
    )
    s_periods = {v.observation_start for v in strict.selected}
    r_periods = {v.observation_start for v in recon.selected}
    e_periods = {v.observation_start for v in expl.selected}
    # 严格 ⊆ 重建 ⊆ 探索（证据门槛递放宽；可用时点只可能更晚）
    assert s_periods <= r_periods, (
        f"STRICT 信息集必须是 RECONSTRUCTED 子集：{s_periods - r_periods}"
    )
    assert r_periods <= e_periods, f"RECONSTRUCTED 必须是 EXPLORATORY 子集：{r_periods - e_periods}"
    # 三者确有差异（PIT-004 行只在重建出现；缺失行只在探索出现）
    assert r_periods - s_periods >= {date(2025, 4, 1)}
    assert e_periods - r_periods >= {date(2025, 3, 1)}
    # 任何模式下，入选行的可知时点不得晚于 as_of（无泄漏全局断言）
    from industrypulse.contracts import effective_available_at

    for sel in (strict, recon, expl):
        for v in sel.selected:
            eff = effective_available_at(v, sel.mode)
            assert eff <= as_of, f"{sel.mode}: {v.period_label} 泄漏（eff={eff} > as_of）"
