"""Controlled vintage API and schema-checked manual backup sources."""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from typing import Any

import httpx


def fred_observations(
    series: str, vintage: date, api_key: str, client: httpx.Client
) -> dict[str, Any]:
    if not api_key:
        raise ValueError("尚未配置 FRED_API_KEY")
    offset = 0
    rows = []
    while True:
        response = client.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "api_key": api_key,
                "file_type": "json",
                "series_id": series,
                "realtime_start": vintage.isoformat(),
                "realtime_end": vintage.isoformat(),
                "output_type": 1,
                "limit": 100000,
                "offset": offset,
            },
        )
        response.raise_for_status()
        payload = response.json()
        batch = payload["observations"]
        for item in batch:
            date.fromisoformat(item["date"])
            if item["value"] != ".":
                Decimal(item["value"])
        rows.extend(batch)
        offset += len(batch)
        if offset >= int(payload["count"]) or not batch:
            break
    return {
        "source": "FRED/ALFRED",
        "series": series,
        "vintage": vintage.isoformat(),
        "observations": rows,
    }


def customs_import(payload: Any) -> list[dict[str, Any]]:
    rows = list(csv.DictReader(io.StringIO(payload))) if isinstance(payload, str) else payload
    result = []
    for row in rows:
        code = str(row["hs_code"])
        if not code.isdigit() or len(code) not in (2, 4, 6, 8, 10):
            raise ValueError("HS 编码须为 2/4/6/8/10 位数字字符串")
        year = int(row["year"])
        if year < 1990 or not row.get("hs_version"):
            raise ValueError("必须声明年份和 HS 版本")
        unit = row["unit"]
        if unit not in {"吨", "tonne", "kg", "千克"}:
            raise ValueError("海关数量仅接受显式吨/千克单位")
        value = Decimal(str(row["value"]))
        if not value.is_finite() or value < 0:
            raise ValueError("海关数量不得为负或非有限值")
        result.append(
            {
                **row,
                "hs_code": code,
                "year": year,
                "quantity_tonnes": str(value / 1000 if unit in {"kg", "千克"} else value),
                "original_unit": unit,
            }
        )
    return result


def akshare_smoke(payload: list[dict[str, Any]]) -> dict[str, Any]:
    required = {"日期", "开盘", "收盘", "最高", "最低", "成交量"}
    drift = sorted(required - set(payload[0])) if payload else sorted(required)
    if drift:
        return {"status": "SCHEMA_DRIFT", "missing": drift, "publishable": False}
    for row in payload:
        date.fromisoformat(str(row["日期"])[:10])
        opening, close, high, low = (float(row[key]) for key in ("开盘", "收盘", "最高", "最低"))
        if not 0 < low <= min(opening, close) <= max(opening, close) <= high:
            raise ValueError("备用行情 OHLC 不一致")
    return {
        "status": "SCHEMA_OK",
        "rows": len(payload),
        "publishable": False,
        "note": "仅备用数据验证；须指定资产、复权与来源后通过行情导入接口发布",
    }
