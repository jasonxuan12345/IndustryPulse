"""阶段 4 · 模型连接机会：三个冻结目标的统一训练与验收入口。

三个目标分开训练、分开验收（基线要求）：

- T1 生产方向（``T1_IVA_DIRECTION``）：行业工业增加值同比的方向。只有
  钢铁（C31）有 IVA 序列，其他行业如实记为数据不足；
- T2 盈利改善（``T2_MARGIN_IMPROVE``）：利润累计同比相对上一公布期的
  方向；
- T3 相对收益（``T3_RELATIVE_RETURN_DIRECTION``）：决策日之后约 3 个月
  （63 个交易日）行业 ETF 相对沪深 300 的总收益差方向。

纪律：
- 模型只在未见过的时间段上比较（run_experiment 的外折 OOS + 冻结清单）；
- 先跑朴素/季节/AR/正则化（M00..M04）；高级模型（M05..M07）只有训练
  样本 ≥60 且每类 ≥10 才加入对照（registry 的门槛 + 本模块的预检）；
- T1/T2 的概率是经济指标方向概率，不是股票上涨概率；T3 是相对收益
  方向概率，不是收益幅度。没有估值历史与一致预期数据，不声称市场
  预期差，估值特征如实缺席；
- 价格特征只用决策日已收盘的 K 线（相对强弱、波动），资金流没有历史
  深度，暂不能作特征。
"""

from __future__ import annotations

from datetime import date, datetime, time
from types import SimpleNamespace
from typing import Any

from industrypulse.contracts import TZ_SHANGHAI
from industrypulse.contracts.context import ResearchMode
from industrypulse.contracts.model import (
    FeatureFrame,
    FeatureRow,
    TargetFrame,
    TargetRow,
    TargetSpec,
)
from industrypulse.economy.price_check import PriceLine
from industrypulse.repositories import DuckDBObservationRepository
from industrypulse.services.research_run import (
    DatasetBuild,
    _add_months,
    _month_ends,
    make_experiment_spec,
)

#: 市场特征：相对强弱（20/60 日总收益差）与已实现波动（20 日日收益标准差）。
MARKET_FEATURES = ("etf_rel_ret_20d", "etf_rel_ret_60d", "etf_vol_20d")
#: T3 标签窗口（交易日）。
T3_HORIZON_DAYS = 63
#: 高级模型加入对照的样本门槛（与 forecast.registry.AdvancedModel 一致）。
ADVANCED_MIN_ROWS = 60

BASELINE_GRID = {
    "M00_LAST": {},
    "M01_SEASONAL": {"window": [12, 36, 60]},
    "M02_AR": {"lag": [1, 2]},
    "M03_LOGIT": {"C": [0.1, 1.0]},
    "M04_ELASTIC": {"C": [0.1, 1.0]},
}
ADVANCED_GRID = {
    "M05_FACTOR": {},
    "M06_LIGHTGBM": {"n_estimators": [80]},
    "M07_MARKOV": {},
}


def t3_target_spec(industry_id: str, horizon_months: int = 3) -> TargetSpec:
    """行业资产相对收益方向的冻结目标（T3）。"""
    return TargetSpec(
        target_id="T3_RELATIVE_RETURN_DIRECTION",
        definition_version="1",
        industry_id=industry_id,
        horizon_months=horizon_months,
        baselines=["M00_LAST", "M01_SEASONAL", "M02_AR"],
        label_kind="direction_up",
        formula=f"1[rel_total_return(d, d+{T3_HORIZON_DAYS} trading days)>0]",
        missing_policy="UNSCORABLE while the forward window has not matured",
        description=(
            "行业 ETF 相对沪深 300 的总收益差（分红加回口径）方向；"
            "是相对收益方向概率，不是收益幅度，也不是个股上涨概率"
        ),
    )


def price_lines(
    market: Any, asset_id: str, benchmark_id: str
) -> tuple[PriceLine | None, PriceLine | None]:
    def line(asset: str) -> PriceLine | None:
        bars = market.bars(asset)
        if not bars:
            return None
        actions = [a for a in market.actions(asset) if a.action_type == "cash_dividend"]
        return PriceLine(
            tuple(bar.trade_date for bar in bars),
            tuple(float(bar.close) for bar in bars),
            tuple((a.ex_date, float(a.distribution_per_unit)) for a in actions),
        )

    return line(asset_id), line(benchmark_id)


def market_feature_view(
    asset: PriceLine,
    benchmark: PriceLine,
    day: date,
    *,
    short: int = 20,
    long: int = 60,
    vol_window: int = 20,
) -> dict[str, float | None]:
    """决策日收盘后可知的市场特征；历史不足给 None（缺失列承接）。"""
    common = sorted(set(asset.days) & set(benchmark.days))
    up_to = [d for d in common if d <= day]
    result: dict[str, float | None] = {
        "etf_rel_ret_20d": None,
        "etf_rel_ret_60d": None,
        "etf_vol_20d": None,
    }
    if len(up_to) <= short:
        return result
    end = up_to[-1]

    def rel(window: int) -> float | None:
        if len(up_to) <= window:
            return None
        start = up_to[-1 - window]
        return asset.total_return(start, end) - benchmark.total_return(start, end)

    result["etf_rel_ret_20d"] = rel(short)
    result["etf_rel_ret_60d"] = rel(long)
    asset_days = [d for d in asset.days if d <= day]
    if len(asset_days) > vol_window:
        closes = [asset.closes[asset.days.index(d)] for d in asset_days[-(vol_window + 1) :]]
        returns = [b / a - 1 for a, b in zip(closes, closes[1:], strict=False)]
        mean = sum(returns) / len(returns)
        result["etf_vol_20d"] = (sum((r - mean) ** 2 for r in returns) / len(returns)) ** 0.5
    return result


def attach_market_features(
    built: DatasetBuild, market: Any, asset_id: str, benchmark_id: str = "ETF_510300"
) -> DatasetBuild:
    """把市场特征并入已构建的研究帧；决策日之后的价格一律不可见。"""
    asset, benchmark = price_lines(market, asset_id, benchmark_id)
    if asset is None or benchmark is None:
        return built
    rows: list[FeatureRow] = []
    for row in built.features.rows:
        view = market_feature_view(asset, benchmark, row.decision_as_of.date())
        features = {**row.features, **view}
        missing = list(
            dict.fromkeys(
                [
                    *row.missing_features,
                    *[name for name in MARKET_FEATURES if features.get(name) is None],
                ]
            )
        )
        rows.append(row.model_copy(update={"features": features, "missing_features": missing}))
    frame = built.features.model_copy(update={"rows": rows})
    return DatasetBuild(
        features=frame,
        targets=built.targets,
        decision_dates=built.decision_dates,
        skipped=built.skipped,
    )


def build_relative_return_frames(
    repository: DuckDBObservationRepository,
    market: Any,
    *,
    snapshot_id: str | None,
    industry_id: str,
    asset_id: str,
    benchmark_id: str = "ETF_510300",
    profit_series: str,
    revenue_series: str,
    start: date,
    end: date,
    horizon_days: int = T3_HORIZON_DAYS,
    research_mode: ResearchMode = ResearchMode.RECONSTRUCTED_PIT,
) -> DatasetBuild:
    """T3 帧：月末决策、当期可知的市场与基本面特征、未来 63 交易日相对收益方向。"""
    asset, benchmark = price_lines(market, asset_id, benchmark_id)
    if asset is None or benchmark is None:
        raise ValueError("研究库里没有这条价格线的日线，无法构建 T3")
    common_all = sorted(set(asset.days) & set(benchmark.days))
    rows: list[FeatureRow] = []
    targets: list[TargetRow] = []
    decision_dates: list[date] = []
    skipped = 0
    for decision in _month_ends(start, end):
        clock = SimpleNamespace(
            as_of=datetime.combine(decision, time(17), tzinfo=TZ_SHANGHAI),
            research_mode=research_mode,
            dataset_snapshot_id=snapshot_id,
        )
        selection = repository.select([profit_series, revenue_series], clock)
        visible = {
            item.series_id: item
            for item in sorted(selection.selected, key=lambda r: r.observation_start)
        }
        profit = visible.get(profit_series)
        revenue = visible.get(revenue_series)
        view = market_feature_view(asset, benchmark, decision)
        if all(value is None for value in view.values()) and profit is None:
            skipped += 1
            continue
        features: dict[str, float | None] = {**view}
        known: list[datetime] = [datetime.combine(decision, time(17), tzinfo=TZ_SHANGHAI)]
        if profit is not None and profit.value is not None:
            features["profit_yoy"] = float(profit.value)
            known.append(profit.available_at)
        else:
            features["profit_yoy"] = None
        if revenue is not None and revenue.value is not None:
            features["revenue_yoy"] = float(revenue.value)
            known.append(revenue.available_at)
        else:
            features["revenue_yoy"] = None
        entry_index = next((i for i, day in enumerate(common_all) if day > decision), None)
        if entry_index is None or entry_index + horizon_days >= len(common_all):
            targets.append(
                TargetRow(
                    industry_id=industry_id,
                    decision_as_of=clock.as_of,
                    target_period=_add_months(date(decision.year, decision.month, 1), 3).strftime(
                        "%Y-%m"
                    ),
                    value=None,
                    label_available_at=None,
                    unscorable_reason="FORWARD_WINDOW_IMMATURE",
                )
            )
            continue
        exit_index = entry_index + horizon_days
        entry, exit_day = common_all[entry_index], common_all[exit_index]
        excess = asset.total_return(entry, exit_day) - benchmark.total_return(entry, exit_day)
        rows.append(
            FeatureRow(
                decision_as_of=clock.as_of,
                industry_id=industry_id,
                features=features,
                missing_features=[name for name, value in features.items() if value is None],
                max_ancestor_available_at=max(known),
            )
        )
        targets.append(
            TargetRow(
                industry_id=industry_id,
                decision_as_of=clock.as_of,
                target_period=_add_months(date(decision.year, decision.month, 1), 3).strftime(
                    "%Y-%m"
                ),
                value=1.0 if excess > 0 else 0.0,
                label_available_at=datetime.combine(exit_day, time(15), tzinfo=TZ_SHANGHAI),
            )
        )
        decision_dates.append(decision)
    if not rows:
        raise ValueError("T3 没有任何可评分样本（价格线或基本面覆盖不足）")
    frame = FeatureFrame(
        industry_id=industry_id,
        snapshot_id=f"{snapshot_id or 'none'}:t3-v1",
        feature_spec_version="t3-v1",
        schema_hash="t3:" + ",".join((*MARKET_FEATURES, "profit_yoy", "revenue_yoy")),
        research_mode=research_mode,
        built_at=datetime.now(TZ_SHANGHAI),
        rows=rows,
    )
    spec = t3_target_spec(industry_id)
    return DatasetBuild(
        features=frame,
        targets=TargetFrame(spec=spec, rows=targets),
        decision_dates=tuple(decision_dates),
        skipped=skipped,
    )


def advanced_gate(built: DatasetBuild) -> dict[str, Any]:
    """高级模型预检：训练样本与类别分布是否达到门槛（与 registry 一致）。"""

    scorable = [row for row in built.targets.rows if row.value is not None]
    ones = sum(1 for row in scorable if row.value == 1.0)
    zeros = len(scorable) - ones
    eligible = len(scorable) >= ADVANCED_MIN_ROWS and min(ones, zeros) >= 10
    return {
        "eligible": eligible,
        "scorable_rows": len(scorable),
        "class_one": ones,
        "class_zero": zeros,
        "threshold": f"样本≥{ADVANCED_MIN_ROWS} 且每类≥10",
    }


def grid_for(built: DatasetBuild, *, include_advanced: bool = True) -> dict[str, Any]:
    """基础网格恒定；高级模型只在门槛满足时加入对照。"""
    grid = dict(BASELINE_GRID)
    gate = advanced_gate(built)
    if include_advanced and gate["eligible"]:
        grid |= ADVANCED_GRID
    return grid


def experiment_spec_for(
    built: DatasetBuild,
    *,
    experiment_id: str,
    feature_list: list[str],
    train_end: date,
    outer_start: date,
    outer_end: date,
) -> Any:
    spec = make_experiment_spec(
        experiment_id=experiment_id,
        features=built.features,
        targets=built.targets,
        train_end=train_end,
        outer_start=outer_start,
        outer_end=outer_end,
        feature_list=feature_list,
    )
    horizon = built.targets.spec.horizon_months
    return spec.model_copy(
        update={
            "target_definition": (
                f"{built.targets.spec.target_id}@{built.targets.spec.definition_version}"
            ),
            "model_grid": grid_for(built),
            "pit_mode": built.features.research_mode,
            "outer_splits": [
                split.model_copy(update={"purge_months": horizon}) for split in spec.outer_splits
            ],
            "inner_splits": [
                split.model_copy(update={"purge_months": horizon}) for split in spec.inner_splits
            ],
        }
    )


def summarize_report(
    target_id: str,
    question: str,
    horizon: str,
    report: Any,
    gate: dict[str, Any],
    failures_extra: list[str] | None = None,
) -> dict[str, Any]:
    """把一次实验收成验收记录：问题、基准、预测期、比较、失败清单。"""
    comparison = report.comparison
    models = []
    for model_id, payload in (comparison.get("models") or {}).items():
        overall = payload.get("overall") or {}
        models.append(
            {
                "model": model_id,
                "role": (
                    "baseline"
                    if model_id in ("M00_LAST", "M01_SEASONAL", "M02_AR")
                    else "regularized"
                    if model_id in ("M03_LOGIT", "M04_ELASTIC")
                    else "advanced"
                ),
                "brier": overall.get("brier"),
                "log_loss": overall.get("log_loss"),
                "roc_auc": overall.get("roc_auc"),
                "n": overall.get("n"),
                "relative_brier_improvement": payload.get("relative_brier_improvement"),
                "screening_candidate": payload.get("screening_candidate"),
                "calibration": payload.get("calibration_status"),
            }
        )
    return {
        "target": target_id,
        "question": question,
        "horizon": horizon,
        "benchmark": "M00_LAST（上期值延续）",
        "oos_only": "比较只发生在外折测试段（未参与任何训练的时间段）",
        "comparison_status": comparison.get("status"),
        "common_oos_count": comparison.get("common_oos_count"),
        "strongest_baseline": comparison.get("strongest_baseline"),
        "advanced_gate": gate,
        "models": models,
        "failures": list(report.failures) + [{"reason": item} for item in (failures_extra or [])],
        "coverage": comparison.get("target_coverage"),
        "probability_meaning": (
            "T1/T2 是经济指标方向概率，不是股票上涨概率；T3 是相对收益方向概率，"
            "不是收益幅度。全部结论上限为研究候选，校准状态为未校准。"
        ),
    }


def format_baseline_report(payload: dict[str, Any]) -> str:
    """渲染三目标验收报告（纯文本，可粘贴进验收记录）。"""
    lines = [
        f"阶段 4 基线验收 {payload['ran_at']} · 快照 {payload['snapshot_id']}",
        f"特征口径：{payload['feature_note']}",
        "",
    ]
    for item in payload["targets"]:
        gate = item["advanced_gate"]
        lines.append(
            f"## {item['target']} · {item['name']}"
            f"{' — ' + item.get('note', '') if item.get('note') else ''}"
        )
        if item.get("status") == "failed":
            lines.append(f"  失败记录：{item.get('reason')}")
            lines.append("")
            continue
        lines.append(f"  问题：{item['question']}")
        lines.append(f"  预测期：{item['horizon']} · 基准 {item['benchmark']} · {item['oos_only']}")
        gate_note = (
            "满足，已加入对照" if gate["eligible"] else "不满足，未加入"
        )
        lines.append(
            f"  高级模型门槛：{gate_note}"
            f"（可评分 {gate['scorable_rows']}，正类 {gate['class_one']}，"
            f"负类 {gate['class_zero']}）"
        )
        for model in item["models"]:
            lines.append(
                f"  {model['model']:<14} {model['role']:<12} brier={_fmt(model['brier'])} "
                f"logloss={_fmt(model['log_loss'])} auc={_fmt(model['roc_auc'])} n={model['n']} "
                f"相对基准提升={_fmt(model['relative_brier_improvement'])} "
                f"筛选候选={'是' if model['screening_candidate'] else '否'}"
            )
        for failure in item["failures"]:
            lines.append(f"  失败：{failure}")
        lines.append(f"  概率含义：{item['probability_meaning']}")
        lines.append("")
    lines.append("结论：所有状态以 NO_EDGE / 筛选候选为上限；没有样本外证据就不能宣称优势。")
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    return "—" if value is None else f"{value:.4f}"


__all__ = [
    "ADVANCED_MIN_ROWS",
    "BASELINE_GRID",
    "MARKET_FEATURES",
    "advanced_gate",
    "attach_market_features",
    "build_relative_return_frames",
    "experiment_spec_for",
    "format_baseline_report",
    "grid_for",
    "market_feature_view",
    "price_lines",
    "summarize_report",
    "t3_target_spec",
]
