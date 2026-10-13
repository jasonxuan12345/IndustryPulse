"""C-009 验收：tests/fixtures/g0 全部文件可被合同模型加载且场景齐全。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from industrypulse.contracts import AvailabilityBasis, EvidenceGrade

FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "g0"

EXPECTED_FIXTURES = {
    "steel_profit_vintages",
    "market_calendar",
    "etf_universe",
    "market_bars",
    "corporate_actions",
    "evidence_candidates",
    "steel_taxonomy",
}


def test_fixture_inventory_complete() -> None:
    present = {p.stem for p in FIXTURES_DIR.glob("*.json")}
    assert EXPECTED_FIXTURES <= present, f"缺 fixture：{EXPECTED_FIXTURES - present}"


def test_fixture_covers_required_scenarios(steel_vintages: list) -> None:
    """C-009 验收清单：未来发布、修订、假日（日历）、跨年、退市、B/C/D 证据。"""
    assert len(steel_vintages) >= 12
    bases = {v.availability_basis for v in steel_vintages}
    assert bases == {
        AvailabilityBasis.OBSERVED_PUBLICATION,
        AvailabilityBasis.ASSUMED_POLICY_LAG,
        AvailabilityBasis.RETRIEVAL_TIME,
    }
    grades = {(v.release_evidence, v.value_evidence) for v in steel_vintages}
    assert (EvidenceGrade.B, EvidenceGrade.C) in grades, "缺 PIT-004 场景（旧发布日+今日修订值）"
    assert (EvidenceGrade.C, EvidenceGrade.B) in grades, "缺 PIT-005 场景（发布日历推算）"
    assert (EvidenceGrade.D, EvidenceGrade.C) in grades, "缺抓取时点兜底场景"
    # 修订共存（同期≥2版本）
    from collections import Counter

    per_period = Counter((v.series_id, v.observation_start) for v in steel_vintages)
    assert max(per_period.values()) >= 2, "缺修订共存场景"
    # 跨年 YTD 重置：12 月与次年 1 月都在
    periods = {v.observation_start for v in steel_vintages}
    assert date(2024, 12, 1) in periods and date(2025, 1, 1) in periods
    # 缺失行存在
    assert any(v.value is None for v in steel_vintages)


def test_calendar_covers_holiday_boundaries(trading_days: list) -> None:
    assert date(2025, 9, 30) in trading_days
    assert date(2025, 10, 1) not in trading_days  # 国庆假期开始
    assert date(2025, 10, 8) not in trading_days  # 假期最后一日
    assert date(2025, 10, 9) in trading_days  # 节后首个交易日
    assert date(2026, 1, 1) not in trading_days


def test_etf_fixture_covers_lifecycle(etf_assets: list) -> None:
    ids = {a.asset_id for a in etf_assets}
    assert {"ETF_SH_STEEL_A", "ETF_SZ_STEEL_B", "ETF_SH_COAL_C", "ETF_SH_STEEL_D"} <= ids
    delisted = [a for a in etf_assets if a.asset_id == "ETF_SH_COAL_C"][0]
    assert delisted.delist_at is not None
    index = [a for a in etf_assets if a.instrument_type == "INDEX"]
    assert index, "必须有指数代理标的（V-009 场景）"


def test_evidence_fixture_covers_review_statuses(evidence_candidates: list) -> None:
    statuses = {c.verification_status.value for c in evidence_candidates}
    assert {"verified", "fetch_failed", "needs_review"} <= statuses
    values = [c.parsed_value for c in evidence_candidates if c.parsed_value is not None]
    assert sorted(str(v) for v in values) == ["1217.9", "1220.5"], "冲突对必须并存"


def test_taxonomy_fixture_marks_macro_proxy(indicator_specs: list) -> None:
    roles = {i.role.value for i in indicator_specs}
    assert "macro_proxy" in roles, "PMI 必须标 MACRO_PROXY（§20.2）"
    assert "label" in roles
