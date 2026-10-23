"""宏观状态与行业改善的确定性规则。

只用已经可见的发布值。缺的维度留空，不用中性分填补。
这里的状态是研究标签，不是官方经济周期，也不是投资信号。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Literal

Regime = Literal["EXPANSION", "SLOWDOWN", "CONTRACTION", "RECOVERY", "UNCERTAIN"]
Cycle = Literal["IMPROVING", "SOFTENING", "WEAK", "STABILIZING", "UNCHANGED", "UNKNOWN"]

REGIME_LABELS = {
    "EXPANSION": "扩张",
    "SLOWDOWN": "扩张放缓",
    "CONTRACTION": "收缩",
    "RECOVERY": "收缩中改善",
    "UNCERTAIN": "证据不足",
}
CYCLE_LABELS = {
    "IMPROVING": "改善",
    "SOFTENING": "高位放缓",
    "WEAK": "偏弱",
    "STABILIZING": "低位回升",
    "UNCHANGED": "持平",
    "UNKNOWN": "证据不足",
}

# 国家统计局口径：制造业 PMI = 新订单 30% + 生产 25% + 从业人员 20%
# + (100 - 供应商配送时间) 15% + 原材料库存 10%。配送时间高于 50 表示交货加快，
# 合成时是逆指数。状态使用已经发布的总体指数，不在本地重算季调结果。
# 出处：https://www.stats.gov.cn/hd/cjwtjd/202302/t20230207_1902267.html
FORWARD_BREADTH = ("new_orders", "production", "employment", "raw_inventory")

MISSING_DIMENSIONS = (
    ("growth", "增长", "研究库没有全国 GDP 或全国工业增加值的发布版本"),
    ("inflation", "价格", "CPI、PPI 只有展示序列，没有发布时点，不进入状态"),
    ("credit", "信用", "没有社融、信贷或货币供应量的发布版本"),
    ("external", "外需", "没有出口或海外 PMI 的发布版本"),
    ("property", "房地产", "没有房地产开发投资、销售或新开工的发布版本"),
)


def classify_regime(level: float | None, previous: float | None) -> Regime:
    """用已发布的制造业 PMI 总体及其相对上一期的变动命名研究状态。"""
    if level is None or previous is None or level == 50:
        return "UNCERTAIN"
    change = level - previous
    if level > 50 and change >= 0:
        return "EXPANSION"
    if level > 50:
        return "SLOWDOWN"
    if change > 0:
        return "RECOVERY"
    return "CONTRACTION"


def acceleration(values: Sequence[float]) -> float | None:
    """总体指数最近一次变动减去再前一次变动。不足三期则为空。"""
    if len(values) < 3:
        return None
    return (values[-1] - values[-2]) - (values[-2] - values[-3])


def expansion_breadth(components: dict[str, float]) -> float | None:
    """四项正向分项里高于 50 的比例。缺任何一项就不计算。配送时间不计入。"""
    if any(key not in components for key in FORWARD_BREADTH):
        return None
    return sum(components[key] > 50 for key in FORWARD_BREADTH) / len(FORWARD_BREADTH)


def cyclical_state(profit_yoy: float | None, change: float | None) -> Cycle:
    if profit_yoy is None or change is None:
        return "UNKNOWN"
    if change == 0:
        return "UNCHANGED"
    if change > 0 and profit_yoy > 0:
        return "IMPROVING"
    if change > 0:
        return "STABILIZING"
    if profit_yoy > 0:
        return "SOFTENING"
    return "WEAK"


def growth_hypothesis(
    *,
    industry_id: str,
    name: str,
    period: date | None,
    profit_yoy: float | None,
    profit_change: float | None,
    revenue_yoy: float | None,
    cost_yoy: float | None,
    source_urls: list[str],
) -> dict[str, object] | None:
    """只有利润累计同比上升、且收入累计同比仍为正时，才形成待验证假设。"""
    if period is None or profit_yoy is None or profit_change is None or revenue_yoy is None:
        return None
    if not (profit_change > 0 and revenue_yoy > 0):
        return None
    supporting = [
        f"利润累计同比为 {profit_yoy:g}%，较上一公布期高 {profit_change:g} 个百分点",
        f"营业收入累计同比为 {revenue_yoy:g}%，仍为正",
    ]
    contradicting: list[str] = []
    if cost_yoy is None:
        contradicting.append("营业成本累计同比不可见，无法区分收入改善和成本下降")
    elif cost_yoy < revenue_yoy:
        supporting.append(f"营业成本累计同比为 {cost_yoy:g}%，低于收入累计同比")
    else:
        contradicting.append(
            f"营业成本累计同比为 {cost_yoy:g}%，不低于收入累计同比 {revenue_yoy:g}%。"
            "利润改善不一定来自需求"
        )
    return {
        "hypothesis_id": f"gh-{industry_id.lower()}-profit-yoy-{period.isoformat()}",
        "industry_id": industry_id,
        "name": name,
        "theme": f"{name}利润累计同比改善",
        "horizon_months": 6,
        "status": "RESEARCH_CANDIDATE",
        "mechanism": (
            "已公布的利润累计同比高于上一期，且营业收入累计同比仍为正。"
            "比较的是累计同比，不是当月新增值，也不能外推为资产收益。"
        ),
        "supporting_evidence": supporting,
        "contradicting_evidence": contradicting,
        "watch_indicators": [
            "下一期利润累计同比",
            "下一期营业收入累计同比",
            "下一期营业成本累计同比",
        ],
        "invalidation_conditions": [
            f"下一期利润累计同比低于当前可见值 {profit_yoy:g}%",
            "营业收入累计同比不高于 0",
        ],
        "source_urls": source_urls,
        "evidence_type": "HYPOTHESIS",
    }
