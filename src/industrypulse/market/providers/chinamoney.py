"""中国货币网公开行情：人民币中间价、LPR、Shibor、国债收益率。

这些序列由中国外汇交易中心发布，页面与 JSON 均免登录。停更的 akshare 镜像不再作为主源。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) IndustryPulse"}
_CCPR = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew"
_LPR = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/LprHis"
_SHIBOR = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-shibor/ShiborHis"
_YIELD = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/SddsIntrRateGovYldHis"


def _window(days: int) -> dict[str, str]:
    end = date.today()
    start = end - timedelta(days=days)
    return {"lang": "CN", "startDate": start.isoformat(), "endDate": end.isoformat()}


def parse_dated_records(
    records: Any, date_key: str, fields: dict[str, str]
) -> list[dict[str, Any]]:
    """records 里的日期列和数值列，归一化为 {period, values}。fields 是 源字段 -> 指标字段。"""
    if not isinstance(records, list):
        return []
    rows: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        raw_date = record.get(date_key)
        if not raw_date:
            continue
        try:
            period = date.fromisoformat(str(raw_date)[:10])
        except ValueError:
            continue
        values: dict[str, float] = {}
        for source_field, target_field in fields.items():
            raw_value = record.get(source_field)
            if raw_value in (None, ""):
                continue
            try:
                values[target_field] = float(str(raw_value))
            except (TypeError, ValueError):
                continue
        if values:
            rows.append({"period": period, "values": values})
    rows.sort(key=lambda item: item["period"])
    return rows


def parse_ccpr(records: Any) -> list[dict[str, Any]]:
    """美元中间价。records[].values[0] 为 USD/CNY。"""
    if not isinstance(records, list):
        return []
    rows: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict) or not record.get("date"):
            continue
        values = record.get("values") or []
        if not values or values[0] in (None, ""):
            continue
        try:
            period = date.fromisoformat(str(record["date"])[:10])
            price = float(values[0])
        except (TypeError, ValueError):
            continue
        rows.append({"period": period, "values": {"value": price}})
    rows.sort(key=lambda item: item["period"])
    return rows


def fetch_series(source: str) -> list[dict[str, Any]]:
    """按注册表 source 取一条已归一化的序列。空结果视为失败。"""
    specs: dict[str, tuple[str, dict[str, str], str, dict[str, str]]] = {
        "lpr": (_LPR, _window(500), "showDateCN", {"1Y": "1Y", "5Y": "5Y"}),
        "shibor": (_SHIBOR, _window(40), "showDateCN", {"ON": "ON", "1W": "1W"}),
        "cn_yield": (_YIELD, _window(40), "dateString", {"tenRate": "value"}),
    }
    with httpx.Client(timeout=15, headers=_HEADERS, follow_redirects=True) as client:
        if source == "usdcny":
            params = {**_window(40), "currency": "USD/CNY", "pageNum": "1", "pageSize": "50"}
            response = client.get(_CCPR, params=params)
            response.raise_for_status()
            rows = parse_ccpr(response.json().get("records"))
        elif source in specs:
            url, params, date_key, fields = specs[source]
            response = client.get(url, params=params)
            response.raise_for_status()
            rows = parse_dated_records(response.json().get("records"), date_key, fields)
        else:
            raise ValueError(f"未知中国货币网序列: {source}")
    if not rows:
        raise ValueError(f"中国货币网 {source} 无记录")
    return rows
