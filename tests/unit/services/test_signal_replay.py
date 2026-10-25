"""信号重放（阶段 1 基线）：联合可见时间、旧期值修订、决策日重放。"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from industrypulse.contracts import TZ_SHANGHAI, ResearchMode
from industrypulse.contracts.observation import (
    AvailabilityBasis,
    EvidenceGrade,
    ObservationVintage,
)
from industrypulse.repositories import DuckDBObservationRepository
from industrypulse.services.signal_replay import (
    first_visible,
    improvement_signal,
    joint_visibility,
    replay_day,
    revision_report,
)

PROFIT = "cn.nbs.industry_c31.profit_ytd_yoy"
REVENUE = "cn.nbs.industry_c31.revenue_ytd_yoy"


def _vintage(
    series_id: str,
    period: date,
    value: float | None,
    *,
    available_at: datetime,
    vintage_id: int = 1,
) -> ObservationVintage:
    return ObservationVintage(
        series_id=series_id,
        observation_start=period,
        observation_end=period,
        vintage_id=vintage_id,
        value=None if value is None else str(value),
        unit="%",
        available_at=available_at,
        publication_at=available_at,
        source_known_at=available_at,
        retrieved_at=available_at,
        vintage_valid_from=available_at,
        availability_basis=AvailabilityBasis.OBSERVED_PUBLICATION,
        release_evidence=EvidenceGrade.A,
        value_evidence=EvidenceGrade.C,
    )


def _at(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time().replace(hour=9, minute=30), TZ_SHANGHAI)


def _repository() -> DuckDBObservationRepository:
    repository = DuckDBObservationRepository(":memory:")
    with repository.database.transaction() as conn:
        for vintage in _scenarios():
            conn.execute(
                "INSERT INTO observation_vintage VALUES (?,?,?,?)",
                [
                    vintage.series_id,
                    vintage.observation_start,
                    vintage.vintage_id,
                    vintage.model_dump_json(),
                ],
            )
    return repository


def _scenarios() -> list[ObservationVintage]:
    """三个月度期：营收比利润晚一天可见，上期值后来被修订（值不变）。"""
    may, june, july = date(2026, 5, 1), date(2026, 6, 1), date(2026, 7, 1)
    rows = [
        _vintage(PROFIT, may, -5.0, available_at=_at(date(2026, 6, 27))),
        _vintage(PROFIT, june, -3.0, available_at=_at(date(2026, 7, 27))),
        # 上期值的修订版本：值没变，只是证据升级（用于 revision_report）。
        _vintage(
            PROFIT,
            may,
            -5.0,
            available_at=_at(date(2026, 8, 27)),
            vintage_id=2,
        ),
        _vintage(REVENUE, june, 2.0, available_at=_at(date(2026, 7, 28))),
        _vintage(REVENUE, july, 1.0, available_at=_at(date(2026, 8, 27))),
    ]
    return rows


def test_improvement_signal_matches_the_price_check_rule() -> None:
    assert improvement_signal(5.0, 3.0, 1.0) is True
    assert improvement_signal(3.0, 5.0, 1.0) is False
    assert improvement_signal(5.0, 3.0, -1.0) is False
    assert improvement_signal(None, 3.0, 1.0) is None
    assert improvement_signal(5.0, None, None) is None


def test_joint_visibility_waits_for_the_latest_component() -> None:
    repository = _repository()
    rows = _scenarios()
    joint = joint_visibility(
        [row for row in rows if row.series_id == PROFIT],
        [row for row in rows if row.series_id == REVENUE],
        repository,
        ResearchMode.RECONSTRUCTED_PIT,
    )
    june = next(row for row in joint if row["period"] == "2026-06-01")
    # 收入比利润晚一天：联合可见时间必须等到收入，不能拿利润的发布时点入场。
    assert june["lagging_component"] == "当期营收"
    assert june["joint_visible_at"] == _at(date(2026, 7, 28)).isoformat()
    assert june["delay_days"] == pytest.approx(1.0)
    assert june["visible_signal"] is True


def test_revision_report_lists_superseded_previous_period_values() -> None:
    repository = _repository()
    rows = revision_report(_scenarios(), repository, ResearchMode.RECONSTRUCTED_PIT)
    assert len(rows) == 1
    assert rows[0]["period"] == "2026-05-01"
    assert rows[0]["versions"] == 2
    assert rows[0]["value_changed"] is False


def test_replay_day_sees_only_then_visible_values() -> None:
    repository = _repository()
    # 7 月 27 日：利润 6 月期已可见，收入要到 28 日——重放必须看到这个差别。
    replay = replay_day(
        repository,
        snapshot_id=None,
        profit_id=PROFIT,
        revenue_id=REVENUE,
        as_of=_at(date(2026, 7, 27)),
        mode=ResearchMode.RECONSTRUCTED_PIT,
    )
    assert replay["visible_profit"] == pytest.approx(-3.0)
    assert replay["visible_revenue"] is None
    assert replay["replayed_signal"] is None

    later = replay_day(
        repository,
        snapshot_id=None,
        profit_id=PROFIT,
        revenue_id=REVENUE,
        as_of=_at(date(2026, 7, 29)),
        mode=ResearchMode.RECONSTRUCTED_PIT,
    )
    assert later["visible_revenue"] == pytest.approx(2.0)
    assert later["replayed_signal"] is True


def test_first_visible_ignores_retrieval_time_basis_in_strict_mode() -> None:
    repository = DuckDBObservationRepository(":memory:")
    observed = _vintage(PROFIT, date(2026, 9, 1), 1.0, available_at=_at(date(2026, 9, 28)))
    observed = observed.model_copy(update={"value_evidence": EvidenceGrade.B})
    assert first_visible([observed], repository, ResearchMode.STRICT_PIT) is not None
    retrieval_only = observed.model_copy(
        update={"availability_basis": AvailabilityBasis.RETRIEVAL_TIME}
    )
    assert first_visible([retrieval_only], repository, ResearchMode.STRICT_PIT) is None
    # EXPLORATORY 不设门槛，抓取时点依据也能用。
    assert first_visible([retrieval_only], repository, ResearchMode.EXPLORATORY) is not None
