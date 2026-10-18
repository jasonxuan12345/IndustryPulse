"""Historical ETF universe, cash distributions, and isolated economic signals."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

from industrypulse.contracts import (
    TZ_SHANGHAI,
    AssetMaster,
    ContractBaseModel,
    CorporateAction,
    InstrumentType,
    MarketBar,
    ModelStatus,
    Prediction,
)


class HistoricalUniverse:
    def __init__(self, assets: Sequence[AssetMaster]) -> None:
        self.assets = {a.asset_id: a for a in assets}
        if len(self.assets) != len(assets):
            raise ValueError("Duplicate asset_id")

    def eligible(self, at: datetime) -> list[AssetMaster]:
        if at.tzinfo is None:
            raise ValueError("Timezone required")
        return [
            a
            for a in self.assets.values()
            if a.instrument_type == InstrumentType.ETF
            and a.list_at <= at
            and (a.delist_at is None or at < a.delist_at)
        ]


class MarketDataset:
    def __init__(
        self,
        universe: HistoricalUniverse,
        bars: Sequence[MarketBar],
        actions: Sequence[CorporateAction],
        trading_days: Sequence[date],
        suspended: set[tuple[str, date]] | None = None,
    ) -> None:
        self.universe = universe
        self.bars = {(b.asset_id, b.trade_date): b for b in bars}
        if len(self.bars) != len(bars):
            raise ValueError("Duplicate bar")
        self.actions = list(actions)
        action_keys = [(a.asset_id, a.ex_date, a.action_type) for a in actions]
        if len(set(action_keys)) != len(action_keys):
            raise ValueError("Duplicate corporate action")
        self.days = set(trading_days)
        self.suspended = suspended or set()
        for bar in bars:
            asset = universe.assets.get(bar.asset_id)
            if (
                asset is None
                or not asset.is_listed_on(bar.trade_date)
                or bar.trade_date not in self.days
            ):
                raise ValueError("Bar outside listed trading calendar")
        for action in actions:
            if action.asset_id not in universe.assets or action.action_type != "cash_dividend":
                raise ValueError("Unknown asset or unsupported corporate action")

    def tradable(self, asset_id: str, day: date) -> bool:
        at = datetime.combine(day, datetime.min.time().replace(hour=15), TZ_SHANGHAI)
        return (
            asset_id in {a.asset_id for a in self.universe.eligible(at)}
            and day in self.days
            and (asset_id, day) in self.bars
            and (asset_id, day) not in self.suspended
            and self.bars[asset_id, day].volume > 0
        )

    def total_return(self, asset_id: str, start: date, end: date) -> Decimal:
        if end <= start:
            raise ValueError("Return interval must advance")
        first, last = self.bars[asset_id, start], self.bars[asset_id, end]
        if first.adj_method != "none" or last.adj_method != "none":
            raise ValueError("Cash dividends require unadjusted bars; avoid double counting")
        dividends = sum(
            (
                a.distribution_per_unit
                for a in self.actions
                if a.asset_id == asset_id and start < a.ex_date <= end
            ),
            Decimal(0),
        )
        return (last.close + dividends) / first.close - 1


class EconomicSignal(ContractBaseModel):
    prediction_id: str
    industry_id: str
    signal_at: datetime
    probability: float | None
    point_estimate: float | None
    research_mode: str


def economic_signal(prediction: Prediction, at: datetime) -> EconomicSignal:
    if at.tzinfo is None or prediction.as_of > at:
        raise ValueError("Future prediction")
    if prediction.expiry_at is not None and at >= prediction.expiry_at:
        raise ValueError("Expired prediction")
    if prediction.model_status not in {
        ModelStatus.OK,
        ModelStatus.UNCALIBRATED,
        ModelStatus.DEGRADED,
    }:
        raise ValueError("Prediction is not usable")
    return EconomicSignal(
        prediction_id=prediction.prediction_id,
        industry_id=prediction.industry_id,
        signal_at=at,
        probability=prediction.predicted_probability,
        point_estimate=prediction.point_estimate,
        research_mode=prediction.research_mode.value,
    )
