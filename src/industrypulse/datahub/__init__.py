"""数据采集、标准化与 Bronze/Silver 仓储（lane S 连接器 + lane D 存储）。"""

from .discovery import KINDS, Announcement, discover, in_range
from .exchange_calendar import (
    DayProbe,
    DerivationReport,
    ProbeError,
    build_calendar,
    derive_trading_days,
)

__all__ = [
    "KINDS",
    "Announcement",
    "DayProbe",
    "DerivationReport",
    "ProbeError",
    "build_calendar",
    "derive_trading_days",
    "discover",
    "in_range",
]
