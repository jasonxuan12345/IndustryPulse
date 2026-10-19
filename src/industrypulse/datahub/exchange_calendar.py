"""交易所交易日历推导：以"当日是否存在官方日行情文件"为证据。

上期所站点的 ``calendar_YYYY.dat`` 与 ``calendar-data.js`` 都含周末/假期条目，
是交割或活动日历，不能当交易日历（见 docs/g2/p3-acceptance.md）。本模块改用
**可验证证据**：对每个工作日探测官方日行情端点，

- 返回 200 且是当日行情 -> 该日是交易日；
- 返回 404（重试后仍 404）-> 该日无行情文件，判为非交易日；
- 其它状态或网络异常 -> 抛出，**不猜**。

推断的边界被写死：只处理"足够过去"的日期（行情文件已发布），未来/当天一律拒绝，
避免把"还没发"误判为"不交易"。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Protocol

from industrypulse.contracts.common import ContractBaseModel, NonEmptyStr
from industrypulse.core.calendar import TradingCalendar


class ProbeError(RuntimeError):
    """探测结果不可用于判定（网络异常、5xx、未知状态），必须中止而不是猜。"""


class DayProbe(Protocol):
    """返回 True=有当日行情、False=官方明确无该日行情。"""

    def __call__(self, day: date) -> bool: ...


class DerivationReport(ContractBaseModel):
    """推导结果的审计摘要。"""

    exchange: NonEmptyStr
    start: NonEmptyStr
    end: NonEmptyStr
    probed: int
    trading_days: int
    skipped_weekends: int
    evidence_sha256: NonEmptyStr


def _is_weekend(day: date) -> bool:
    return day.weekday() >= 5


def derive_trading_days(
    probe: DayProbe,
    *,
    start: date,
    end: date,
    today: date,
    publication_lag_days: int = 2,
) -> tuple[list[date], list[str], DerivationReport]:
    """按证据推导区间内交易日；返回 (交易日, 证据行, 摘要)。"""
    if start > end:
        raise ValueError("起始日不得晚于结束日")
    if publication_lag_days < 0:
        raise ValueError("publication_lag_days 必须非负")
    cutoff = today - timedelta(days=publication_lag_days)
    if end > cutoff:
        raise ProbeError(
            f"结束日 {end} 晚于可验证截止 {cutoff}：行情文件可能尚未发布，"
            "拒绝把'还没发'当成'不交易'"
        )
    days: list[date] = []
    evidence: list[str] = []
    weekends = 0
    current = start
    probed = 0
    while current <= end:
        if _is_weekend(current):
            weekends += 1
            evidence.append(f"{current.isoformat()} weekend")
            current += timedelta(days=1)
            continue
        probed += 1
        has_data = probe(current)
        evidence.append(f"{current.isoformat()} {'200' if has_data else '404'}")
        if has_data:
            days.append(current)
        current += timedelta(days=1)
    if not days:
        raise ProbeError("区间内没有任何交易日：疑似探测逻辑或端点错误")
    digest = hashlib.sha256("\n".join(evidence).encode()).hexdigest()
    report = DerivationReport(
        exchange="SHFE",
        start=start.isoformat(),
        end=end.isoformat(),
        probed=probed,
        trading_days=len(days),
        skipped_weekends=weekends,
        evidence_sha256=digest,
    )
    return days, evidence, report


def build_calendar(
    days: Sequence[date],
    *,
    report: DerivationReport,
    source_ref: str,
    version: str,
) -> TradingCalendar:
    """把推导结果固化为 :class:`TradingCalendar`（出处为证据摘要哈希）。"""
    return TradingCalendar(
        version=version,
        exchange=report.exchange,
        source_ref=source_ref,
        source_sha256=report.evidence_sha256,
        days=list(days),
    )


__all__ = [
    "DayProbe",
    "DerivationReport",
    "ProbeError",
    "build_calendar",
    "derive_trading_days",
]
