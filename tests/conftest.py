"""T-001 测试公共配置：fixture 加载器与跨套件共享夹具。

规范：
- 单元/契约测试禁止访问网络（§3.4）；一切外部形态以 tests/fixtures 的
  合成文件为准（C-009 场景全集）；
- 金样本（AI 抽取审计，A-010）在 G2 单独落目录，不与本层混放。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from industrypulse.contracts import (
    AssetIndustryMap,
    AssetMaster,
    CorporateAction,
    EvidenceCandidate,
    IndicatorSpec,
    IndustryNode,
    IndustryRelation,
    MarketBar,
    ObservationVintage,
    ResearchQuery,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "g0"


def load_fixture(name: str) -> Any:
    """读取 tests/fixtures/g0/<name>.json。"""
    path = FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        pytest.fail(f"fixture 缺失：{path}（C-009 场景不全）")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def steel_vintages() -> list[ObservationVintage]:
    """三版本钢铁利润序列（未来发布/修订/跨年/缺失/B/C/D 级证据全覆盖）。"""
    doc = load_fixture("steel_profit_vintages")
    return [ObservationVintage.model_validate(row) for row in doc["vintages"]]


@pytest.fixture(scope="session")
def trading_days() -> list[date]:
    """合成交易日历（升序，pit 语义依赖此序）。"""
    doc = load_fixture("market_calendar")
    days = sorted(date.fromisoformat(d) for d in doc["trading_days"])
    assert days == [date.fromisoformat(d) for d in doc["trading_days"]], "日历必须升序"
    return days


@pytest.fixture(scope="session")
def etf_assets() -> list[AssetMaster]:
    doc = load_fixture("etf_universe")
    return [AssetMaster.model_validate(a) for a in doc["assets"]]


@pytest.fixture(scope="session")
def asset_industry_map() -> list[AssetIndustryMap]:
    doc = load_fixture("etf_universe")
    return [AssetIndustryMap.model_validate(m) for m in doc["industry_map"]]


@pytest.fixture(scope="session")
def market_bars() -> list[MarketBar]:
    doc = load_fixture("market_bars")
    return [MarketBar.model_validate(b) for b in doc["bars"]]


@pytest.fixture(scope="session")
def corporate_actions() -> list[CorporateAction]:
    doc = load_fixture("corporate_actions")
    return [CorporateAction.model_validate(a) for a in doc["actions"]]


@pytest.fixture(scope="session")
def research_queries() -> list[ResearchQuery]:
    doc = load_fixture("evidence_candidates")
    return [ResearchQuery.model_validate(q) for q in doc["queries"]]


@pytest.fixture(scope="session")
def evidence_candidates() -> list[EvidenceCandidate]:
    doc = load_fixture("evidence_candidates")
    return [EvidenceCandidate.model_validate(c) for c in doc["candidates"]]


@pytest.fixture(scope="session")
def industry_nodes() -> list[IndustryNode]:
    doc = load_fixture("steel_taxonomy")
    return [IndustryNode.model_validate(n) for n in doc["nodes"]]


@pytest.fixture(scope="session")
def industry_relations() -> list[IndustryRelation]:
    doc = load_fixture("steel_taxonomy")
    return [IndustryRelation.model_validate(r) for r in doc["relations"]]


@pytest.fixture(scope="session")
def indicator_specs() -> list[IndicatorSpec]:
    doc = load_fixture("steel_taxonomy")
    return [IndicatorSpec.model_validate(i) for i in doc["indicators"]]
