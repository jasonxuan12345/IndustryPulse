"""akshare 宏观适配层（可选依赖）：调用社区验证过的公开数据端点。

akshare 缺失或调用失败时抛出异常，由上层记录健康状态并降级；
本文件不复制 akshare 实现，只做调用与归一化。
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, cast

import pandas as pd

_PERIOD_PATTERNS = [
    re.compile(r"^(\d{4})年(\d{2})月份?$"),
    re.compile(r"^(\d{4})年(\d{1,2})月"),
    re.compile(r"^(\d{4})(\d{2})$"),
    re.compile(r"^(\d{4})\.(\d{1,2})$"),
]


def parse_period(raw: Any) -> date:
    """把各源五花八门的期次表示归一化为 date（月度取当月 1 日）。"""
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    for pattern in _PERIOD_PATTERNS:
        matched = pattern.match(text)
        if matched:
            year, month = int(matched.group(1)), int(matched.group(2))
            return date(year, month, 1)
    quarter = re.match(r"^(\d{4})年第(\d)(?:-(\d))?季度", text)
    if quarter:
        last = quarter.group(3) or quarter.group(2)
        month = int(last) * 3 - 2
        return date(int(quarter.group(1)), month, 1)
    raise ValueError(f"无法解析期次: {raw!r}")


def fetch_frame(function_name: str) -> pd.DataFrame:
    import akshare as ak

    function = getattr(ak, function_name, None)
    if function is None:
        raise AttributeError(f"akshare 无函数 {function_name}")
    frame = function()
    if frame is None or len(frame) == 0:
        raise ValueError(f"akshare {function_name} 返回空表")
    return cast(pd.DataFrame, frame)


def normalize_frame(
    frame: pd.DataFrame, date_column: str, value_columns: list[str]
) -> list[dict[str, Any]]:
    """转成 [{period: date, values: {column: float}}]，升序去重（同月取最后出现）。"""
    records: dict[date, dict[str, float | None]] = {}
    for _, row in frame.iterrows():
        try:
            period = parse_period(row[date_column])
        except (ValueError, TypeError, KeyError):
            continue
        values: dict[str, float | None] = {}
        for column in value_columns:
            raw = row.get(column)
            value = None
            if raw is not None and raw == raw:  # NaN 判定
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    value = None
            values[column] = value
        records[period] = values
    return [{"period": key, "values": value} for key, value in sorted(records.items())]
