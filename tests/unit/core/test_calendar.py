"""交易日历装载：拒绝一切会让 PIT 规则静默退化的输入。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from industrypulse.core import CalendarError, load_trading_calendar

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/g0/market_calendar.json"


def _write(tmp_path: Path, days: list[str], **extra: object) -> Path:
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps({"trading_days": days, **extra}), encoding="utf-8")
    return path


def test_loads_frozen_synthetic_fixture() -> None:
    calendar = load_trading_calendar(FIXTURE)
    assert calendar.exchange == "SSE"
    assert calendar.version == "fixture-synthetic-v1"
    assert len(calendar.days) == 222
    assert calendar.days[0] == date(2025, 4, 1)
    assert calendar.covers(date(2025, 4, 1))
    assert not calendar.covers(date(2025, 3, 31))
    assert len(calendar.source_sha256) == 64


def test_weekend_is_rejected(tmp_path: Path) -> None:
    # 2025-01-04 是周六：中国交易所不可能有交易日，出现即说明解析或来源有误
    with pytest.raises(CalendarError):
        load_trading_calendar(_write(tmp_path, ["2025-01-03", "2025-01-04"]))


def test_duplicate_day_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CalendarError):
        load_trading_calendar(_write(tmp_path, ["2025-01-02", "2025-01-02"]))


def test_unordered_input_is_normalized_not_silently_kept(tmp_path: Path) -> None:
    calendar = load_trading_calendar(_write(tmp_path, ["2025-01-03", "2025-01-02"]))
    assert calendar.days == [date(2025, 1, 2), date(2025, 1, 3)]


def test_empty_calendar_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CalendarError):
        load_trading_calendar(_write(tmp_path, []))


def test_suspicious_gap_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CalendarError):
        load_trading_calendar(_write(tmp_path, ["2025-01-02", "2025-03-03"]), max_gap_days=14)


def test_non_iso_day_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CalendarError):
        load_trading_calendar(_write(tmp_path, ["2025/01/02"]))


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CalendarError):
        load_trading_calendar(tmp_path / "absent.json")


def test_version_falls_back_to_content_hash(tmp_path: Path) -> None:
    calendar = load_trading_calendar(_write(tmp_path, ["2025-01-02", "2025-01-03"]))
    assert calendar.version == f"sha256-{calendar.source_sha256[:12]}"
    assert calendar.as_tuple() == (date(2025, 1, 2), date(2025, 1, 3))
