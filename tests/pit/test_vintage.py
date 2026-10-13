"""T-003 数据修订与同日不同版本竞态测试（§19 PIT-003、DATA-002、§7.3 tie-break）。

版本选择必须有确定 tie-break，不能静默取最新修订。
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from industrypulse.contracts import (
    ResearchMode,
    SkipReason,
    effective_available_at,
    select_vintages_as_of,
)

SERIES = "cn.nbs.industry_c31.profit_ytd"


def test_pit_003_historical_query_picks_first_release(steel_vintages: list) -> None:
    """PIT-003：2025-07 首发 v1（2025-08-27）、修订 v2（2026-01-27）。
    as_of=2025-08-31 的历史查询必须选 v1=1220.5，不得取今日修订 1235.8。"""
    sel = select_vintages_as_of(
        steel_vintages,
        as_of=datetime.fromisoformat("2025-08-31T17:00:00+08:00"),
        mode=ResearchMode.STRICT_PIT,
    )
    row = sel.selected_for(SERIES, date(2025, 7, 1))
    assert row is not None
    assert row.vintage_id == 1 and str(row.value) == "1220.5"
    # 同期只入选一行
    same_period = [v for v in sel.selected if v.observation_start == date(2025, 7, 1)]
    assert len(same_period) == 1


def test_current_query_sees_latest_revision(steel_vintages: list) -> None:
    """as_of=2026-03（两版均已知）→ 取 available_at 更晚的 v2=1235.8。"""
    sel = select_vintages_as_of(
        steel_vintages,
        as_of=datetime.fromisoformat("2026-03-01T17:00:00+08:00"),
        mode=ResearchMode.STRICT_PIT,
    )
    row = sel.selected_for(SERIES, date(2025, 7, 1))
    assert row is not None
    assert row.vintage_id == 2 and str(row.value) == "1235.8"


def test_same_available_time_tie_break_by_vintage_id(steel_vintages: list) -> None:
    """同日不同版本竞态：2025-02 期 v1/v2 同一 available_at（2025-04-15 09:30），
    tie-break = vintage_id DESC，且对输入顺序不敏感。"""
    base = dict(
        as_of=datetime.fromisoformat("2025-06-30T17:00:00+08:00"),
        mode=ResearchMode.STRICT_PIT,
    )
    sel = select_vintages_as_of(steel_vintages, **base)
    row = sel.selected_for(SERIES, date(2025, 2, 1))
    assert row is not None and row.vintage_id == 2 and str(row.value) == "615.8"

    shuffled = list(steel_vintages)
    random.Random(42).shuffle(shuffled)
    sel_shuffled = select_vintages_as_of(shuffled, **base)
    row2 = sel_shuffled.selected_for(SERIES, date(2025, 2, 1))
    assert row2 == row, "tie-break 必须确定，与输入顺序无关"


def test_missing_row_value_is_null_not_zero(steel_vintages: list) -> None:
    """缺失行（value=None+missing_reason）在 EXPLORATORY 可见，且绝不为 0。"""
    sel = select_vintages_as_of(
        steel_vintages,
        as_of=datetime.fromisoformat("2025-07-31T17:00:00+08:00"),
        mode=ResearchMode.EXPLORATORY,
    )
    row = sel.selected_for(SERIES, date(2025, 3, 1))
    assert row is not None
    assert row.value is None
    assert row.missing_reason is not None
    assert str(row.missing_reason.value) == "not_collected"
    # 而在 STRICT 下该行因 value_evidence=D 被拒
    strict = select_vintages_as_of(
        steel_vintages,
        as_of=datetime.fromisoformat("2025-07-31T17:00:00+08:00"),
        mode=ResearchMode.STRICT_PIT,
    )
    skip = [s for s in strict.skipped if s.observation_start == date(2025, 3, 1)]
    assert skip and skip[0].reason == SkipReason.INSUFFICIENT_VALUE_EVIDENCE


@settings(max_examples=200, deadline=None)
@given(
    as_of=st.datetimes(
        min_value=datetime(2025, 1, 1, 0, 0),
        max_value=datetime(2027, 1, 1, 0, 0),
        timezones=st.just(UTC),
    ),
    mode=st.sampled_from(ResearchMode),
)
def test_selection_never_leaks_future_knowledge(steel_vintages: list, as_of, mode) -> None:
    """属性测试：任意 as_of × 任意模式，入选行有效可知时点必须 ≤ as_of。"""
    sel = select_vintages_as_of(steel_vintages, as_of=as_of, mode=mode)
    for v in sel.selected:
        eff = effective_available_at(v, mode)
        assert eff <= as_of, f"{mode} @ {as_of}: {v.period_label} 泄漏（eff={eff}）"
    # 每个周期至多一行
    keys = [(v.series_id, v.observation_start) for v in sel.selected]
    assert len(keys) == len(set(keys))


@settings(max_examples=50, deadline=None)
@given(seed=st.integers(0, 2**32 - 1))
def test_selection_deterministic_under_permutation(steel_vintages: list, seed: int) -> None:
    """属性测试：任意打乱输入顺序，选择结果逐字节一致（DATA-002 幂等语义）。"""
    as_of = datetime.fromisoformat("2026-06-30T17:00:00+08:00")
    shuffled = list(steel_vintages)
    random.Random(seed).shuffle(shuffled)
    a = select_vintages_as_of(steel_vintages, as_of=as_of, mode=ResearchMode.STRICT_PIT)
    b = select_vintages_as_of(shuffled, as_of=as_of, mode=ResearchMode.STRICT_PIT)
    assert a.selected == b.selected
    assert a.skipped == b.skipped
