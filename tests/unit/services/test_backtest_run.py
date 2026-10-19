"""规则信号：必须取最新可见所属期，而不是最早一期。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from industrypulse.contracts import TZ_SHANGHAI, MarketBar, ResearchMode
from industrypulse.contracts.observation import (
    AvailabilityBasis,
    EvidenceGrade,
    ObservationVintage,
)
from industrypulse.contracts.pit import AsOfSelection
from industrypulse.services import BacktestRequest, build_rule_signals

SERIES = "cn.nbs.test_series"


def _vintage(period: date, value: str | None, vintage_id: int = 1) -> ObservationVintage:
    stamp = datetime(2025, 1, 1, tzinfo=TZ_SHANGHAI)
    return ObservationVintage(
        vintage_id=vintage_id,
        series_id=SERIES,
        observation_start=period,
        observation_end=period,
        value=Decimal(value) if value is not None else None,
        missing_reason=None if value is not None else "withdrawn_by_publisher",
        unit="%",
        publication_at=stamp,
        retrieved_at=stamp,
        available_at=stamp,
        availability_basis=AvailabilityBasis.OBSERVED_PUBLICATION,
        vintage_valid_from=stamp,
        release_evidence=EvidenceGrade.A,
        value_evidence=EvidenceGrade.C,
        artifact_id="x",
    )


class StubRepo:
    def __init__(self, rows_by_day: dict[date, list[ObservationVintage]]) -> None:
        self.rows_by_day = rows_by_day

    def select(self, series_ids, ctx):  # noqa: ANN001, ANN201
        return AsOfSelection(
            as_of=ctx.as_of,
            mode=ResearchMode.RECONSTRUCTED_PIT,
            selected=self.rows_by_day.get(ctx.as_of.date(), []),
            skipped=[],
        )


def _bars(days: int) -> list[MarketBar]:
    out = []
    start = date(2025, 9, 30)
    for index in range(days):
        day = start + timedelta(days=index)
        out.append(
            MarketBar(
                asset_id="ETF_X",
                trade_date=day,
                open=Decimal("1"),
                high=Decimal("1"),
                low=Decimal("1"),
                close=Decimal("1"),
                volume=Decimal("1"),
            )
        )
    return out


def _request() -> BacktestRequest:
    return BacktestRequest(
        asset_id="ETF_X",
        benchmark_asset_id=None,
        series_id=SERIES,
        snapshot_id="snap",
        start=date(2025, 9, 30),
        end=date(2025, 10, 2),
    )


def test_uses_latest_visible_period_not_first() -> None:
    day = date(2025, 9, 30)
    # 早期为正、最新为负：若错误地取 selected[0]，会得出"满仓"
    repo = StubRepo({day: [_vintage(date(2025, 6, 1), "12.0"), _vintage(date(2025, 8, 1), "-3.0")]})
    signals, _warnings = build_rule_signals(repo, request=_request(), bars=_bars(1))
    assert signals[day] == {}


def test_missing_latest_value_goes_flat_with_warning() -> None:
    day = date(2025, 9, 30)
    repo = StubRepo({day: [_vintage(date(2025, 8, 1), None)]})
    signals, warnings = build_rule_signals(repo, request=_request(), bars=_bars(1))
    assert signals[day] == {}
    assert any("数值缺失" in warning for warning in warnings)


def test_positive_latest_value_goes_long() -> None:
    day = date(2025, 9, 30)
    repo = StubRepo({day: [_vintage(date(2025, 8, 1), "5.0")]})
    signals, _warnings = build_rule_signals(repo, request=_request(), bars=_bars(1))
    assert signals[day] == {"ETF_X": 1.0}
