"""Time and timezone utilities."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")


def now_shanghai() -> datetime:
    return datetime.now(TZ_SHANGHAI)
