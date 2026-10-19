"""版本化交易日历：结构校验 + 出处指纹，拒绝猜测。

为什么需要这个模块：STRICT PIT 的 B 级规则是"只知日期的发布，顺延到**次一交易日**
09:30 后才可用"。此前 ``trading_days`` 只能由调用方注入，全仓唯一可用实例是
``tests/fixtures/g0/market_calendar.json`` 这份合成日历。真实回放时若拿不到日历，
``effective_available_at`` 会退化为 ``+1 自然日 00:00``——**不报错、静默变宽松**，
正是本项目要防的那类失效。

本模块只提供装载与校验，**不内置任何"看起来像真的"日历数据**：调用方必须提供
带出处的官方文件（上交所年度休市安排），装载时记录文件 sha256，使日历版本可审计。
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, ValidationError, model_validator

from industrypulse.contracts.common import ContractBaseModel, NonEmptyStr, Sha256Hex

#: 相邻交易日允许的最大自然日间隔。春节/国庆长假约 9-11 天；
#: 超过阈值的空洞通常意味着文件被截断、年份缺失或解析失败。
DEFAULT_MAX_GAP_DAYS = 14

#: 项目只覆盖中国交易所（§1.2，Asia/Shanghai）；沪深交易所从不在周六日交易。
_CN_EXCHANGES = frozenset({"SSE", "SZSE", "CFFEX", "SHFE", "DCE", "CZCE", "GFEX"})


class CalendarError(ValueError):
    """日历结构或来源不可信；拒绝装载而不是猜测。"""


class TradingCalendar(ContractBaseModel):
    """一份带出处的交易日历快照（不可变）。

    ``source_sha256`` 是日历文件本身的指纹，用于证明"这次回测用的是哪版日历"。
    """

    version: NonEmptyStr
    exchange: NonEmptyStr
    timezone: NonEmptyStr = "Asia/Shanghai"
    source_ref: NonEmptyStr
    source_sha256: Sha256Hex
    days: Annotated[list[date], Field(min_length=1)]

    @model_validator(mode="after")
    def _ordered_weekday_only(self) -> TradingCalendar:
        for previous, current in zip(self.days, self.days[1:], strict=False):
            if current <= previous:
                raise ValueError("交易日历必须严格升序且不重复")
        if self.exchange in _CN_EXCHANGES:
            weekend = [d for d in self.days if d.weekday() >= 5]
            if weekend:
                raise ValueError(f"中国交易所日历不得包含周六日：{weekend[0]}")
        return self

    def as_tuple(self) -> tuple[date, ...]:
        """直接可传给 :func:`industrypulse.contracts.pit.select_vintages_as_of`。"""
        return tuple(self.days)

    def covers(self, day: date) -> bool:
        """day 是否落在本日历声明区间内（注意：区间内也可能是休市日）。"""
        return self.days[0] <= day <= self.days[-1]

    def is_trading_day(self, day: date) -> bool:
        return day in set(self.days)


def _raw_days(path: Path) -> tuple[list[Any], str | None, str | None, str | None]:
    """读取原始日期序列，返回 (原始值列表, 版本, 出处, 交易所)。"""
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        if rows and "date" not in {key.lower() for key in rows[0]}:
            raise CalendarError("CSV 日历必须包含 date 列，禁止按列位置猜测")
        csv_days: list[Any] = [row.get("date") or row.get("DATE") for row in rows]
        return csv_days, None, None, None
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise CalendarError(f"日历不是合法 JSON：{exc}") from exc
    if not isinstance(document, dict):
        raise CalendarError("日历 JSON 顶层必须是对象")
    raw_days: Any = document.get("trading_days", document.get("days"))
    if not isinstance(raw_days, list):
        raise CalendarError("日历 JSON 缺少 trading_days 数组")
    version = document.get("calendar_version") or document.get("version")
    source = document.get("source_ref") or document.get("description") or document.get("source")
    declared_exchange = document.get("exchange")
    return (
        list(raw_days),
        version if isinstance(version, str) else None,
        (source if isinstance(source, str) else None),
        (declared_exchange if isinstance(declared_exchange, str) else None),
    )


def load_trading_calendar(
    path: str | Path,
    *,
    exchange: str = "SSE",
    max_gap_days: int = DEFAULT_MAX_GAP_DAYS,
) -> TradingCalendar:
    """装载并校验交易日历；任何结构问题都抛 :class:`CalendarError`。

    JSON 支持 ``calendar_version``/``version``、``trading_days``/``days``、
    ``source_ref``/``description``；CSV 必须有 ``date`` 列。
    版本缺省时用文件指纹前 12 位补齐，保证同一文件始终得到同一版本号。
    """
    if max_gap_days < 1:
        raise CalendarError("max_gap_days 必须为正")
    target = Path(path)
    if not target.is_file():
        raise CalendarError(f"日历文件不存在：{target}")
    raw = target.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    values, declared_version, declared_source, declared_exchange = _raw_days(target)

    days: list[date] = []
    for index, value in enumerate(values, start=1):
        if not isinstance(value, str):
            raise CalendarError(f"第 {index} 个交易日不是字符串：{value!r}")
        try:
            days.append(date.fromisoformat(value.strip()))
        except ValueError as exc:
            raise CalendarError(f"第 {index} 个交易日不是 ISO 日期：{value!r}") from exc
    if not days:
        raise CalendarError("日历为空；拒绝用空日历让 PIT 规则静默退化")

    ordered = sorted(days)
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if current - previous > timedelta(days=max_gap_days):
            raise CalendarError(
                f"{previous} 与 {current} 之间间隔超过 {max_gap_days} 天：疑似年份缺失或文件被截断"
            )

    try:
        return TradingCalendar(
            version=declared_version or f"sha256-{digest[:12]}",
            # 文件自带 exchange 时以文件为准，避免用 CLI 默认值覆盖来源声明
            exchange=declared_exchange or exchange,
            source_ref=declared_source or str(target.resolve()),
            source_sha256=digest,
            days=ordered,
        )
    except ValidationError as exc:
        # 统一错误类型：调用方只需捕获 CalendarError，不会漏掉 pydantic 的包装异常
        raise CalendarError(str(exc)) from exc


__all__ = [
    "DEFAULT_MAX_GAP_DAYS",
    "CalendarError",
    "TradingCalendar",
    "load_trading_calendar",
]
