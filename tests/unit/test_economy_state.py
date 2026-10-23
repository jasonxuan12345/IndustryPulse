from datetime import date
from pathlib import Path

from industrypulse.economy.state import (
    acceleration,
    classify_regime,
    cyclical_state,
    expansion_breadth,
    growth_hypothesis,
)
from industrypulse.services.industry_expand import FINANCIAL_INDUSTRIES
from industrypulse.taxonomy import TaxonomyRegistry

ROOT = Path("config/industries")


def test_regime_uses_published_headline_and_its_change() -> None:
    assert classify_regime(50.1, 49.8) == "EXPANSION"
    assert classify_regime(50.1, 50.4) == "SLOWDOWN"
    assert classify_regime(49.2, 49.8) == "CONTRACTION"
    assert classify_regime(49.5, 49.2) == "RECOVERY"
    assert classify_regime(50.0, 49.0) == "UNCERTAIN"
    assert classify_regime(50.1, None) == "UNCERTAIN"


def test_acceleration_needs_three_published_values() -> None:
    assert acceleration([49.0, 50.0]) is None
    assert acceleration([49.0, 50.0, 50.2]) == (50.2 - 50.0) - (50.0 - 49.0)


def test_breadth_excludes_inverse_supplier_delivery_and_partial_sets() -> None:
    assert expansion_breadth({"new_orders": 51, "production": 49, "supplier_delivery": 80}) is None
    assert (
        expansion_breadth(
            {
                "new_orders": 51,
                "production": 49,
                "employment": 48,
                "raw_inventory": 52,
                "supplier_delivery": 10,
            }
        )
        == 0.5
    )


def test_hypothesis_requires_rising_profit_and_positive_revenue() -> None:
    period = date(2026, 8, 1)
    base = {
        "industry_id": "CN_NBS_C36",
        "name": "汽车制造业",
        "period": period,
        "profit_yoy": 5.0,
        "profit_change": 1.2,
        "revenue_yoy": 3.0,
        "cost_yoy": 1.0,
        "source_urls": ["https://www.stats.gov.cn/example"],
    }
    hypothesis = growth_hypothesis(**base)
    assert hypothesis is not None
    assert hypothesis["status"] == "RESEARCH_CANDIDATE"
    assert "累计同比" in str(hypothesis["mechanism"])
    assert growth_hypothesis(**{**base, "profit_change": -0.2}) is None
    assert growth_hypothesis(**{**base, "revenue_yoy": 0}) is None
    assert growth_hypothesis(**{**base, "profit_change": None}) is None
    costly = growth_hypothesis(**{**base, "cost_yoy": 4})
    assert costly is not None
    assert costly["contradicting_evidence"]


def test_cycle_labels_do_not_treat_a_positive_level_as_improvement() -> None:
    assert cyclical_state(8, -1) == "SOFTENING"
    assert cyclical_state(-2, 1) == "STABILIZING"
    assert cyclical_state(None, 1) == "UNKNOWN"


def test_new_industries_use_official_rows_and_not_the_semiconductor_name() -> None:
    expected = {
        "auto": "汽车制造业",
        "pharma": "医药制造业",
        "electronics": "计算机、通信和其他电子设备制造业",
        "electrical": "电气机械和器材制造业",
        "general-equipment": "通用设备制造业",
        "special-equipment": "专用设备制造业",
        "oil-gas": "石油和天然气开采业",
        "agri-food": "农副食品加工业",
        "food": "食品制造业",
        "beverage": "酒、饮料和精制茶制造业",
        "refining": "石油、煤炭及其他燃料加工业",
        "building-materials": "非金属矿物制品业",
        "transport-equipment": "铁路、船舶、航空航天和其他运输设备制造业",
        "instruments": "仪器仪表制造业",
        "power": "电力、热力生产和供应业",
    }
    for stem, name in expected.items():
        assert stem in FINANCIAL_INDUSTRIES
        registry = TaxonomyRegistry.load(ROOT / f"{stem}.yaml")
        assert registry.nodes[0].names["zh"] == name
        assert name != "半导体"
        assert len(registry.series) == 6
        code = registry.nodes[0].code
        section = registry.nodes[0].industry_id.removeprefix("CN_NBS_")[0].lower()
        assert section == {"07": "b", "44": "d"}.get(code, "c")
        assert {item.series_id for item in registry.series} == {
            f"cn.nbs.industry_{section}{code}.{suffix}"
            for suffix in (
                "revenue_ytd",
                "revenue_ytd_yoy",
                "cost_ytd",
                "cost_ytd_yoy",
                "profit_ytd",
                "profit_ytd_yoy",
            )
        }
