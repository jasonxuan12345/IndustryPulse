"""同期原件证据：窗口内才升格，其余一律保持 C。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from industrypulse.contracts import TZ_SHANGHAI
from industrypulse.services import AsPublishedClaim, ClaimError, evaluate_claim, parse_claim

PUB = datetime(2025, 8, 27, 9, 30, tzinfo=TZ_SHANGHAI)


def test_parse_claim_variants() -> None:
    assert parse_claim(None) is None
    assert parse_claim("  ") is None
    auto = parse_claim("auto")
    assert auto is not None and auto.method == "capture" and auto.captured_at is None
    explicit = parse_claim("capture:2025-08-27T17:00:00+08:00")
    assert explicit is not None and explicit.captured_at is not None


@pytest.mark.parametrize(
    "raw,match",
    [
        ("wayback:2025-08-27T17:00:00+08:00", "未支持的证据方法"),
        ("capture", "需要显式时点"),
        ("capture:not-a-date", "不是合法 ISO8601"),
        ("capture:2025-08-27T17:00:00", "必须带时区"),
    ],
)
def test_parse_claim_rejects_bad_input(raw: str, match: str) -> None:
    with pytest.raises(ClaimError, match=match):
        parse_claim(raw)


def test_no_publication_time_cannot_be_contemporary() -> None:
    verdict = evaluate_claim(
        AsPublishedClaim(method="capture"),
        publication_at=None,
        fallback_captured_at=PUB,
    )
    assert verdict.ok is False
    assert "无可核验发布时间" in verdict.reason


def test_capture_before_publication_is_contradictory() -> None:
    verdict = evaluate_claim(
        AsPublishedClaim(method="capture"),
        publication_at=PUB,
        fallback_captured_at=PUB - timedelta(hours=1),
    )
    assert verdict.ok is False
    assert "早于发布时间" in verdict.reason


def test_window_boundary() -> None:
    on_edge = evaluate_claim(
        AsPublishedClaim(method="capture"),
        publication_at=PUB,
        fallback_captured_at=PUB + timedelta(hours=48),
    )
    assert on_edge.ok is True and on_edge.window_hours == 48.0
    too_late = evaluate_claim(
        AsPublishedClaim(method="capture"),
        publication_at=PUB,
        fallback_captured_at=PUB + timedelta(hours=48, minutes=1),
    )
    assert too_late.ok is False
    assert "超出" in too_late.reason


def test_explicit_capture_time_wins_over_fallback() -> None:
    claim = AsPublishedClaim(method="capture", captured_at=PUB + timedelta(hours=2))
    verdict = evaluate_claim(
        claim, publication_at=PUB, fallback_captured_at=PUB + timedelta(days=30)
    )
    assert verdict.ok is True and verdict.window_hours == 2.0


def test_missing_claim_stays_conservative() -> None:
    verdict = evaluate_claim(None, publication_at=PUB, fallback_captured_at=PUB)
    assert verdict.ok is False
    assert "未提供同期原件证据" in verdict.reason
