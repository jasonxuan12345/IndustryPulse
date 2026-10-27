from __future__ import annotations

import json
from datetime import datetime

from industrypulse.contracts import TZ_SHANGHAI
from industrypulse.services import ledger, ledger_runtime
from industrypulse.services.workbench import Workbench


def test_source_and_quote_failures_keep_original_records_and_health(tmp_path, monkeypatch):
    service = Workbench(tmp_path)
    source = tmp_path / "data/research/hypotheses.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps(
            {
                "hypothesis_id": "real-source",
                "created_at": "2026-10-26T17:00:00+08:00",
                "statement": "需求改善",
                "chain": "电力",
                "observation_months": 3,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ledger_runtime, "refresh_prices", lambda *_: [{"asset": "ETF_159611", "error": "offline"}]
    )

    def no_screen(*_):
        raise ValueError("研究库不足")

    monkeypatch.setattr(ledger_runtime, "capture_screen", no_screen)
    now = datetime(2026, 10, 27, 18, tzinfo=TZ_SHANGHAI)
    ledger_runtime.configure(service, True)
    result = ledger_runtime.run(service, now=now, refresh=True)
    assert result["status"] == "degraded"
    assert len(result["errors"]) == 2
    path = tmp_path / "data/ledger/recommendations.jsonl"
    history = ledger.load_ledger(path)
    assert len(history) == 1 and history[0]["created_at"] == now.isoformat()
    assert history[0]["asset"] == ""
    assert (tmp_path / "data/ledger/weekly.json").exists()
    assert ledger_runtime.run(service, now=now)["seed"]["seeded"] == 0
    assert len(ledger.load_ledger(path)) == 1
    assert ledger_runtime.settings(service)["enabled"]
    service.pool.shutdown()
    service.runner.close()


def test_growth_capture_uses_current_visible_versions_and_does_not_buy(tmp_path):
    from decimal import Decimal
    from types import SimpleNamespace

    from industrypulse.contracts.observation import ObservationVintage

    folder = tmp_path / "config/industries"
    folder.mkdir(parents=True)
    (folder / "power.yaml").write_text("stub", encoding="utf-8")
    prefix = "cn.nbs.industry_d44"

    def row(kind, period, value, released, version=1):
        stamp = datetime.fromisoformat(released)
        return ObservationVintage(
            series_id=prefix + "." + kind + "_ytd_yoy",
            observation_start=datetime.fromisoformat(period).date(),
            observation_end=datetime.fromisoformat(period).date(),
            value=Decimal(value),
            unit="%",
            publication_at=stamp,
            retrieved_at=stamp,
            available_at=stamp,
            vintage_valid_from=stamp,
            vintage_id=version,
            availability_basis="observed_publication",
            release_evidence="A",
            value_evidence="C",
            source_url="https://www.stats.gov.cn/original",
            artifact_id="original",
        )

    rows = [
        row("profit", "2026-08-01", "2", "2026-09-27T09:30:00+08:00"),
        row("profit", "2026-09-01", "5", "2026-10-26T09:30:00+08:00"),
        row("revenue", "2026-09-01", "1", "2026-10-26T09:30:00+08:00"),
        row("cost", "2026-09-01", "2", "2026-10-26T09:30:00+08:00"),
        row("profit", "2026-09-01", "-20", "2026-11-26T09:30:00+08:00", 2),
    ]
    repo = SimpleNamespace(
        all_vintages=lambda _: rows, trading_days=(), conservative_lag_days=5, series_lag_days={}
    )
    registry = SimpleNamespace(
        synthetic=False,
        series=[SimpleNamespace(series_id=prefix + ".profit_ytd_yoy")],
        nodes=[SimpleNamespace(industry_id="CN_NBS_D44", names={"zh": "电力"})],
    )
    service = SimpleNamespace(root=tmp_path, repository=lambda: repo, taxonomy=lambda _: registry)
    now = datetime(2026, 10, 27, 18, tzinfo=TZ_SHANGHAI)
    assert ledger_runtime.capture_growth(service, now)["created_hypotheses"] == 1
    assert ledger_runtime.capture_growth(service, now)["created_hypotheses"] == 0
    record = next(
        iter(ledger.latest_versions(tmp_path / "data/ledger/recommendations.jsonl").values())
    )
    assert record["category"] == "economic_hypothesis" and record["asset"] == ""
    assert record["source_snapshot"]["facts"][0]["value"] == 5  # 不取未来修订-20
    assert record["created_at"] == now.isoformat()
    assert (
        ledger_runtime.capture_growth(service, datetime(2026, 11, 27, 18, tzinfo=TZ_SHANGHAI))[
            "created_hypotheses"
        ]
        == 0
    )


def test_monitor_pause_never_removes_ledger_and_does_not_start_other_jobs(tmp_path):
    service = Workbench(tmp_path)
    path = tmp_path / "data/ledger/recommendations.jsonl"
    ledger.register(
        source_kind="test",
        source_id="one",
        category="economic_hypothesis",
        subject="电力",
        statement="需求改善",
        path=path,
    )
    before = path.read_bytes()
    ledger_runtime.configure(service, True)
    ledger_runtime.configure(service, False)
    assert path.read_bytes() == before
    assert service.scheduler is None
    service.pool.shutdown()
    service.runner.close()
