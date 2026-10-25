"""新浪财经公开接口：行业/概念资金流，以及国内期货主力连续报价。

资金流在东财 clist 断连时作为领域净流入的兜底，名称来自接口原文，调用方必须原样标注。
期货报价是主力连续合约的最新价与相对昨结算的涨跌幅，不是现货中间价。
"""

from __future__ import annotations

from typing import Any

import httpx

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) IndustryPulse",
    "Referer": "https://finance.sina.com.cn",
}
_FLOW_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_bk"
)
_FUTURES_URL = "https://hq.sinajs.cn/list="


def parse_flow_payload(rows: Any) -> dict[str, dict[str, Any]]:
    """category -> {name, netamount}。netamount 单位为元。"""
    found: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list):
        return found
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("category") or "")
        if not code:
            continue
        try:
            amount = float(row["netamount"])
        except (KeyError, TypeError, ValueError):
            continue
        found[code] = {"name": str(row.get("name") or code), "netamount": amount}
    return found


def fetch_board_flows() -> dict[str, dict[str, Any]]:
    """申万行业、概念、证监会行业三套分类的当日净流入。"""
    found: dict[str, dict[str, Any]] = {}
    with httpx.Client(timeout=12, headers=_HEADERS, follow_redirects=True) as client:
        for fenlei in ("0", "1", "2"):
            response = client.get(
                _FLOW_URL,
                params={
                    "page": "1",
                    "num": "300",
                    "sort": "netamount",
                    "asc": "0",
                    "fenlei": fenlei,
                },
            )
            response.raise_for_status()
            found.update(parse_flow_payload(response.json()))
    if not found:
        raise ValueError("新浪行业资金流为空")
    return found


def parse_futures_payload(text: str) -> dict[str, dict[str, Any]]:
    """解析 hq.sinajs.cn 期货行。字段 8 为最新价，10 为昨结算，17 为交易日。"""
    found: dict[str, dict[str, Any]] = {}
    for line in text.split(";"):
        if "hq_str_nf_" not in line or '"' not in line:
            continue
        left, right = line.split('"', 1)
        symbol = left.split("hq_str_nf_", 1)[-1].split("=", 1)[0].strip()
        fields = right.split('"')[0].split(",")
        if len(fields) < 18 or not symbol:
            continue
        try:
            price = float(fields[8])
            previous = float(fields[10])
        except ValueError:
            continue
        if price <= 0 or previous <= 0:
            continue
        found[symbol] = {
            "name": fields[0],
            "price": price,
            "change_pct": round((price / previous - 1) * 100, 2),
            "date": fields[17][:10],
        }
    return found


def fetch_futures(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """主力连续报价。symbols 形如 RB0、LC0，不含 nf_ 前缀。"""
    if not symbols:
        return {}
    query = ",".join(f"nf_{symbol}" for symbol in symbols)
    response = httpx.get(_FUTURES_URL + query, headers=_HEADERS, timeout=12)
    response.raise_for_status()
    found = parse_futures_payload(response.content.decode("gbk", "replace"))
    if not found:
        raise ValueError("新浪期货行情为空")
    return found
