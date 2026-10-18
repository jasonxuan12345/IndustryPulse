"""Behavioral acceptance for economy, investment and operations G1 lanes."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from industrypulse.contracts import (
    TZ_SHANGHAI,
    AnalysisContext,
    AssetMaster,
    CalibrationStatus,
    CorporateAction,
    IndicatorSpec,
    JobState,
    MarketBar,
    ModelStatus,
    ObservationFrame,
    ObservationVintage,
    Prediction,
    PredictionFrame,
    ResearchMode,
)
from industrypulse.contracts.model import AnalysisContextLike
from industrypulse.core import Settings
from industrypulse.economy import EconomyEngine, RobustScaler, transform, ytd_increment
from industrypulse.investment import HistoricalUniverse, MarketDataset, economic_signal
from industrypulse.jobs import JobRunner, assess_source, forecast_monthly
from industrypulse.services import ForecastSnapshotStore

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/g0"
AT = datetime(2025, 5, 1, 17, tzinfo=TZ_SHANGHAI)


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def prediction(at: datetime = AT) -> Prediction:
    return Prediction(
        prediction_id="p1",
        model_id="M00",
        model_version="1",
        industry_id="CN_NBS_C31",
        target_id="T1",
        as_of=at,
        horizon_months=3,
        research_mode=ResearchMode.STRICT_PIT,
        predicted_probability=0.6,
        point_estimate=None,
        prediction_interval=None,
        baseline_probability=0.5,
        calibration_status=CalibrationStatus.UNCALIBRATED,
        model_status=ModelStatus.UNCALIBRATED,
        run_id="run1",
    )


def test_robust_scaling_and_unit_semantics() -> None:
    scaler = RobustScaler().fit([(AT - timedelta(days=i), float(i)) for i in range(1, 6)], AT)
    old = scaler.score(3)
    assert scaler.score(1e9) == 3 and scaler.score(3) == old
    with pytest.raises(ValueError, match="future"):
        scaler.fit([(AT + timedelta(days=1), 9999)], AT)
    assert transform(52, None, "pmi_gap") == 2
    assert transform(5, 3, "percentage_point") == 2
    assert transform(5, 0, "percent_change") is None
    assert transform(-5, -3, "percent_change") is None
    rows = [ObservationVintage.model_validate(v) for v in load("steel_profit_vintages")["vintages"]]
    november, december = rows[0], rows[1]
    assert ytd_increment(december, november) == float(Decimal("342.3"))
    with pytest.raises(ValueError):
        ytd_increment(december.model_copy(update={"observation_end": date(2025, 1, 31)}), december)


def test_economy_missing_direction_and_ancestry() -> None:
    rows = [ObservationVintage.model_validate(v) for v in load("steel_profit_vintages")["vintages"]]

    class Repository:
        def fetch(self, series_ids, ctx: AnalysisContextLike) -> ObservationFrame:
            return ObservationFrame(
                series_ids=list(series_ids),
                as_of=ctx.as_of,
                research_mode=ctx.research_mode.value,
                vintages=[rows[0]],
            )

    specs = [IndicatorSpec.model_validate(v) for v in load("steel_taxonomy")["indicators"]]
    ctx = AnalysisContext(
        as_of=AT,
        research_mode=ResearchMode.STRICT_PIT,
        industry_id="CN_NBS_C31",
        dataset_snapshot_id="fixture",
        mapping_version="1",
    )
    scaler = RobustScaler().fit(
        [(AT - timedelta(days=10), 1500), (AT - timedelta(days=5), 1600)], AT
    )
    report = EconomyEngine(Repository()).calculate(specs, ctx, {"steel_profit_ytd": scaler})
    assert report.data_coverage == pytest.approx(1 / 3)
    assert report.dimensions["inventory"].level is None
    assert report.drivers[0].ancestor_ids and report.level is not None
    assert report.breadth is None and report.momentum is None
    unknown = specs[0].model_copy(update={"economic_direction": "unknown"})
    assert (
        EconomyEngine(Repository()).calculate([unknown], ctx, {"steel_profit_ytd": scaler}).level
        is None
    )


def test_etf_lifespan_calendar_and_cash_dividend() -> None:
    asset = AssetMaster(
        asset_id="ETF_TEST",
        exchange="SSE",
        instrument_type="ETF",
        name_zh="合成ETF",
        list_at=datetime(2025, 4, 29, 9, 30, tzinfo=TZ_SHANGHAI),
        delist_at=datetime(2025, 5, 2, 9, 30, tzinfo=TZ_SHANGHAI),
    )
    universe = HistoricalUniverse([asset])
    assert not universe.eligible(asset.list_at - timedelta(seconds=1))
    assert universe.eligible(asset.list_at)
    assert not universe.eligible(asset.delist_at)
    days = [date(2025, 4, 29), date(2025, 4, 30)]
    bars = [
        MarketBar(
            asset_id=asset.asset_id,
            trade_date=day,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=100,
        )
        for day, price in zip(days, [10, 9], strict=True)
    ]
    actions = [
        CorporateAction(
            asset_id=asset.asset_id,
            ex_date=days[1],
            action_type="cash_dividend",
            distribution_per_unit=1,
        )
    ]
    market = MarketDataset(universe, bars, actions, days)
    assert market.total_return(asset.asset_id, *days) == 0
    assert market.tradable(asset.asset_id, days[0])
    market.suspended.add((asset.asset_id, days[0]))
    assert not market.tradable(asset.asset_id, days[0])
    with pytest.raises(ValueError):
        MarketDataset(
            universe, [bars[0].model_copy(update={"trade_date": date(2025, 1, 1)})], [], days
        )


def test_signal_no_future_labels_prices_or_predictions() -> None:
    with pytest.raises(ValueError, match="Future"):
        economic_signal(prediction(), AT - timedelta(seconds=1))
    signal = economic_signal(prediction(), AT)
    assert "price" not in type(signal).model_fields and "label" not in type(signal).model_fields
    with pytest.raises(ValueError, match="Expired"):
        economic_signal(prediction().model_copy(update={"expiry_at": AT}), AT)


def test_persistent_jobs_retry_idempotency_and_safe_errors(tmp_path: Path) -> None:
    path = tmp_path / "jobs.db"
    delays = []
    calls = 0

    def action() -> int:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("secret-token")
        return 42

    runner = JobRunner(path, sleep=delays.append)
    status, value = runner.run("download", "key", action)
    assert status.state == JobState.SUCCEEDED and value == 42 and calls == 3
    assert delays == [1, 2] and status.retry_count == 2
    assert len(runner.events(status.job_id)) == 4
    runner.close()
    runner = JobRunner(path)
    skipped, value = runner.run("download", "key", action)
    assert skipped.state == JobState.SKIPPED and calls == 3 and value is None

    def fail() -> None:
        raise ValueError("secret-token")

    failed, _ = runner.run("parse", "other-key", fail)
    assert failed.state == JobState.FAILED and failed.error == "ValueError"
    assert "secret-token" not in path.read_bytes().decode("utf-8", errors="ignore")
    runner.close()


def test_settings_and_health_do_not_expose_secrets() -> None:
    settings = Settings.from_env({"INDUSTRYPULSE_API_KEY": "secret-token"})
    assert "secret-token" not in repr(settings)
    assert "secret-token" not in str(settings.model_dump())
    assert settings.diagnostics()["research_provider"] == "configured"
    assert Settings.from_env({}).diagnostics()["research_provider"] == "manual_file"
    assert assess_source("nbs", AT, AT - timedelta(days=5), timedelta(days=3)).state == "stale"
    assert assess_source("nbs", AT, None, timedelta(days=3)).state == "unavailable"


def test_monthly_atomic_snapshot_failure_retains_old_and_age(tmp_path: Path) -> None:
    store = ForecastSnapshotStore(tmp_path / "prediction.json")
    old_prediction = prediction(AT - timedelta(days=30)).model_copy(
        update={"prediction_id": "p-old"}
    )
    old = PredictionFrame(run_id="run1", predictions=[old_prediction])
    store.publish(old)
    before = store.path.read_bytes()

    def fail() -> PredictionFrame:
        raise RuntimeError("failed model")

    view = store.monthly(AT, inputs_available=True, produce=fail)
    assert view.stale and view.age_days == 30 and store.path.read_bytes() == before
    view = store.monthly(AT, inputs_available=False, produce=fail)
    assert view.reason == "NOT_YET_PUBLISHED" and store.path.read_bytes() == before
    runner = JobRunner()
    status, result = forecast_monthly(
        runner,
        store,
        AT,
        "snap2",
        True,
        lambda: PredictionFrame(run_id="run1", predictions=[prediction()]),
    )
    assert status.state == JobState.SUCCEEDED and result is not None
    assert store.load().predictions[0].as_of == AT
    runner.close()


def test_failed_monthly_job_can_retry_same_input(tmp_path: Path) -> None:
    store = ForecastSnapshotStore(tmp_path / "predictions.json")
    runner = JobRunner()

    def fail() -> PredictionFrame:
        raise TimeoutError("model error")

    failed, _ = forecast_monthly(runner, store, AT, "same-input", True, fail)
    assert failed.state == JobState.FAILED
    succeeded, _ = forecast_monthly(
        runner,
        store,
        AT,
        "same-input",
        True,
        lambda: PredictionFrame(run_id="run1", predictions=[prediction()]),
    )
    assert succeeded.state == JobState.SUCCEEDED
    runner.close()


def test_duplicate_cash_actions_and_backward_snapshot_rejected(tmp_path: Path) -> None:
    asset = AssetMaster(
        asset_id="ETF_TEST",
        exchange="SSE",
        instrument_type="ETF",
        name_zh="合成ETF",
        list_at=AT - timedelta(days=30),
    )
    action = CorporateAction(
        asset_id=asset.asset_id,
        ex_date=AT.date(),
        action_type="cash_dividend",
        distribution_per_unit=1,
    )
    with pytest.raises(ValueError, match="Duplicate"):
        MarketDataset(HistoricalUniverse([asset]), [], [action, action], [])
    store = ForecastSnapshotStore(tmp_path / "predictions.json")
    store.publish(PredictionFrame(run_id="run1", predictions=[prediction()]))
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="Older"):
        store.publish(
            PredictionFrame(
                run_id="run1",
                predictions=[
                    prediction(AT - timedelta(days=1)).model_copy(
                        update={"prediction_id": "p-earlier"}
                    )
                ],
            )
        )
    assert store.path.read_bytes() == before
    changed = prediction().model_copy(update={"predicted_probability": 0.9})
    with pytest.raises(ValueError, match="Frozen prediction"):
        store.publish(PredictionFrame(run_id="run1", predictions=[changed]))
    assert len(list((tmp_path / "predictions-history").glob("*.json"))) == 1


def test_future_fitted_scaler_rejected_by_historical_economy() -> None:
    class EmptyRepository:
        def fetch(self, series_ids, ctx):
            raise AssertionError("Must reject before data query")

    future = AT + timedelta(days=30)
    scaler = RobustScaler().fit([(future, 100)], future)
    ctx = AnalysisContext(
        as_of=AT,
        research_mode=ResearchMode.STRICT_PIT,
        industry_id="CN_NBS_C31",
        dataset_snapshot_id="fixture",
        mapping_version="1",
    )
    with pytest.raises(ValueError, match="cutoff"):
        EconomyEngine(EmptyRepository()).calculate([], ctx, {"x": scaler})
