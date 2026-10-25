"""领先指标扫描（阶段 2）：变化检测、稳定事件 ID、账本幂等、AI 门槛。"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from industrypulse.contracts import TZ_SHANGHAI
from industrypulse.services import leading

# ------------------------------------------------------------------ 纯函数


def test_next_profit_release_is_next_month_27th() -> None:
    moment = datetime(2026, 10, 25, 19, 0, tzinfo=TZ_SHANGHAI)
    assert leading.next_profit_release(moment) == datetime(2026, 11, 27, 9, 30, tzinfo=TZ_SHANGHAI)
    year_end = datetime(2026, 12, 31, tzinfo=TZ_SHANGHAI)
    assert leading.next_profit_release(year_end) == datetime(2027, 1, 27, 9, 30, tzinfo=TZ_SHANGHAI)


def test_event_id_depends_on_chain_and_is_stable() -> None:
    first = leading.event_id("auto", "pmi_new_orders", "2026-09-01", "value_change", "down")
    again = leading.event_id("auto", "pmi_new_orders", "2026-09-01", "value_change", "down")
    other_chain = leading.event_id(
        "nonferrous", "pmi_new_orders", "2026-09-01", "value_change", "down"
    )
    assert first == again
    assert first != other_chain


def test_value_change_respects_threshold_and_missing_values() -> None:
    observation = {
        "period": "2026-09-01",
        "value": 50.0,
        "previous_period": "2026-08-01",
        "previous_value": 50.3,
        "release_at": "2026-09-30T09:30:00+08:00",
    }
    event = leading.detect_value_change("pmi", observation, threshold=0.5)
    assert event is not None
    assert event["direction"] == "down" and event["material"] is False
    assert event["relative_to"] == "2026-08-01 的 50.3"
    assert leading.detect_value_change("pmi", {"value": None, "previous_value": 1.0}, 0.5) is None


def test_momentum_needs_history_and_reacts_only_to_flips() -> None:
    history = [{"at": "2026-10-01", "price": 100.0, "momentum": 2.0}] * 5
    # 同向动量：不产生事件。
    assert leading.detect_momentum("cu0", 105.0, "2026-10-24", history) is None
    # 动量翻负：产生事件。
    event = leading.detect_momentum("cu0", 97.0, "2026-10-24", history)
    assert event is not None
    assert event["change_kind"] == "momentum_flip" and event["direction"] == "down"
    assert event["material"] is True
    # 历史不足：不产生事件。
    assert leading.detect_momentum("cu0", 97.0, "2026-10-24", history[:2]) is None


def test_sign_flip_ignores_first_scan_and_same_sign() -> None:
    assert (
        leading.detect_sign_flip("etf_rel", 1.5, "2026-10-23", [], change_kind="relative_flip")
        is None
    )
    history = [{"at": "2026-10-22", "value": -0.4, "sign": "down"}]
    event = leading.detect_sign_flip(
        "etf_rel", 1.5, "2026-10-23", history, change_kind="relative_flip"
    )
    assert event is not None and event["direction"] == "up"
    same = leading.detect_sign_flip(
        "etf_rel", -0.9, "2026-10-23", history, change_kind="relative_flip"
    )
    assert same is None


# ------------------------------------------------------------------ 账本


def test_append_events_dedupes_within_batch_and_across_runs(tmp_path: Path) -> None:
    ledger = tmp_path / "events.jsonl"
    event = {"event_id": "lead-1", "what_changed": "x"}
    assert leading.append_events([event, dict(event)], ledger) == 1
    assert leading.append_events([dict(event)], ledger) == 0
    lines = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert leading.load_event_ids(ledger) == {"lead-1"}


# ------------------------------------------------------------------ 端到端


class FakeRepository:
    """研究库替身：一条 PMI 序列，两个期次。"""

    def __init__(self) -> None:
        self.series = "cn.nbs.pmi.new_orders"
        self.rows = [
            SimpleNamespace(
                series_id=self.series,
                observation_start=date(2026, 8, 1),
                value=50.6,
                publication_at=datetime(2026, 8, 31, 9, 30, tzinfo=TZ_SHANGHAI),
                source_url="https://example.test/a",
            ),
            SimpleNamespace(
                series_id=self.series,
                observation_start=date(2026, 9, 1),
                value=51.2,
                publication_at=datetime(2026, 9, 30, 9, 30, tzinfo=TZ_SHANGHAI),
                source_url="https://example.test/b",
            ),
        ]

    def select(self, series_ids, ctx):  # noqa: ANN001, ANN202
        return SimpleNamespace(selected=list(self.rows), skipped=[])

    def all_vintages(self, series_ids):  # noqa: ANN001, ANN202
        return list(self.rows)


class FakeMarket:
    def bars(self, asset_id, **_):  # noqa: ANN001, ANN202
        return []

    def actions(self, asset_id):  # noqa: ANN001, ANN202
        return ()


@pytest.fixture()
def scan_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离账本/状态文件 + 单候选配置 + 打桩网络采集。"""
    monkeypatch.setattr(leading, "EVENTS_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(leading, "STATE_PATH", tmp_path / "state.json")
    config = tmp_path / "chains.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "chains": {
                    "nonferrous": {
                        "name": "有色金属",
                        "industries": [{"id": "CN_NBS_C32", "name": "有色金属冶炼和压延加工业"}],
                        "candidates": [
                            {
                                "id": "pmi_new_orders",
                                "name": "制造业 PMI 新订单",
                                "kind": "research",
                                "series": "cn.nbs.pmi.new_orders",
                                "release": "每月 31 日",
                                "material": 0.5,
                                "verify": "{next_profit} 核对 {industry}",
                            }
                        ],
                    }
                }
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return config


def test_scan_writes_event_once_and_reports_latency(scan_env: Path) -> None:
    moment = datetime(2026, 10, 25, 19, 0, tzinfo=TZ_SHANGHAI)
    payload = leading.scan(
        FakeRepository(), FakeMarket(), snapshot_id=None, now=moment, chains_path=scan_env
    )
    assert payload["new_events"] == 1
    assert payload["queued_for_ai"] == 1  # +0.6 越过 0.5 阈值
    event = payload["events"][0]
    assert event["event_id"].startswith("lead-")
    release = datetime(2026, 9, 30, 9, 30, tzinfo=TZ_SHANGHAI)
    assert event["detection_lag_hours"] == pytest.approx(
        (moment - release).total_seconds() / 3600, abs=0.1
    )
    assert "2026-11-27" in event["next_verification"]
    ledger = leading.EVENTS_PATH.read_text(encoding="utf-8").splitlines()
    assert len(ledger) == 1

    again = leading.scan(
        FakeRepository(), FakeMarket(), snapshot_id=None, now=moment, chains_path=scan_env
    )
    assert again["new_events"] == 0
    assert len(leading.EVENTS_PATH.read_text(encoding="utf-8").splitlines()) == 1


def test_scan_skips_sub_threshold_changes(scan_env: Path) -> None:
    repository = FakeRepository()
    repository.rows[-1].value = 50.8  # 变化 +0.2，低于 0.5
    payload = leading.scan(
        repository,
        FakeMarket(),
        snapshot_id=None,
        now=datetime(2026, 10, 25, 19, 0, tzinfo=TZ_SHANGHAI),
        chains_path=scan_env,
    )
    assert payload["new_events"] == 1 and payload["queued_for_ai"] == 0
    assert payload["events"][0]["ai_status"] == "skipped"
