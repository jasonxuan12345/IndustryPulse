"""腾讯行情公开接口：批量实时报价与日 K 历史（主源）。

实时报价 qt.gtimg.cn 返回 GBK 文本，字段以 ~ 分隔；K 线 web.ifzq.gtimg.cn 返回
JSON，day 数组为 [date, open, close, high, low, volume]。均为公开、免登录端点。
"""

from __future__ import annotations

from datetime import datetime

import httpx

from industrypulse.contracts import TZ_SHANGHAI

from ..models import Bar, QuoteSnapshot

_QUOTES_URL = "https://qt.gtimg.cn/q="
_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) IndustryPulse"}
# v_ 串 0 起算字段位置（经 2026-10-23 真实报文逐字段核对）：
# 3 最新价 31 涨跌 32 涨跌% 37 成交额(万) 38 换手率 39 PE(TTM)
# 44 流通市值(亿) 45 总市值(亿) 46 PB 47/48 涨停/跌停；指数与 ETF 无 PE/PB/市值。
_PRICE, _CHANGE, _CHANGE_PCT, _AMOUNT_WAN, _TURNOVER, _PE, _FLOW_CAP, _TOTAL_CAP, _PB = (
    3,
    31,
    32,
    37,
    38,
    39,
    44,
    45,
    46,
)


def fetch_quotes(symbols: list[str]) -> dict[str, QuoteSnapshot]:
    if not symbols:
        return {}
    response = httpx.get(_QUOTES_URL + ",".join(symbols), headers=_HEADERS, timeout=12)
    response.raise_for_status()
    text = response.content.decode("gbk", errors="replace")
    snapshots: dict[str, QuoteSnapshot] = {}
    for line in text.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line or '"' not in line:
            continue
        symbol = line.split("=", 1)[0].removeprefix("v_").strip()
        fields = line.split('"', 1)[1].split("~")
        if len(fields) <= _PB:
            continue

        def number_at(index: int, values: list[str] = fields) -> float | None:
            raw = values[index].strip() if index < len(values) else ""
            try:
                return float(raw) if raw else None
            except ValueError:
                return None

        stamp = fields[30].strip() if len(fields) > 30 else ""
        quoted_at = None
        if len(stamp) >= 14:
            try:
                quoted_at = datetime.strptime(stamp[:14], "%Y%m%d%H%M%S").replace(
                    tzinfo=TZ_SHANGHAI
                )
            except ValueError:
                quoted_at = None
        amount_wan = number_at(_AMOUNT_WAN)
        snapshots[symbol] = QuoteSnapshot(
            symbol=symbol,
            name=fields[1].strip(),
            price=number_at(_PRICE),
            change=number_at(_CHANGE),
            change_pct=number_at(_CHANGE_PCT),
            amount=amount_wan * 10_000 if amount_wan is not None else None,
            turnover_pct=number_at(_TURNOVER),
            pe=number_at(_PE) or None,
            pb=number_at(_PB) or None,
            market_cap=number_at(_TOTAL_CAP) or None,
            quoted_at=quoted_at,
        )
    return snapshots  # 个别代码缺失容错：返回拿到的，由健康层记录


def fetch_daily_kline(symbol: str, bars: int = 760) -> list[Bar]:
    response = httpx.get(
        _KLINE_URL,
        params={"param": f"{symbol},day,,,{bars},qfq"},
        headers=_HEADERS,
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    node = payload.get("data", {}).get(symbol, {})
    rows = node.get("qfqday") or node.get("day") or []
    if not rows:
        raise ValueError(f"腾讯 K 线无数据: {symbol}")
    result: list[Bar] = []
    for row in rows:
        try:
            result.append(
                Bar(
                    date=datetime.strptime(str(row[0]), "%Y-%m-%d").date(),
                    open=float(row[1]),
                    close=float(row[2]),
                    high=float(row[3]),
                    low=float(row[4]),
                    volume=float(row[5]) if len(row) > 5 else None,
                    source="tencent",
                )
            )
        except (ValueError, IndexError):
            continue
    if not result:
        raise ValueError(f"腾讯 K 线解析失败: {symbol}")
    return result
