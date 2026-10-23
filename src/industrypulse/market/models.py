"""统一市场/宏观数据层的标准模型（Dashboard Current View，独立于研究 PIT 合同）。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class Bar(BaseModel):
    """日行情棒（指数 / 板块 / ETF / 个股通用）。"""

    date: date
    open: float | None = None
    close: float
    high: float | None = None
    low: float | None = None
    volume: float | None = None
    amount: float | None = None
    source: str = "tencent"


class QuoteSnapshot(BaseModel):
    """实时报价快照（腾讯批量接口字段）。"""

    symbol: str
    name: str
    price: float | None = None
    change: float | None = None
    change_pct: float | None = None
    amount: float | None = Field(default=None, description="成交额（元）")
    turnover_pct: float | None = None
    pe: float | None = None
    pb: float | None = None
    market_cap: float | None = Field(default=None, description="总市值（亿元）")
    quoted_at: datetime | None = None
    source: Literal["tencent"] = "tencent"


class MoneyFlowPoint(BaseModel):
    date: date
    main_net_inflow: float | None = Field(default=None, description="主力净流入（元）")
    source: str = "eastmoney"


class MacroPoint(BaseModel):
    indicator_id: str
    name: str
    value: float
    unit: str = ""
    period: date
    source: str
    freq: str = "monthly"


class SourceStatus(BaseModel):
    source_id: str
    provider: str
    kind: Literal["quotes", "kline", "moneyflow", "macro", "official", "sector", "persist"] = (
        "quotes"
    )
    status: Literal["active", "degraded", "failed", "blocked", "idle"] = "idle"
    last_success: datetime | None = None
    last_attempt: datetime | None = None
    latency_ms: int | None = None
    note: str = ""
