"""新浪财经 ETF/指数日线适配器：不复权原始价 + 限速采集。

口径已用腾讯 raw/qfq 双序列交叉验证：新浪返回**不复权原始价**
（641 个重叠交易日最大绝对差 0.000000）。因此 ``MarketBar.adj_method`` 必须写
``"none"``，总收益需由现金分红加回（见 ``investment/market.py``）。

证据等级：第三方聚合站点，合同 v1 无对应 EvidenceClass，按 C 级处理，
并在 ``notes`` 记录来源与复权口径。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as dtime
from decimal import Decimal

from industrypulse.contracts.common import TZ_SHANGHAI
from industrypulse.contracts.domain import MarketBar
from industrypulse.contracts.sources import Frequency

SINA_KLINE = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
)

#: 单次请求允许的最大记录数（超过会被服务端截断）。
MAX_ROWS = 1900


class SinaParseError(ValueError):
    """返回体不符合预期；拒绝猜测。"""


@dataclass(frozen=True)
class SinaQuote:
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


def parse_sina_kline(payload: str) -> list[SinaQuote]:
    """解析新浪日线 JSON 数组；任何字段缺失/非法即拒绝整批。"""
    try:
        rows = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SinaParseError(f"不是合法 JSON：{exc}") from exc
    if not isinstance(rows, list) or not rows:
        raise SinaParseError("返回体为空数组或非数组")
    quotes: list[SinaQuote] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SinaParseError(f"第 {index} 行不是对象")
        try:
            quotes.append(
                SinaQuote(
                    trade_date=date.fromisoformat(str(row["day"])),
                    open=Decimal(str(row["open"])),
                    high=Decimal(str(row["high"])),
                    low=Decimal(str(row["low"])),
                    close=Decimal(str(row["close"])),
                    volume=Decimal(str(row["volume"])),
                )
            )
        except (KeyError, ValueError) as exc:
            raise SinaParseError(f"第 {index} 行字段缺失或非法：{exc}") from exc
    quotes.sort(key=lambda item: item.trade_date)
    for left, right in zip(quotes, quotes[1:], strict=False):
        if right.trade_date <= left.trade_date:
            raise SinaParseError("交易日必须严格升序且不重复")
    return quotes


def to_market_bars(quotes: list[SinaQuote], *, asset_id: str) -> list[MarketBar]:
    """转成不可变 MarketBar；adj_method 固定 none（复权已在采集层验证）。"""
    return [
        MarketBar(
            asset_id=asset_id,
            trade_date=quote.trade_date,
            open=quote.open,
            high=quote.high,
            low=quote.low,
            close=quote.close,
            volume=quote.volume,
            adj_method="none",
            frequency=Frequency.DAILY,
        )
        for quote in quotes
    ]


def fetch_sina_kline(
    symbol: str,
    *,
    client: object,
    rows: int = MAX_ROWS,
    interval: float = 1.2,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """采集原始 JSON 文本；调用方负责归档与解析。

    ``client`` 需提供 ``get(url, params=...)``（httpx.Client 即可）。
    """
    if not symbol or not symbol.isalnum():
        raise ValueError(f"非法代码：{symbol!r}")
    if not 1 <= rows <= MAX_ROWS:
        raise ValueError(f"rows 必须在 1..{MAX_ROWS}")
    sleep(interval)
    response = client.get(  # type: ignore[attr-defined]
        SINA_KLINE,
        params={"symbol": symbol, "scale": "240", "ma": "no", "datalen": rows},
    )
    response.raise_for_status()
    return str(response.text)


def derived_list_at(quotes: list[SinaQuote]) -> datetime:
    """无主档来源时，用首个交易日作为上市时点的**下界估计**（需在 notes 声明）。"""
    if not quotes:
        raise SinaParseError("空序列无法推导上市时点")
    return datetime.combine(quotes[0].trade_date, dtime(9, 30), tzinfo=TZ_SHANGHAI)


__all__ = [
    "MAX_ROWS",
    "SINA_KLINE",
    "SinaParseError",
    "SinaQuote",
    "derived_list_at",
    "fetch_sina_kline",
    "parse_sina_kline",
    "to_market_bars",
]
