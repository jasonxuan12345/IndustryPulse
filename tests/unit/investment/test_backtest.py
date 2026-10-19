"""回测引擎：次日成交、成本、约束、基准、可交易性。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from industrypulse.contracts import TZ_SHANGHAI, AssetMaster, MarketBar
from industrypulse.investment import (
    BacktestEngine,
    CostModel,
    HistoricalUniverse,
    MarketDataset,
    PortfolioConstraints,
)

LISTED = datetime(2020, 1, 1, tzinfo=TZ_SHANGHAI)


def _asset(asset_id: str) -> AssetMaster:
    return AssetMaster(
        asset_id=asset_id,
        exchange="SSE",
        instrument_type="ETF",
        name_zh=asset_id,
        list_at=LISTED,
    )


def _bars(asset_id: str, prices: list[float], start: date) -> list[MarketBar]:
    out = []
    for index, price in enumerate(prices):
        day = start + timedelta(days=index)
        if day.weekday() >= 5:
            day = day + timedelta(days=7 - day.weekday())
        value = Decimal(str(price))
        out.append(
            MarketBar(
                asset_id=asset_id,
                trade_date=day,
                open=value,
                high=value * Decimal("1.01"),
                low=value * Decimal("0.99"),
                close=value,
                volume=Decimal("100000"),
            )
        )
    return out


def _dataset(prices: list[float]) -> MarketDataset:
    assets = [_asset("ETF_A")]
    bars = _bars("ETF_A", prices, date(2024, 1, 1))
    days = sorted({bar.trade_date for bar in bars})
    return MarketDataset(HistoricalUniverse(assets), bars, [], days)


def test_signal_executes_on_next_trading_day() -> None:
    dataset = _dataset([1.0, 1.0, 1.0, 1.0])
    engine = BacktestEngine(dataset, costs=CostModel(commission_bps=0, slippage_bps=0))
    days = sorted(dataset.days)
    result = engine.run({days[0]: {"ETF_A": 1.0}})
    assert len(result.trades) == 1
    assert result.trades[0].trade_date == days[1]  # 绝不同日成交


def test_costs_reduce_final_equity() -> None:
    dataset = _dataset([1.0, 1.0, 1.0, 1.0])
    days = sorted(dataset.days)
    free = BacktestEngine(
        dataset, costs=CostModel(commission_bps=0, slippage_bps=0, cash_rate_annual=0)
    ).run({days[0]: {"ETF_A": 1.0}})
    costly = BacktestEngine(
        dataset, costs=CostModel(commission_bps=50, slippage_bps=50, cash_rate_annual=0)
    ).run({days[0]: {"ETF_A": 1.0}})
    assert costly.final_equity < free.final_equity
    assert costly.trades[0].commission > 0
    assert costly.trades[0].slippage_cost > 0


def test_cash_interest_accrues_when_flat() -> None:
    dataset = _dataset([1.0, 1.0, 1.0])
    result = BacktestEngine(
        dataset, costs=CostModel(cash_rate_annual=0.10, commission_bps=0, slippage_bps=0)
    ).run({sorted(dataset.days)[0]: {}})
    assert result.final_equity > result.initial_cash
    assert not result.trades


def test_max_weight_and_turnover_are_enforced() -> None:
    dataset = _dataset([1.0, 1.1, 1.2, 1.3])
    days = sorted(dataset.days)
    engine = BacktestEngine(
        dataset,
        costs=CostModel(commission_bps=0, slippage_bps=0, cash_rate_annual=0),
        constraints=PortfolioConstraints(max_weight=0.5, max_turnover=0.3),
    )
    result = engine.run({days[0]: {"ETF_A": 1.0}})
    assert any("超过上限" in warning for warning in result.warnings)
    assert any("换手" in warning for warning in result.warnings)
    # 只买入了半个仓位：首日成交金额显著小于初始资金
    assert result.trades[0].notional < result.initial_cash


def test_untradable_day_is_skipped_with_warning() -> None:
    dataset = _dataset([1.0, 1.0, 1.0])
    days = sorted(dataset.days)
    # 把执行日的成交量置 0 -> 不可交易
    bar = dataset.bars[("ETF_A", days[1])]
    dataset.bars[("ETF_A", days[1])] = bar.model_copy(update={"volume": Decimal("0")})
    engine = BacktestEngine(dataset, costs=CostModel(commission_bps=0, slippage_bps=0))
    result = engine.run({days[0]: {"ETF_A": 1.0}})
    assert not result.trades
    assert any("不可交易" in warning for warning in result.warnings)


def test_benchmark_uses_same_costs() -> None:
    dataset = _dataset([1.0, 1.2, 1.4, 1.6])
    days = sorted(dataset.days)
    result = BacktestEngine(
        dataset, costs=CostModel(commission_bps=0, slippage_bps=0, cash_rate_annual=0)
    ).run({days[0]: {"ETF_A": 1.0}}, benchmark_asset_id="ETF_A")
    assert result.benchmark_metrics is not None
    assert result.benchmark_metrics.total_return is not None
    assert result.benchmark_metrics.total_return > 0


def test_metrics_and_guards() -> None:
    dataset = _dataset([1.0, 1.0, 1.0])
    days = sorted(dataset.days)
    result = BacktestEngine(dataset).run({days[0]: {"ETF_A": 1.0}})
    assert result.metrics.total_return is not None
    assert result.equity_curve[0][0] == days[0]
    with pytest.raises(ValueError, match="initial_cash"):
        BacktestEngine(dataset).run({days[0]: {}}, initial_cash=Decimal(0))
    with pytest.raises(ValueError, match="没有任何可执行"):
        BacktestEngine(dataset).run({days[-1]: {"ETF_A": 1.0}})
