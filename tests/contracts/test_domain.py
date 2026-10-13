"""C-005 专项：行业/指标/资产领域不变式（ID 稳定性、PIT-008 上市判定）。"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from industrypulse.contracts import (
    AssetMaster,
    EconomicDirection,
    IndustryNode,
    MarketBar,
    SeriesRole,
)


def test_industry_id_independent_of_display_name(industry_nodes: list[IndustryNode]) -> None:
    """C-005 验收：ID 不随中文显示名改变。"""
    steel = industry_nodes[0]
    renamed = steel.model_copy(
        update={"names": {"zh": "黑色金属冶炼和压延加工业（改称）", "en": "X"}}
    )
    assert renamed.industry_id == steel.industry_id == "CN_NBS_C31"
    assert renamed != steel  # 展示名变了，但身份键不变
    registry = {steel.industry_id: steel}
    registry[renamed.industry_id] = renamed  # 同一主键槽位
    assert set(registry) == {"CN_NBS_C31"}


def test_indicator_direction_unknown_only_for_display() -> None:
    """§9.2：unknown 方向不得作为 LABEL。"""
    from industrypulse.contracts import IndicatorSpec

    base = {
        "indicator_id": "x",
        "series_ids": ["cn.demo.s"],
        "dimension": "demand",
        "economic_direction": "unknown",
        "level": "CORE",
        "industry_ids": ["CN_NBS_C31"],
        "role": "feature",
        "valid_from": date(2025, 1, 1),
        "description": "d",
    }
    IndicatorSpec.model_validate(base)  # feature 角色 unknown 合法（只展示）
    with pytest.raises(ValidationError, match="unknown 方向"):
        IndicatorSpec.model_validate({**base, "role": SeriesRole.LABEL})


def _asset(**overrides: object) -> AssetMaster:
    base: dict[str, object] = {
        "asset_id": "ETF_SH_X",
        "exchange": "SSE",
        "instrument_type": "ETF",
        "name_zh": "（合成）X",
        "list_at": "2025-03-14T09:30:00+08:00",
    }
    base.update(overrides)
    return AssetMaster.model_validate(base)


def test_asset_lifespan_validation() -> None:
    with pytest.raises(ValidationError, match="delist_at"):
        _asset(delist_at="2025-03-14T09:30:00+08:00")


def test_is_listed_on_boundaries() -> None:
    late = _asset()  # 2025-03-14 上市
    assert not late.is_listed_on(date(2025, 3, 13))
    assert late.is_listed_on(date(2025, 3, 14))  # 上市当日可入池
    delisted = _asset(delist_at="2025-10-16T15:00:00+08:00")
    assert delisted.is_listed_on(date(2025, 10, 15))
    assert not delisted.is_listed_on(date(2025, 10, 16))  # 退市当日不可入池


def test_pit_008_universe_membership(etf_assets: list[AssetMaster]) -> None:
    """PIT-008：2025-10-31 的可交易 ETF 池 = 已上市且未退市；丁（11-10 上市）被拒。"""
    universe = [
        a.asset_id
        for a in etf_assets
        if a.instrument_type == "ETF" and a.is_listed_on(date(2025, 10, 31))
    ]
    assert universe == ["ETF_SH_STEEL_A", "ETF_SZ_STEEL_B"]
    # 指数不可作为 ETF 入池
    idx = [a for a in etf_assets if a.instrument_type == "INDEX"]
    assert all(a.asset_id not in universe for a in idx)


def test_market_bar_ohlc_invariant() -> None:
    base = {
        "asset_id": "ETF_SH_X",
        "trade_date": "2025-10-09",
        "open": "1.06",
        "high": "1.05",
        "low": "1.04",
        "close": "1.05",
        "volume": "100",
    }
    with pytest.raises(ValidationError, match="OHLC"):
        MarketBar.model_validate(base)


def test_direction_enum_semantics() -> None:
    """方向枚举只有三值；unknown 仅展示（§9.2）。"""
    assert {d.value for d in EconomicDirection} == {"positive", "negative", "unknown"}
