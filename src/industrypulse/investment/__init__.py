"""投资研究：ETF 宇宙、信号隔离、回测与机会卡（lane V；不含自动交易）。"""

from .backtest import (
    BacktestEngine,
    BacktestResult,
    CostModel,
    Metrics,
    PortfolioConstraints,
    Trade,
)
from .market import EconomicSignal, HistoricalUniverse, MarketDataset, economic_signal

__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "CostModel",
    "EconomicSignal",
    "HistoricalUniverse",
    "MarketDataset",
    "Metrics",
    "PortfolioConstraints",
    "Trade",
    "economic_signal",
]
