"""投资回测引擎（V-005..V-010）。

设计取舍（都写进 ``BacktestResult.warnings``，不允许静默）：

- **V-005 执行时点**：信号在决策日收盘产生，订单只在**下一个交易日开盘**成交。
  同日成交一律禁止；无下一交易日则该信号作废并记警告。
- **V-006 成本**：显式佣金（bps）+ 滑点（bps，买加卖减）+ 现金年化利息（按 252 日折算）。
  分红不在本模块内假设——若未装载公司行动，结果只是**价格收益**，必须由调用方声明。
- **V-007 约束**：单资产权重上限、单次换手上限；超出则按比例缩放到约束内。
- **V-008 基准**：同成本、同起点的买入持有基准。
- **V-009 真实 ETF vs 指数代理**：引擎不区分二者，由 ``instrument_type`` 与
  调用方警告声明；指数型资产不可成交（``MarketDataset.tradable`` 只放行已上市 ETF）。
- **V-010 事件日志**：每笔成交记录价格、数量、费用、滑点成本与归因。
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import Field

from industrypulse.contracts.common import ContractBaseModel, NonEmptyStr
from industrypulse.investment.market import MarketDataset

#: 一年交易日数（现金利息与年化折算）。
TRADING_DAYS = 252


@dataclass(frozen=True)
class CostModel:
    commission_bps: float = 3.0
    slippage_bps: float = 5.0
    cash_rate_annual: float = 0.015

    def __post_init__(self) -> None:
        for name, value in (
            ("commission_bps", self.commission_bps),
            ("slippage_bps", self.slippage_bps),
        ):
            if value < 0:
                raise ValueError(f"{name} 不能为负")
        if self.cash_rate_annual <= -1:
            raise ValueError("cash_rate_annual 必须大于 -100%")


@dataclass(frozen=True)
class PortfolioConstraints:
    max_weight: float = 1.0
    max_turnover: float | None = None

    def __post_init__(self) -> None:
        if not 0 < self.max_weight <= 1:
            raise ValueError("max_weight 必须在 (0, 1]")
        if self.max_turnover is not None and self.max_turnover <= 0:
            raise ValueError("max_turnover 必须为正")


class Trade(ContractBaseModel):
    trade_date: date
    asset_id: NonEmptyStr
    side: str
    quantity: Annotated[Decimal, Field(gt=0)]
    price: Annotated[Decimal, Field(gt=0)]
    notional: Annotated[Decimal, Field(ge=0)]
    commission: Annotated[Decimal, Field(ge=0)]
    slippage_cost: Annotated[Decimal, Field(ge=0)]
    realized_pnl: Decimal | None = None


class Metrics(ContractBaseModel):
    total_return: float | None
    annualized_return: float | None
    volatility: float | None
    sharpe: float | None
    max_drawdown: float | None
    turnover: float
    trades: int


class BacktestResult(ContractBaseModel):
    start: date
    end: date
    initial_cash: Decimal
    final_equity: Decimal
    equity_curve: list[tuple[date, Decimal]]
    trades: list[Trade]
    metrics: Metrics
    benchmark_equity: list[tuple[date, Decimal]] = Field(default_factory=list)
    benchmark_metrics: Metrics | None = None
    warnings: list[str] = Field(default_factory=list)


def _metrics(curve: Sequence[tuple[date, Decimal]], turnover: float, trades: int) -> Metrics:
    if len(curve) < 2:
        return Metrics(
            total_return=None,
            annualized_return=None,
            volatility=None,
            sharpe=None,
            max_drawdown=None,
            turnover=turnover,
            trades=trades,
        )
    values = [float(value) for _day, value in curve]
    total = values[-1] / values[0] - 1
    returns = [values[i] / values[i - 1] - 1 for i in range(1, len(values))]
    years = len(returns) / TRADING_DAYS
    annualized = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else None
    volatility = statistics.pstdev(returns) * (TRADING_DAYS**0.5) if len(returns) > 1 else None
    sharpe = (annualized / volatility) if annualized is not None and volatility else None
    peak, drawdown = values[0], 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    return Metrics(
        total_return=total,
        annualized_return=annualized,
        volatility=volatility,
        sharpe=sharpe,
        max_drawdown=drawdown,
        turnover=turnover,
        trades=trades,
    )


class BacktestEngine:
    def __init__(
        self,
        market: MarketDataset,
        *,
        costs: CostModel | None = None,
        constraints: PortfolioConstraints | None = None,
    ) -> None:
        self.market = market
        self.costs = costs or CostModel()
        self.constraints = constraints or PortfolioConstraints()

    # —— 内部工具 ——
    def _close(self, asset_id: str, day: date) -> Decimal:
        return self.market.bars[asset_id, day].close

    def _open(self, asset_id: str, day: date) -> Decimal:
        return self.market.bars[asset_id, day].open

    def _next_trading_day(self, day: date) -> date | None:
        for candidate in sorted(self.market.days):
            if candidate > day:
                return candidate
        return None

    def run(
        self,
        targets: Mapping[date, Mapping[str, float]],
        *,
        initial_cash: Decimal = Decimal("1000000"),
        benchmark_asset_id: str | None = None,
        warnings: Sequence[str] = (),
    ) -> BacktestResult:
        if initial_cash <= 0:
            raise ValueError("initial_cash 必须为正")
        days = sorted(self.market.days)
        if not days:
            raise ValueError("交易日历为空")
        schedule: dict[date, Mapping[str, float]] = {}
        result_warnings = list(warnings)
        for signal_day, weights in sorted(targets.items()):
            execution = self._next_trading_day(signal_day)
            if execution is None:
                result_warnings.append(
                    f"信号日 {signal_day} 之后无交易日，信号作废（禁止同日成交）"
                )
                continue
            schedule[execution] = weights
        if not schedule:
            raise ValueError("没有任何可执行的调仓信号")

        cash = initial_cash
        positions: dict[str, Decimal] = {}
        average_cost: dict[str, Decimal] = {}
        trades: list[Trade] = []
        curve: list[tuple[date, Decimal]] = []
        traded_notional = Decimal(0)
        daily_rate = Decimal(str(self.costs.cash_rate_annual)) / Decimal(TRADING_DAYS)
        commission_rate = Decimal(str(self.costs.commission_bps)) / Decimal(10000)
        slippage_rate = Decimal(str(self.costs.slippage_bps)) / Decimal(10000)

        dividend_income = Decimal(0)
        for day in days:
            cash *= 1 + daily_rate
            for action in self.market.actions:
                if action.ex_date == day and action.asset_id in positions:
                    amount = positions[action.asset_id] * action.distribution_per_unit
                    cash += amount
                    dividend_income += amount
            scheduled = schedule.get(day)
            if scheduled is not None:
                weights = scheduled
                equity = cash + sum(
                    (quantity * self._close(asset, day) for asset, quantity in positions.items()),
                    Decimal(0),
                )
                clamped = self._clamp(weights, result_warnings, day)
                target_values = {
                    asset: equity * Decimal(str(weight)) for asset, weight in clamped.items()
                }
                current_values = {
                    asset: quantity * self._close(asset, day)
                    for asset, quantity in positions.items()
                }
                deltas = {
                    asset: target_values.get(asset, Decimal(0))
                    - current_values.get(asset, Decimal(0))
                    for asset in set(target_values) | set(current_values)
                }
                deltas = self._apply_turnover_cap(deltas, equity, result_warnings, day)
                for asset, delta in sorted(deltas.items()):
                    if delta == 0:
                        continue
                    if not self.market.tradable(asset, day):
                        result_warnings.append(
                            f"{day} {asset} 不可交易（停牌/无成交/未上市），跳过该笔调仓"
                        )
                        continue
                    price_open = self._open(asset, day)
                    if delta > 0:
                        price = price_open * (1 + slippage_rate)
                        quantity = (delta / price).to_integral_value(rounding="ROUND_FLOOR")
                        if quantity <= 0:
                            continue
                        notional = quantity * price
                        commission = notional * commission_rate
                        if notional + commission > cash:
                            affordability = (
                                cash / (price * (1 + commission_rate))
                            ).to_integral_value(rounding="ROUND_FLOOR")
                            if affordability <= 0:
                                continue
                            quantity = affordability
                            notional = quantity * price
                            commission = notional * commission_rate
                        cash -= notional + commission
                        held = positions.get(asset, Decimal(0))
                        total_cost = (
                            average_cost.get(asset, Decimal(0)) * held + notional + commission
                        )
                        positions[asset] = held + quantity
                        average_cost[asset] = total_cost / positions[asset]
                        trades.append(
                            Trade(
                                trade_date=day,
                                asset_id=asset,
                                side="buy",
                                quantity=quantity,
                                price=price,
                                notional=notional,
                                commission=commission,
                                slippage_cost=quantity * (price - price_open),
                            )
                        )
                        traded_notional += notional
                    else:
                        price = price_open * (1 - slippage_rate)
                        held = positions.get(asset, Decimal(0))
                        wanted = (-delta / price).to_integral_value(rounding="ROUND_FLOOR")
                        quantity = min(held, wanted)
                        if quantity <= 0:
                            continue
                        notional = quantity * price
                        commission = notional * commission_rate
                        cash += notional - commission
                        cost_basis = average_cost.get(asset, Decimal(0))
                        realized = (price - cost_basis) * quantity - commission
                        positions[asset] = held - quantity
                        if positions[asset] == 0:
                            positions.pop(asset)
                            average_cost.pop(asset, None)
                        trades.append(
                            Trade(
                                trade_date=day,
                                asset_id=asset,
                                side="sell",
                                quantity=quantity,
                                price=price,
                                notional=notional,
                                commission=commission,
                                slippage_cost=quantity * (price_open - price),
                                realized_pnl=realized,
                            )
                        )
                        traded_notional += notional
            equity = cash + sum(
                (quantity * self._close(asset, day) for asset, quantity in positions.items()),
                Decimal(0),
            )
            curve.append((day, equity))

        average_equity = sum((value for _day, value in curve), Decimal(0)) / Decimal(len(curve))
        metrics = _metrics(
            curve, float(traded_notional / average_equity) if average_equity else 0.0, len(trades)
        )
        benchmark_curve: list[tuple[date, Decimal]] = []
        benchmark_metrics = None
        if benchmark_asset_id is not None:
            benchmark_curve = self._buy_and_hold(
                benchmark_asset_id, days, initial_cash, result_warnings
            )
            if benchmark_curve:
                benchmark_metrics = _metrics(benchmark_curve, 0.0, 1)
        if dividend_income > 0:
            result_warnings.append(f"已计入现金分红合计 {dividend_income:.2f}")
        else:
            result_warnings.append("区间内未发生分红入账")
        return BacktestResult(
            start=days[0],
            end=days[-1],
            initial_cash=initial_cash,
            final_equity=curve[-1][1],
            equity_curve=curve,
            trades=trades,
            metrics=metrics,
            benchmark_equity=benchmark_curve,
            benchmark_metrics=benchmark_metrics,
            warnings=result_warnings,
        )

    def _clamp(
        self, weights: Mapping[str, float], warnings: list[str], day: date
    ) -> dict[str, float]:
        cleaned = {asset: weight for asset, weight in weights.items() if weight > 0}
        for asset, weight in cleaned.items():
            if weight > self.constraints.max_weight:
                warnings.append(
                    f"{day} {asset} 目标权重 {weight:.2f} 超过上限 "
                    f"{self.constraints.max_weight:.2f}，已截断"
                )
        clamped = {
            asset: min(weight, self.constraints.max_weight) for asset, weight in cleaned.items()
        }
        total = sum(clamped.values())
        if total > 1.0:
            warnings.append(f"{day} 目标权重合计 {total:.2f} > 1，已等比例缩放到满仓")
            clamped = {asset: weight / total for asset, weight in clamped.items()}
        return clamped

    def _apply_turnover_cap(
        self,
        deltas: Mapping[str, Decimal],
        equity: Decimal,
        warnings: list[str],
        day: date,
    ) -> dict[str, Decimal]:
        cap = self.constraints.max_turnover
        if cap is None or equity <= 0:
            return dict(deltas)
        gross = sum((abs(value) for value in deltas.values()), Decimal(0))
        if gross <= equity * Decimal(str(cap)):
            return dict(deltas)
        scale = (equity * Decimal(str(cap))) / gross
        warnings.append(f"{day} 换手 {float(gross / equity):.2f} 超过上限 {cap:.2f}，按比例缩量")
        return {asset: value * scale for asset, value in deltas.items()}

    def _buy_and_hold(
        self,
        asset_id: str,
        days: Sequence[date],
        initial_cash: Decimal,
        warnings: list[str],
    ) -> list[tuple[date, Decimal]]:
        """基准：首个可交易日开盘买入并持有，成本口径与策略一致。"""
        commission_rate = Decimal(str(self.costs.commission_bps)) / Decimal(10000)
        slippage_rate = Decimal(str(self.costs.slippage_bps)) / Decimal(10000)
        entry_index = None
        for index, day in enumerate(days):
            if self.market.tradable(asset_id, day):
                entry_index = index
                break
        if entry_index is None:
            warnings.append(f"基准 {asset_id} 在整个区间不可交易，基准曲线为空")
            return []
        entry_day = days[entry_index]
        price = self._open(asset_id, entry_day) * (1 + slippage_rate)
        quantity = (initial_cash / (price * (1 + commission_rate))).to_integral_value(
            rounding="ROUND_FLOOR"
        )
        if quantity <= 0:
            warnings.append(f"基准 {asset_id} 资金不足一手，基准曲线为空")
            return []
        cash = initial_cash - quantity * price * (1 + commission_rate)
        curve: list[tuple[date, Decimal]] = []
        daily_rate = Decimal(str(self.costs.cash_rate_annual)) / Decimal(TRADING_DAYS)
        for day in days:
            cash *= 1 + daily_rate
            if day >= entry_day:
                for action in self.market.actions:
                    if action.ex_date == day and action.asset_id == asset_id:
                        cash += quantity * action.distribution_per_unit
                curve.append((day, cash + quantity * self._close(asset_id, day)))
            else:
                curve.append((day, cash))
        return curve


__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "CostModel",
    "Metrics",
    "PortfolioConstraints",
    "Trade",
]
