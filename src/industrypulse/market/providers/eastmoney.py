"""东方财富公开接口：行业板块 K 线、板块资金流、宏观数据中心、北向资金。

push2his（K 线）在部分网络下间歇失败，调用方需配合镜像与缓存（见 service）。
数据中心 datacenter-web 对不存在的报表返回明确的「报表配置不存在」。
"""

from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any

import httpx

from ..models import Bar, MoneyFlowPoint

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) IndustryPulse"}
_KLINE_MIRRORS = ["https://push2his.eastmoney.com/api/qt/stock/kline/get"]
_FLOW_MIRRORS = [
    "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get",
    "https://push2delay.eastmoney.com/api/qt/stock/fflow/kline/get",
    "https://push2his.eastmoney.com/api/qt/stock/fflow/kline/get",
]
_DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_NORTHBOUND_REPORT = "RPT_MUTUAL_DEAL_HISTORY"


def _get_json(client: httpx.Client, mirrors: list[str], params: dict[str, str]) -> dict[str, Any]:
    last_error: Exception | None = None
    for mirror in mirrors:
        try:
            response = client.get(mirror, params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            time.sleep(0.2)
    raise ConnectionError(f"东财接口全部镜像失败: {last_error}")


def fetch_board_kline(quote_id: str, bars: int = 760) -> list[Bar]:
    """行业板块日 K（fields2: 日期,开,收,高,低,量,额,振幅）。"""
    with httpx.Client(timeout=7, headers=_HEADERS) as client:
        payload = _get_json(
            client,
            _KLINE_MIRRORS,
            {
                "secid": quote_id,
                "klt": "101",
                "fqt": "0",
                "lmt": str(bars),
                "end": "20500101",
                "fields1": "f1,f2,f3",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            },
        )
    rows = (payload.get("data") or {}).get("klines") or []
    result: list[Bar] = []
    for row in rows:
        parts = str(row).split(",")
        try:
            result.append(
                Bar(
                    date=datetime.strptime(parts[0], "%Y-%m-%d").date(),
                    open=float(parts[1]),
                    close=float(parts[2]),
                    high=float(parts[3]),
                    low=float(parts[4]),
                    volume=float(parts[5]) if len(parts) > 5 else None,
                    amount=float(parts[6]) if len(parts) > 6 else None,
                    source="eastmoney",
                )
            )
        except (ValueError, IndexError):
            continue
    if not result:
        raise ValueError(f"东财板块 K 线无数据: {quote_id}")
    return result


def fetch_board_moneyflow(quote_id: str, days: int = 90) -> list[MoneyFlowPoint]:
    """板块资金流历史（f51 日期, f52 主力净流入额·元）。"""
    with httpx.Client(timeout=7, headers=_HEADERS) as client:
        payload = _get_json(
            client,
            _FLOW_MIRRORS,
            {
                "lmt": str(days),
                "klt": "101",
                "secid": quote_id,
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56",
            },
        )
    rows = (payload.get("data") or {}).get("klines") or []
    result: list[MoneyFlowPoint] = []
    for row in rows:
        parts = str(row).split(",")
        try:
            result.append(
                MoneyFlowPoint(
                    date=datetime.strptime(parts[0], "%Y-%m-%d").date(),
                    main_net_inflow=float(parts[1]),
                )
            )
        except (ValueError, IndexError):
            continue
    return result


def fetch_macro_report(report: str, max_rows: int = 600) -> list[dict[str, Any]]:
    """数据中心报表（列名 ALL；分页拉全量后由调用方归一化）。"""
    rows: list[dict[str, Any]] = []
    page = 1
    with httpx.Client(timeout=15, headers=_HEADERS) as client:
        while len(rows) < max_rows:
            response = client.get(
                _DATACENTER,
                params={
                    "reportName": report,
                    "columns": "ALL",
                    "pageSize": "500",
                    "pageNumber": str(page),
                },
            )
            response.raise_for_status()
            payload = response.json()
            result = payload.get("result") or {}
            data = result.get("data") or []
            if not data:
                if page == 1:
                    raise ValueError(payload.get("message") or f"报表无数据: {report}")
                break
            rows.extend(data)
            pages_total = int(result.get("pages") or 1)
            if page >= pages_total:
                break
            page += 1
    return rows


def fetch_northbound(days: int = 120) -> list[dict[str, Any]]:
    """沪深港通北向历史（RPT_MUTUAL_DEAL_HISTORY，全市场口径）。"""
    rows = fetch_macro_report(_NORTHBOUND_REPORT, max_rows=days)
    date_key = next(
        (key for key in rows[0] if "DATE" in key.upper()), None
    ) if rows else None
    if date_key is None:
        raise ValueError("北向报表缺少日期列")
    normalized: list[dict[str, Any]] = []
    for row in rows:
        raw = row.get(date_key)
        if isinstance(raw, str) and len(raw) >= 10:
            row = {**row, "_date": date.fromisoformat(raw[:10])}
            normalized.append(row)
    return normalized
