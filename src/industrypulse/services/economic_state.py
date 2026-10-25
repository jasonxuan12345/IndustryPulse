"""把研究库里可见的 PMI 和行业财务，收成宏观状态、行业改善和待验证假设。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

import yaml

from industrypulse.contracts import ResearchMode
from industrypulse.economy.price_check import (
    HORIZONS,
    PriceLine,
    adjacent_periods,
    benjamini_hochberg,
    compare,
    forward_excess,
    signal_history,
)
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


class _PriceLines:
    def __init__(self, service: Workbench, database: Any, as_of: datetime) -> None:
        from industrypulse.repositories import DuckDBMarketRepository

        path = service.root / "config/market/industry_price_lines.yaml"
        config = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.benchmark = config.get("benchmark") or {}
        self.mapping = config.get("industries") or {}
        self.market = DuckDBMarketRepository(database)
        self.day = as_of.date()
        self.cache: dict[str, PriceLine | None] = {}
        self.barriers: dict[str, tuple[date, ...]] = {}

    def line(self, asset_id: str) -> PriceLine | None:
        if asset_id not in self.cache:
            bars = self.market.bars(asset_id, end=self.day)
            self.cache[asset_id] = (
                PriceLine(
                    tuple(bar.trade_date for bar in bars),
                    tuple(float(bar.close) for bar in bars),
                    tuple(
                        (action.ex_date, float(action.distribution_per_unit))
                        for action in self.market.actions(asset_id)
                        if action.action_type == "cash_dividend" and action.ex_date <= self.day
                    ),
                )
                if bars
                else None
            )
        return self.cache[asset_id]

    def barriers_for(self, asset_id: str) -> tuple[date, ...]:
        """疑似份额拆并日：缺口超过阈值且已登记行动解释不了。回放时跨越即剔除。"""
        if asset_id not in self.barriers:
            from industrypulse.services.etf_audit import find_barriers

            bars = self.market.bars(asset_id, end=self.day)
            actions = [
                action for action in self.market.actions(asset_id) if action.ex_date <= self.day
            ]
            self.barriers[asset_id] = tuple(
                date.fromisoformat(event["day"]) for event in find_barriers(bars, actions)
            )
        return self.barriers[asset_id]

    def check(self, industry_id: str, series_points: dict[str, list[Any]]) -> dict[str, Any]:
        spec = self.mapping.get(industry_id)
        if not spec:
            return {"status": "NO_PRICE_LINE", "reason": "没有找到对口的场内 ETF"}
        described = {
            "asset_id": spec["asset_id"],
            "name": spec["name"],
            "fit": spec["fit"],
            "note": spec.get("note", ""),
            "benchmark": self.benchmark.get("name"),
        }
        asset = self.line(spec["asset_id"])
        benchmark = self.line(self.benchmark.get("asset_id", ""))
        if asset is None or benchmark is None:
            return {**described, "status": "NO_BARS", "reason": "研究库里还没有这条价格线的日线"}
        asset_barriers = self.barriers_for(str(spec["asset_id"]))
        benchmark_barriers = self.barriers_for(str(self.benchmark.get("asset_id", "")))
        releases = signal_history(
            [
                (row.observation_start, row.available_at, _number(row))
                for row in series_points["profit"]
            ],
            {row.observation_start: _number(row) for row in series_points["revenue"]},
        )
        horizons = []
        for months, trading_days in HORIZONS:
            outcomes, pending, blocked = forward_excess(
                releases,
                asset,
                benchmark,
                trading_days,
                barriers=asset_barriers + benchmark_barriers,
            )
            horizons.append(
                {
                    "months": months,
                    "trading_days": trading_days,
                    "pending": pending,
                    "excluded_corporate_event": blocked,
                }
                | compare(outcomes)
            )
        return {
            **described,
            "status": "NOT_VALIDATED",
            "first_day": asset.days[0].isoformat(),
            "last_day": asset.days[-1].isoformat(),
            "suspected_splits": [day.isoformat() for day in asset_barriers],
            "horizons": horizons,
        }


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
    price_lines = _PriceLines(service, repository.database, as_of)
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
        note = "累计同比相对上一公布期的变动，不是当月同比。"
        if (
            latest["profit"] is not None
            and latest["revenue"] is not None
            and latest["profit"].observation_start < latest["revenue"].observation_start
        ):
            latest["profit"] = prior["profit"] = None
            note = "最近公布期官方没有给利润累计同比（上年同期亏损时不计算），不判断改善。"
        if (
            latest["profit"] is not None
            and prior["profit"] is not None
            and not adjacent_periods(
                prior["profit"].observation_start, latest["profit"].observation_start
            )
        ):
            prior["profit"] = None
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
        shown = latest["profit"] or latest["revenue"]
        period = shown.observation_start if shown is not None else None
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
                "note": note,
                "price_check": price_lines.check(industry_id, series_points),
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
    tested = [
        horizon
        for item in industries
        for horizon in item["price_check"].get("horizons", [])
        if horizon["status"] == "TESTED"
    ]
    for horizon, q_value in zip(
        tested, benjamini_hochberg([h["p_value"] for h in tested]), strict=True
    ):
        horizon["q_value"] = q_value
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
        "price_check": {
            "status": "NOT_VALIDATED",
            "tests": len(tested),
            "method": (
                "信号同增长假设规则。发布日后第一个交易日收盘入场，持有约 3 / 6 个月"
                "（63 / 126 个交易日），比较 ETF 与沪深 300 的总收益差。"
                "收益为不复权收盘价加回期间现金分红（不再投资），零费用。"
                "窗口重叠，用循环平移检验求单侧 p 值，再对全部检验做 BH 校正。"
                "疑似份额拆并日（价格缺口且无已登记行动解释）跨越的窗口整段剔除，"
                "计入 excluded_corporate_event。"
            ),
            "disclaimer": (
                "历史描述，不是收益预测，也不是投资建议。ETF 与统计局行业口径不同，"
                "样本只有几十个月，q 值小也只说明值得继续研究。"
            ),
        },
    }
