"""回测编排：把仓储里的行情与观测拼成一次可复现的实验。

只做编排，不做研究决策：信号规则由调用方显式给出并写进报告，
避免"策略参数藏在代码里"导致不可复现。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from industrypulse.contracts.common import TZ_SHANGHAI
from industrypulse.contracts.context import ResearchMode
from industrypulse.contracts.domain import AssetMaster, MarketBar
from industrypulse.investment import BacktestEngine, BacktestResult, CostModel, PortfolioConstraints
from industrypulse.investment.market import HistoricalUniverse, MarketDataset
from industrypulse.repositories import (
    DuckDBMarketRepository,
    DuckDBObservationRepository,
    PublicationError,
)


@dataclass(frozen=True)
class BacktestRequest:
    asset_id: str
    benchmark_asset_id: str | None
    series_id: str
    snapshot_id: str
    start: date
    end: date
    threshold: float = 0.0
    commission_bps: float = 3.0
    slippage_bps: float = 5.0
    cash_rate_annual: float = 0.015
    max_weight: float = 1.0
    max_turnover: float | None = None


class _Clock:
    def __init__(self, as_of: datetime, snapshot_id: str) -> None:
        self.as_of = as_of
        self.research_mode = ResearchMode.RECONSTRUCTED_PIT
        self.dataset_snapshot_id = snapshot_id


def _decision_dates(bars: Sequence[MarketBar]) -> list[date]:
    """每月最后一个有行情的工作日作为决策日（信号在收盘产生）。"""
    by_month: dict[tuple[int, int], date] = {}
    for bar in bars:
        key = (bar.trade_date.year, bar.trade_date.month)
        by_month[key] = max(by_month.get(key, bar.trade_date), bar.trade_date)
    return [by_month[key] for key in sorted(by_month)]


def build_rule_signals(
    observations: DuckDBObservationRepository,
    *,
    request: BacktestRequest,
    bars: Sequence[MarketBar],
) -> tuple[dict[date, dict[str, float]], list[str]]:
    """规则：观测值 > threshold 则满仓该资产，否则空仓（现金）。

    这是**基线规则**，不是策略结论；报告必须原样记录阈值与规则名。
    """
    signals: dict[date, dict[str, float]] = {}
    warnings: list[str] = []
    for day in _decision_dates(bars):
        clock = datetime.combine(day, time(17), tzinfo=TZ_SHANGHAI)
        try:
            selection = observations.select([request.series_id], _Clock(clock, request.snapshot_id))
        except PublicationError as exc:
            warnings.append(f"{day} 查询失败：{exc}")
            continue
        if not selection.selected:
            signals[day] = {}
            continue
        # 必须取**最新所属期**的可见值：select 返回全部可见期，取第一个会
        # 永远停留在最早一期（真实多期数据才暴露的缺陷）。
        latest = max(selection.selected, key=lambda row: (row.observation_start, row.vintage_id))
        if latest.value is None:
            warnings.append(
                f"{day} 最新可见期 {latest.observation_start} 数值缺失"
                f"（{latest.missing_reason}），本决策日空仓"
            )
            signals[day] = {}
            continue
        signals[day] = {request.asset_id: 1.0} if float(latest.value) > request.threshold else {}
    return signals, warnings


def run_backtest(
    market_repository: DuckDBMarketRepository,
    observations: DuckDBObservationRepository,
    request: BacktestRequest,
    *,
    initial_cash: Decimal = Decimal("1000000"),
) -> tuple[BacktestResult, dict[str, object]]:
    """装配市场数据集并执行一次回测；返回 (结果, 实验元数据)。"""
    asset = market_repository.asset(request.asset_id)
    bars = list(market_repository.bars(request.asset_id, start=request.start, end=request.end))
    if not bars:
        raise ValueError(f"{request.asset_id} 在区间内没有行情")
    assets: list[AssetMaster] = [asset]
    all_bars: list[MarketBar] = list(bars)
    if request.benchmark_asset_id:
        benchmark = market_repository.asset(request.benchmark_asset_id)
        assets.append(benchmark)
        all_bars.extend(
            market_repository.bars(request.benchmark_asset_id, start=request.start, end=request.end)
        )
    universe = HistoricalUniverse(assets)
    trading_days = sorted({bar.trade_date for bar in bars})
    dataset = MarketDataset(universe, all_bars, [], trading_days)
    engine = BacktestEngine(
        dataset,
        costs=CostModel(
            commission_bps=request.commission_bps,
            slippage_bps=request.slippage_bps,
            cash_rate_annual=request.cash_rate_annual,
        ),
        constraints=PortfolioConstraints(
            max_weight=request.max_weight, max_turnover=request.max_turnover
        ),
    )
    signals, signal_warnings = build_rule_signals(observations, request=request, bars=bars)
    warnings = [
        "信号规则：观测值 > threshold 时满仓，否则现金（基线规则，非策略结论）",
        "行情来源为第三方聚合站点（C 级证据）；adj_method=none（不复权原始价）",
        "若未装载公司行动，本结果为**价格收益**，不含分红",
        "成本假设：佣金/滑点/现金利率见 experiment 字段",
        *signal_warnings,
    ]
    if asset.instrument_type.value == "INDEX":
        warnings.append("V-009：基准资产为指数，不可直接交易，仅作参照")
    result = engine.run(
        signals,
        initial_cash=initial_cash,
        benchmark_asset_id=request.benchmark_asset_id,
        warnings=warnings,
    )
    metadata: dict[str, object] = {
        "asset_id": request.asset_id,
        "benchmark_asset_id": request.benchmark_asset_id,
        "series_id": request.series_id,
        "snapshot_id": request.snapshot_id,
        "threshold": request.threshold,
        "costs": {
            "commission_bps": request.commission_bps,
            "slippage_bps": request.slippage_bps,
            "cash_rate_annual": request.cash_rate_annual,
        },
        "constraints": {
            "max_weight": request.max_weight,
            "max_turnover": request.max_turnover,
        },
        "bars": len(bars),
        "rebalances": len(signals),
    }
    return result, metadata


__all__ = ["BacktestRequest", "build_rule_signals", "run_backtest"]
