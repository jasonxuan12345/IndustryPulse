"""Local dashboard and replaceable sector intelligence feed."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import AwareDatetime, BaseModel, Field, HttpUrl

from industrypulse.contracts import TZ_SHANGHAI

ROOT = Path(__file__).resolve().parents[3]
SECTORS = {
    "semiconductor": "半导体",
    "ai": "人工智能",
    "energy": "新能源",
    "steel": "钢铁",
    "health": "医药生物",
}


class Point(BaseModel):
    date: str
    value: float
    benchmark: float | None = None


class Sector(BaseModel):
    id: str
    name: str
    change: float | None = None
    net_flow: float | None = None
    heat: float | None = Field(default=None, ge=0, le=100)
    trend: list[Point] = Field(default_factory=list)


class Story(BaseModel):
    id: str
    sector: str
    category: Literal["news", "hotspot", "personnel"]
    title: str
    summary: str
    published_at: AwareDatetime
    source: str
    url: HttpUrl | None = None
    company: str | None = None
    tags: list[str] = Field(default_factory=list)


class Company(BaseModel):
    id: str
    sector: str
    name: str
    symbol: str
    focus: str
    change: float | None = None


class Feed(BaseModel):
    mode: Literal["demo", "live"]
    updated_at: AwareDatetime
    source: str
    flow_unit: str = "亿元"
    trend_unit: str = "归一化指数（起点 100）"
    trend_label: str = "行业指数"
    benchmark_label: str = "市场基准"
    sectors: list[Sector]
    stories: list[Story]
    companies: list[Company]


def demo_feed() -> Feed:
    import math

    now = datetime.now(TZ_SHANGHAI)
    sectors = []
    stories = []
    companies: list[Company] = []
    titles = [
        (
            "hotspot",
            "先进封装与算力基础设施成为产业观察焦点",
            "关注产能利用率、设备交付和下游订单之间的变化。此条为虚构界面样例。",
        ),
        (
            "news",
            "产业链观察：从设备订单看新一轮资本开支",
            "将公司公告、财报和产业数据集中整理，保留原文入口和发布时间。此条为虚构界面样例。",
        ),
        (
            "personnel",
            "示例公司宣布研发负责人调整",
            "人事接口展示职位、任免公告和公司关联；当前人物与事件均为演示。",
        ),
        (
            "news",
            "需求跟踪：终端补库与库存周期如何演变",
            "交叉观察库存、价格与交期，避免把单一事件直接等同于行业趋势。此条为虚构界面样例。",
        ),
        (
            "hotspot",
            "技术路线观察：新工艺的量产节奏",
            "持续汇总研发进度、客户验证和量产公告，后续由资讯 API 提供。此条为虚构界面样例。",
        ),
    ]
    focus = ["设备与材料", "制造与封装", "设计与应用"]
    for index, (sector_id, name) in enumerate(SECTORS.items()):
        trend = [
            Point(
                date=(now.date() - timedelta(days=offset)).isoformat(),
                value=round(
                    100
                    + (179 - offset) * (0.07 - index * 0.018)
                    + math.sin((179 - offset) / 9 + index) * 2.4,
                    2,
                ),
                benchmark=round(100 + (179 - offset) * 0.025 + math.sin((179 - offset) / 14), 2),
            )
            for offset in range(179, -1, -1)
        ]
        sectors.append(
            Sector(
                id=sector_id,
                name=name,
                change=[2.36, 1.82, -0.64, 0.78, -1.12][index],
                net_flow=[18.62, 12.48, -6.82, 3.21, -4.56][index],
                heat=[92, 86, 68, 54, 61][index],
                trend=trend,
            )
        )
        for position, (category, title, summary) in enumerate(titles):
            stories.append(
                Story(
                    id=f"{sector_id}-{position}",
                    sector=sector_id,
                    category=category,
                    title=title if index == 0 else f"{name} · {title}",
                    summary=summary,
                    published_at=now - timedelta(hours=position * 7 + index),
                    source="虚构演示 · 非真实新闻",
                    company=f"示例{name}科技",
                    tags=[
                        name,
                        ["产业趋势", "资本开支", "管理层", "供需跟踪", "技术进展"][position],
                    ],
                )
            )
        companies.extend(
            Company(
                id=f"{sector_id}-{position}",
                sector=sector_id,
                name=f"示例{name}{suffix}",
                symbol="DEMO",
                focus=focus[position],
                change=[1.82, -0.56, 2.41][position],
            )
            for position, suffix in enumerate(["科技", "制造", "研究"])
        )
    return Feed(
        mode="demo",
        updated_at=now,
        source="内置虚构样例",
        sectors=sectors,
        stories=stories,
        companies=companies,
    )


def create_dashboard_router() -> APIRouter:
    router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
    feed_path = Path(os.getenv("INDUSTRYPULSE_FEED_FILE", str(ROOT / "data/intelligence.json")))

    @router.get("/feed", response_model=Feed)
    def feed() -> Feed:
        remote = os.getenv("INDUSTRYPULSE_FEED_URL")
        try:
            if remote:
                token = os.getenv("INDUSTRYPULSE_FEED_TOKEN")
                headers = {"Authorization": f"Bearer {token}"} if token else {}
                with httpx.Client(timeout=15) as client:
                    response = client.get(remote, headers=headers)
                    response.raise_for_status()
                return Feed.model_validate(response.json())
            if feed_path.exists():
                return Feed.model_validate_json(feed_path.read_text(encoding="utf-8"))
            return demo_feed()
        except (httpx.HTTPError, ValueError, OSError) as exc:
            raise HTTPException(502, "资讯源读取失败，请检查接口配置或导入文件") from exc

    @router.post("/feed", response_model=Feed)
    def import_feed(payload: Feed) -> Feed:
        if os.getenv("INDUSTRYPULSE_FEED_URL"):
            raise HTTPException(409, "当前使用远程接口；请先移除 FEED_URL 再使用文件导入")
        feed_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = feed_path.with_suffix(".tmp")
        temporary.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(feed_path)
        return payload

    @router.get("/schema")
    def schema() -> dict[str, object]:
        return Feed.model_json_schema()

    @router.get("/research")
    def research() -> dict[str, object]:
        from industrypulse.repositories.database import Database

        path = Path(os.getenv("INDUSTRYPULSE_DASHBOARD_DB", str(ROOT / "data/g2.duckdb")))
        if not path.exists():
            return {"mode": "unavailable", "series": [], "message": "未找到研究数据库"}
        try:
            database = Database(path)
            with database.transaction() as connection:
                rows = connection.execute(
                    "SELECT payload FROM observation_vintage ORDER BY observation_start, vintage_id"
                ).fetchall()
            grouped: dict[str, list[dict[str, object]]] = {}
            for row in rows:
                item = json.loads(row[0])
                grouped.setdefault(item["series_id"], []).append(item)
            return {
                "mode": "local",
                "message": "本地当前版本浏览 · 非 STRICT PIT · 不代表预测有效性",
                "count": len(rows),
                "series": [
                    {"id": key, "count": len(items), "latest": items[-1], "history": items}
                    for key, items in grouped.items()
                ],
            }
        except (OSError, RuntimeError) as exc:
            raise HTTPException(503, "研究数据库暂不可读") from exc

    return router
