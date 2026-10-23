"""把研究库里可见的 PMI 和行业财务，收成宏观状态、行业改善和待验证假设。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from industrypulse.contracts import ResearchMode
from industrypulse.economy.state import (
    CYCLE_LABELS,
    MISSING_DIMENSIONS,
    REGIME_LABELS,
    acceleration,
    classify_regime,
    cyclical_state,
    expansion_breadth,
    growth_hypothesis,
)
from industrypulse.services.workbench import Workbench

PMI = {
    "headline": "cn.nbs.pmi.headline",
    "new_orders": "cn.nbs.pmi.new_orders",
    "production": "cn.nbs.pmi.production",
    "employment": "cn.nbs.pmi.employment",
    "raw_inventory": "cn.nbs.pmi.raw_inventory",
    "supplier_delivery": "cn.nbs.pmi.supplier_delivery",
}
PMI_NAMES = {
    "headline": "制造业 PMI",
    "new_orders": "新订单",
    "production": "生产",
    "employment": "从业人员",
    "raw_inventory": "原材料库存",
    "supplier_delivery": "供应商配送时间",
}


def _points(rows: Sequence[Any]) -> list[Any]:
    chosen: dict[object, Any] = {}
    for row in rows:
        current = chosen.get(row.observation_start)
        if current is None or row.vintage_id > current.vintage_id:
            chosen[row.observation_start] = row
    return [chosen[key] for key in sorted(chosen)]


def _reading(row: Any, previous: Any | None) -> dict[str, Any]:
    change = (
        None
        if previous is None or row.value is None or previous.value is None
        else (float(row.value) - float(previous.value))
    )
    return {
        "period": row.observation_start.isoformat(),
        "value": None if row.value is None else float(row.value),
        "change": change,
        "unit": row.unit,
        "source_url": str(row.source_url) if row.source_url else None,
        "available_at": row.available_at.isoformat(),
    }


def _series_points(rows: Sequence[Any], series_id: str) -> list[Any]:
    return _points([row for row in rows if row.series_id == series_id and row.value is not None])


def _number(row: Any) -> float | None:
    if row is None or row.value is None:
        return None
    return float(row.value)


def build_economic_state(
    service: Workbench,
    mode: ResearchMode,
    as_of: datetime,
    snapshot: str = "",
) -> dict[str, Any]:
    repository = service.repository()
    snapshot_id = service.snapshot(repository, snapshot)
    from types import SimpleNamespace

    clock = SimpleNamespace(as_of=as_of, research_mode=mode, dataset_snapshot_id=snapshot_id)
    pmi_rows = repository.fetch(list(PMI.values()), clock).vintages
    components: dict[str, dict[str, Any]] = {}
    levels: dict[str, float] = {}
    for key, series_id in PMI.items():
        points = _series_points(pmi_rows, series_id)
        if not points:
            components[key] = {"id": key, "name": PMI_NAMES[key], "status": "missing"}
            continue
        latest, previous = points[-1], points[-2] if len(points) > 1 else None
        reading = _reading(latest, previous)
        reading.update({"id": key, "name": PMI_NAMES[key], "status": "ok", "series_id": series_id})
        if key == "supplier_delivery":
            reading["note"] = (
                "高于 50 表示交货加快。合成 PMI 时按 100 减该指数计入，不把它当成扩张。"
            )
        elif key == "raw_inventory":
            reading["note"] = "高于 50 表示原材料库存环比上升，不直接当成需求改善。"
        components[key] = reading
        if reading["value"] is not None:
            levels[key] = float(reading["value"])
    headline_points = _series_points(pmi_rows, PMI["headline"])
    headline_values = [float(row.value) for row in headline_points]
    level = headline_values[-1] if headline_values else None
    previous = headline_values[-2] if len(headline_values) > 1 else None
    regime = classify_regime(level, previous)
    breadth = expansion_breadth(levels)
    covered = sum(key in levels for key in ("new_orders", "production", "employment"))
    industries = []
    hypotheses = []
    gaps = []
    for path in sorted((service.root / "config/industries").glob("*.yaml")):
        registry = service.taxonomy(path.stem)
        name = registry.nodes[0].names.get("zh", path.stem)
        industry_id = registry.nodes[0].industry_id
        profit_id = next(
            (
                item.series_id
                for item in registry.series
                if item.series_id.endswith("profit_ytd_yoy")
            ),
            "",
        )
        if registry.synthetic or not profit_id:
            if not registry.synthetic and not registry.series and industry_id == "CN_NBS_C3972":
                gaps.append(
                    {
                        "id": path.stem,
                        "name": name,
                        "industry_id": industry_id,
                        "status": "INSUFFICIENT_EVIDENCE",
                        "reason": "官方工业利润表没有半导体单列，不能把电子设备大类改名为半导体",
                    }
                )
            continue
        wanted = {
            "profit": profit_id,
            "revenue": profit_id.replace("profit_ytd_yoy", "revenue_ytd_yoy"),
            "cost": profit_id.replace("profit_ytd_yoy", "cost_ytd_yoy"),
        }
        rows = repository.fetch(list(wanted.values()), clock).vintages
        series_points = {key: _series_points(rows, series_id) for key, series_id in wanted.items()}
        latest = {key: points[-1] if points else None for key, points in series_points.items()}
        prior = {
            key: points[-2] if len(points) > 1 else None for key, points in series_points.items()
        }
        profit = _number(latest["profit"])
        previous_profit = _number(prior["profit"])
        profit_change = (
            None if profit is None or previous_profit is None else profit - previous_profit
        )
        revenue = _number(latest["revenue"])
        cost = _number(latest["cost"])
        urls = sorted(
            {str(row.source_url) for row in latest.values() if row is not None and row.source_url}
        )
        period = latest["profit"].observation_start if latest["profit"] is not None else None
        state = cyclical_state(profit, profit_change)
        industries.append(
            {
                "id": path.stem,
                "name": name,
                "industry_id": industry_id,
                "period": None if period is None else period.isoformat(),
                "profit_yoy": profit,
                "profit_change": profit_change,
                "revenue_yoy": revenue,
                "cost_yoy": cost,
                "cyclical_state": state,
                "cyclical_label": CYCLE_LABELS[state],
                "source_urls": urls,
                "note": "累计同比相对上一公布期的变动，不是当月同比。",
            }
        )
        hypothesis = growth_hypothesis(
            industry_id=industry_id,
            name=name,
            period=period,
            profit_yoy=profit,
            profit_change=profit_change,
            revenue_yoy=revenue,
            cost_yoy=cost,
            source_urls=urls,
        )
        if hypothesis:
            hypotheses.append(hypothesis)
    industries.sort(
        key=lambda item: (
            item["profit_change"] if isinstance(item["profit_change"], int | float) else -(10**9)
        ),
        reverse=True,
    )
    return {
        "as_of": as_of.isoformat(),
        "mode": mode.value,
        "snapshot_id": snapshot_id,
        "disclaimer": "研究状态，不是官方经济周期认定，也不是投资信号。单月越过 50 不能当成趋势。",
        "macro": {
            "regime": regime,
            "regime_label": REGIME_LABELS[regime],
            "level": level,
            "momentum": None if level is None or previous is None else level - previous,
            "acceleration": acceleration(headline_values),
            "breadth": breadth,
            "breadth_note": (
                "生产、新订单、从业人员、原材料库存四项都可见时，计算高于 50 的比例。"
                "供应商配送时间是逆指数，不计入。"
            ),
            "period": components["headline"].get("period"),
            "source_url": components["headline"].get("source_url"),
            "components": [components[key] for key in PMI],
            "dimensions": [
                {
                    "id": "demand",
                    "name": "需求",
                    "status": "proxy",
                    "detail": "制造业 PMI 新订单，50 为荣枯线",
                },
                {
                    "id": "production",
                    "name": "生产",
                    "status": "proxy",
                    "detail": "制造业 PMI 生产，不是行业产量",
                },
                {
                    "id": "employment",
                    "name": "就业",
                    "status": "proxy",
                    "detail": "制造业 PMI 从业人员，不是城镇调查失业率",
                },
                *[
                    {"id": item[0], "name": item[1], "status": "missing", "detail": item[2]}
                    for item in MISSING_DIMENSIONS
                ],
            ],
            "coverage": covered / 8,
            "coverage_note": "八个目标维度里，目前只有需求、生产、就业有 PMI 代理。",
        },
        "industries": industries,
        "hypotheses": hypotheses,
        "gaps": gaps,
    }
