"""对口 ETF 价格线审计：分红覆盖、疑似份额拆并、代理范围（阶段 1 · 可信基线）。

基线要求："核对 ETF 分红、拆并份额、资产代理范围；回放时排除未来事件。"

收益口径（price_check）：不复权收盘价加回现金分红（不再投资）、零费用。
份额拆分/合并会机械地改写不复权价格，而 corporate_action 里目前只有
cash_dividend。本模块用价格缺口找疑似拆并：相邻交易日涨跌超过拆并阈值
（默认 20%——A 股 ETF 涨跌停带最高 ±20%，缺口更大就不是正常行情），
且当日已登记的公司行动解释不了这个幅度，就列为疑似事件。

疑似事件不回填复权比例：比例要等基金公告确认后由人登记成
share_split / share_merge 行动。在此之前，重放把疑似日当障碍日，跨越的
收益窗口整段剔除。

只读仓储；输出审计结论，不改任何数据。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from industrypulse.contracts.domain import CorporateAction, MarketBar
from industrypulse.repositories import DuckDBMarketRepository

#: 涨跌超过该比例视为疑似拆并；A 股 ETF 涨跌停带最高 ±20%（创业板/科创板）。
SPLIT_GAP_THRESHOLD = 0.20
#: 单次现金分红的合理收益率上限。超过它说明"分红"记录本身可疑
#: （常见于份额合并被第三方页面记成大额分红），不能当作已解释。
MAX_DIVIDEND_YIELD = 0.15


def find_barriers(
    bars: Sequence[MarketBar],
    actions: Sequence[CorporateAction] = (),
    *,
    threshold: float = SPLIT_GAP_THRESHOLD,
) -> list[dict[str, Any]]:
    """相邻交易日缺口超过阈值、且已登记行动解释不了的日期。

    分红只能把价格往下压每股分红那么多；只有当某条已登记行动的每份金额
    能解释缺口的一半以上、且隐含收益率不超过 :data:`MAX_DIVIDEND_YIELD`，
    才认为"已解释"。
    """
    ordered = sorted(bars, key=lambda bar: bar.trade_date)
    recorded = {action.ex_date: action for action in actions}
    found: list[dict[str, Any]] = []
    previous: MarketBar | None = None
    for bar in ordered:
        if previous is not None and previous.close:
            change = float(bar.close) / float(previous.close) - 1
            if abs(change) > threshold:
                action = recorded.get(bar.trade_date)
                yield_rate = (
                    float(action.distribution_per_unit) / float(previous.close)
                    if action is not None and previous.close
                    else 0.0
                )
                explained = abs(change) / 2 <= yield_rate <= MAX_DIVIDEND_YIELD
                if not explained:
                    found.append(
                        {
                            "day": bar.trade_date.isoformat(),
                            "previous_day": previous.trade_date.isoformat(),
                            "change_pct": round(change * 100, 1),
                            "recorded_action": None if action is None else action.action_type,
                        }
                    )
        previous = bar
    return found


def audit_asset(
    market: DuckDBMarketRepository,
    asset_id: str,
    *,
    as_of: datetime,
    threshold: float = SPLIT_GAP_THRESHOLD,
) -> dict[str, Any]:
    """一条价格线的覆盖、分红与疑似拆并清单；严格排除 as_of 之后的事件。"""
    day = as_of.date()
    bars = market.bars(asset_id, end=day)
    actions = [action for action in market.actions(asset_id) if action.ex_date <= day]
    dividends = [
        (action.ex_date, float(action.distribution_per_unit))
        for action in actions
        if action.action_type == "cash_dividend"
    ]
    unknown_types = sorted(
        {action.action_type for action in actions if action.action_type != "cash_dividend"}
    )
    return {
        "asset_id": asset_id,
        "bars": len(bars),
        "first_day": bars[0].trade_date.isoformat() if bars else None,
        "last_day": bars[-1].trade_date.isoformat() if bars else None,
        "dividends": len(dividends),
        "last_dividend": max((ex_day for ex_day, _ in dividends), default=None),
        "non_dividend_action_types": unknown_types,
        "suspected_splits": find_barriers(bars, actions, threshold=threshold),
        "dividend_record_empty": not dividends,
    }


def audit_price_lines(
    config_path: Path,
    market: DuckDBMarketRepository,
    *,
    as_of: datetime,
    threshold: float = SPLIT_GAP_THRESHOLD,
) -> dict[str, Any]:
    """按 industry_price_lines.yaml 逐条审计基准与行业代理，并检查未来事件排除。"""
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assets: dict[str, dict[str, Any]] = {}

    def one(asset_id: str) -> dict[str, Any]:
        if asset_id not in assets:
            assets[asset_id] = audit_asset(market, asset_id, as_of=as_of, threshold=threshold)
        return assets[asset_id]

    benchmark = one(str(config["benchmark"]["asset_id"]))
    industries = {
        industry_id: {**spec, "audit": one(str(spec["asset_id"]))}
        for industry_id, spec in (config.get("industries") or {}).items()
    }
    future_actions = []
    day = as_of.date()
    for asset_id in assets:
        for action in market.actions(asset_id):
            if action.ex_date > day:
                future_actions.append({"asset_id": asset_id, "ex_date": action.ex_date.isoformat()})
    return {
        "as_of": as_of.isoformat(),
        "threshold_pct": round(threshold * 100, 1),
        "benchmark": benchmark,
        "industries": industries,
        "future_actions_excluded": future_actions,
        "return_convention": "不复权收盘价 + 期间现金分红加回（不再投资），零费用",
    }


def format_audit(payload: dict[str, Any]) -> str:
    """渲染成可粘贴进验收记录的纯文本。"""
    lines = [
        f"ETF 价格线审计 as_of={payload['as_of']} 阈值=±{payload['threshold_pct']}%",
        f"收益口径：{payload['return_convention']}",
        "",
        f"{'资产':<12}{'日线':>6}{'起':>12}{'止':>12}{'分红':>5}{'疑似拆并':>6}",
        "-" * 60,
    ]
    for section in (payload["benchmark"], *payload["industries"].values()):
        audit = section["audit"] if "audit" in section else section
        lines.append(
            f"{audit['asset_id']:<12}{audit['bars']:>6}{audit['first_day'] or '—':>12}"
            f"{audit['last_day'] or '—':>12}{audit['dividends']:>5}"
            f"{len(audit['suspected_splits']):>6}"
        )
    suspects = [
        (asset_id, event)
        for asset_id, audit in ((key, value) for key, value in _iter_sections(payload))
        for event in audit["suspected_splits"]
    ]
    if suspects:
        lines += ["", "疑似份额拆并（缺口超过阈值且无已登记行动解释；跨越窗口已剔除）："]
        for asset_id, event in suspects:
            lines.append(
                f"  {asset_id}: {event['previous_day']} -> {event['day']}"
                f"（{event['change_pct']:+}%）"
            )
    empty = [
        asset_id for asset_id, audit in _iter_sections(payload) if audit["dividend_record_empty"]
    ]
    if empty:
        lines += ["", "没有分红记录的价格线（需人工核对天天基金 F10 是否真的从未分红）："]
        lines.append("  " + "、".join(empty))
    if payload["future_actions_excluded"]:
        lines += [
            "",
            f"回放已排除 as_of 之后的公司行动 {len(payload['future_actions_excluded'])} 条。",
        ]
    return "\n".join(lines)


def _iter_sections(payload: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    yield str(payload["benchmark"]["asset_id"]), payload["benchmark"]
    for spec in payload["industries"].values():
        yield str(spec["asset_id"]), spec["audit"]


__all__ = [
    "SPLIT_GAP_THRESHOLD",
    "audit_asset",
    "audit_price_lines",
    "find_barriers",
    "format_audit",
]
