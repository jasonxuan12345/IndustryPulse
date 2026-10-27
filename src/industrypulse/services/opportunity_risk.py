"""Deterministic research admission, sizing constraints and revocation checks."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, cast

from industrypulse.economy.price_check import adjacent_periods
from industrypulse.services import ledger
from industrypulse.services.chain_demo import EvidenceReplay
from industrypulse.services.research_forecast import price_features


def screen(
    bundle: dict[str, Any],
    spec: dict[str, Any],
    at: datetime,
    model: dict[str, Any],
    dividend_ok: bool,
    dividend_reason: str,
) -> dict[str, Any]:
    policy = bundle["policy"]
    replay = EvidenceReplay(bundle)
    selected = replay.select(at)
    profit = selected.get(spec["profit_id"], [])
    current, previous = (profit[-1] if profit else None), (profit[-2] if len(profit) > 1 else None)
    revenue = next(
        (
            r
            for r in reversed(selected.get(spec["revenue_id"], []))
            if current and r.observation_start == current.observation_start
        ),
        None,
    )
    cost = next(
        (
            r
            for r in reversed(selected.get(spec["cost_id"], []))
            if current and r.observation_start == current.observation_start
        ),
        None,
    )
    facts = [replay.fact(r) for r in (current, previous, revenue, cost) if r]
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, observed: Any, threshold: Any, kind: str = "admission") -> None:
        checks.append(
            {
                "check": name,
                "passed": bool(ok),
                "observed": observed,
                "required": threshold,
                "kind": kind,
            }
        )

    has_pair = bool(
        current
        and previous
        and current.value is not None
        and previous.value is not None
        and adjacent_periods(previous.observation_start, current.observation_start)
    )
    delta = (
        float(current.value - previous.value)
        if has_pair
        and current
        and previous
        and current.value is not None
        and previous.value is not None
        else None
    )
    rev = float(revenue.value) if revenue and revenue.value is not None else None
    spread = (
        rev - float(cost.value) if rev is not None and cost and cost.value is not None else None
    )
    check("相邻公布期利润改善", delta is not None and delta > 0, delta, ">0且期次相邻", "economic")
    check("同期营收增长", rev is not None and rev > 0, rev, ">0", "economic")
    age = (at.date() - current.observation_end).days if current else None
    check(
        "基本面所属期新鲜度",
        age is not None and 0 <= age <= policy["fundamental_max_age_days"],
        age,
        policy["fundamental_max_age_days"],
    )
    aid = spec.get("asset_id", "")
    check(
        "资产映射",
        bool(aid) and (spec.get("fit") == "对口" or not policy["require_exact_proxy"]),
        spec.get("fit"),
        "对口ETF",
    )
    bars = sorted(
        [b for b in bundle["bars"] if b["asset_id"] == aid], key=lambda b: b["trade_date"]
    )
    bid = policy["benchmark_id"]
    bench = {b["trade_date"]: b for b in bundle["bars"] if b["asset_id"] == bid}
    common = [b for b in bars if b["trade_date"] in bench]
    last = date.fromisoformat(bars[-1]["trade_date"]) if bars else None
    check(
        "报价新鲜度",
        last is not None and 0 <= (at.date() - last).days <= policy["price_max_age_days"],
        last.isoformat() if last else None,
        f"<={policy['price_max_age_days']}自然日",
    )
    check(
        "共同交易日覆盖",
        len(common) >= policy["minimum_common_bars"],
        len(common),
        policy["minimum_common_bars"],
    )
    recent = common[-20:]
    valid = (
        bool(recent)
        and len(recent) == 20
        and all(float(b["volume"]) > 0 and b["adj_method"] == "none" for b in recent)
    )
    average_volume = sum(float(b["volume"]) for b in recent) / len(recent) if recent else 0
    check("可交易原始行情", valid, len(recent), "20个正成交量、未复权交易日")
    check(
        "流动性",
        average_volume >= policy["minimum_average_volume"],
        average_volume,
        f"20日平均成交份数>={policy['minimum_average_volume']}；未提供委托规模，不能证明实际冲击成本",
    )
    features = price_features(bundle, aid, at) if aid else {}
    vol = features.get("volatility_20d")
    annualized = vol * math.sqrt(252) if vol is not None else None
    check(
        "波动风险",
        annualized is not None and annualized <= policy["max_annualized_volatility"],
        annualized,
        policy["max_annualized_volatility"],
    )
    relative = features.get("relative_20d")
    check(
        "价格确认", relative is not None and relative > 0, relative, "20交易日含现金分红超额收益>0"
    )
    check(
        "分红独立核对",
        dividend_ok or not policy["require_independent_dividend_check"],
        dividend_reason,
        "两来源记录一致、原件完整、核对未过期",
    )
    from industrypulse.contracts.domain import CorporateAction, MarketBar
    from industrypulse.services.etf_audit import find_barriers

    acts = [CorporateAction.model_validate(a) for a in bundle["actions"] if a["asset_id"] == aid]
    barriers = find_barriers([MarketBar.model_validate(b) for b in bars[-61:]], acts)
    check("近期公司行动", not barriers, barriers, "60日窗口不跨越未处理公司行动")
    # Drawdown uses dividend-adjusted wealth and is withheld across a barrier.
    wealth, peak, dd, cash_paid = 1.0, 1.0, 0.0, 0.0
    base_close = (
        float(bars[-61]["close"]) if len(bars) >= 61 else float(bars[0]["close"]) if bars else 1.0
    )
    for a, b in zip(bars[-61:-1], bars[-60:], strict=False):
        paid = sum(
            float(x.distribution_per_unit)
            for x in acts
            if x.action_type == "cash_dividend"
            and a["trade_date"] < x.ex_date.isoformat() <= b["trade_date"]
        )
        cash_paid += paid
        wealth = (float(b["close"]) + cash_paid) / base_close
        peak = max(peak, wealth)
        dd = min(dd, wealth / peak - 1)
    check(
        "近期最大回撤",
        bool(bars) and not barriers and -dd <= policy["max_recent_drawdown"],
        dd if bars and not barriers else None,
        policy["max_recent_drawdown"],
    )
    # The current frozen model registry has no validated family. It cannot admit
    # investment sizing merely because one of several raw probabilities is high.
    edge = model.get("status") == "VALIDATED"
    check(
        "模型样本外准入",
        edge or not policy["require_model_edge"],
        model.get("status"),
        "完整强基线共同OOS、校准、稳定性和前瞻验证均已验收",
        "model",
    )
    economic = all(c["passed"] for c in checks if c["kind"] == "economic")
    admission = all(c["passed"] for c in checks)
    quality = all(c["passed"] for c in checks if c["kind"] == "admission")
    # Transparent evidence ranking, not expected returns or a probability.
    rank = (2 if delta is not None and delta > 0 else 0) + (2 if rev is not None and rev > 0 else 0)
    rank += int(spread is not None and spread > 0) + int(relative is not None and relative > 0)
    return {
        "industry_id": spec["industry_id"],
        "name": spec["name"],
        "asset_id": aid,
        "fit": spec.get("fit"),
        "as_of": at.isoformat(),
        "checks": checks,
        "facts": facts,
        "economic_signal": economic,
        "economic_observed": bool(has_pair and rev is not None),
        "data_risk_passed": quality,
        "admitted": admission,
        "tier": "CONDITIONAL_CANDIDATE"
        if admission
        else "RESEARCH_CANDIDATE"
        if economic
        else "OBSERVE",
        "evidence_score": rank,
        "score_max": 6,
        "score_definition": (
            "利润改善2、营收正增长2、营收同比高于成本同比1、20日价格超额为正1；非收益预测"
        ),
        "profit_change_pp": delta,
        "revenue_yoy_pct": rev,
        "revenue_cost_spread_pp": spread,
        "relative_20d": relative,
        "annualized_volatility": annualized,
        "max_drawdown_60d": dd if bars and not barriers else None,
        "blocking_reasons": [c["check"] for c in checks if not c["passed"]],
        "target_weight": 0.0,
        "commission_bps": policy["commission_bps"],
        "slippage_bps": policy["slippage_bps"],
        "expected_net_return": None,
        "invalidation_conditions": [
            "相邻公布期利润同比不再改善",
            "同期营收同比<=0",
            "新反证推翻传导假设",
            f"报价超过{policy['price_max_age_days']}自然日或来源核验失效：暂停",
            f"原登记后费后亏损达到{policy['asset_loss_revoke']:.0%}：撤销",
            f"原登记后费后峰谷回撤达到{policy['asset_drawdown_revoke']:.0%}：撤销",
        ],
    }


def allocate(
    items: list[dict[str, Any]],
    policy: dict[str, Any],
    previous: dict[str, float],
    paused: bool = False,
) -> dict[str, Any]:
    """Apply single-asset, industry, total and turnover limits to shadow targets.

    Risk exits take precedence over a turnover cap; entry orders may be deferred.
    No execution or actual account positions are inferred from these weights.
    """
    targets: dict[str, float] = {}
    industries: dict[str, float] = {}
    exits = sum(
        weight
        for aid, weight in previous.items()
        if not any(i["asset_id"] == aid and i["admitted"] for i in items) or paused
    )
    remaining_turnover = max(0.0, policy["max_turnover"] - exits)
    for item in sorted(items, key=lambda i: (-i["evidence_score"], i["industry_id"])):
        aid, iid = item["asset_id"], item["industry_id"]
        if not item["admitted"] or paused or aid in targets:
            continue
        previous_weight = previous.get(aid, 0.0)
        cap = min(
            policy["single_asset_weight"],
            policy["industry_weight"] - industries.get(iid, 0),
            policy["total_weight"] - sum(targets.values()),
        )
        weight = max(0.0, min(cap, previous_weight + remaining_turnover))
        remaining_turnover -= max(0.0, weight - previous_weight)
        if weight:
            targets[aid] = weight
            industries[iid] = industries.get(iid, 0) + weight
        item["target_weight"] = weight
    turnover = sum(
        abs(targets.get(a, 0) - previous.get(a, 0)) for a in set(targets) | set(previous)
    )
    return {
        "weights": targets,
        "cash_weight": 1 - sum(targets.values()),
        "turnover": turnover,
        "exit_overrides_turnover": exits > policy["max_turnover"],
        "paused": paused,
        "scope": "研究模拟目标；未连接交易账户，不代表实际持仓或成交",
        "cost_stress_roundtrip_bps": 2
        * (policy["stress_commission_bps"] + policy["stress_slippage_bps"]),
    }


def shadow_account(
    market: Any, state: dict[str, Any] | None, now: datetime, policy: dict[str, Any]
) -> dict[str, Any]:
    """Advance a normalized research account; orders fill on a later day's open.

    No account balances are requested. Split handling and missing held-asset bars
    stop valuation instead of inventing an execution. A drawdown latch persists.
    """
    from datetime import time, timedelta

    state = ledger._copy(
        state
        or {
            "cash": 1.0,
            "units": {},
            "nav": 1.0,
            "peak": 1.0,
            "max_drawdown": 0.0,
            "paused": False,
            "last_day": now.date().isoformat(),
            "history": [],
        }
    )
    cost = (policy["commission_bps"] + policy["slippage_bps"]) / 10000
    end = now.date() if now.time() >= time(15) else now.date() - timedelta(days=1)
    days = [
        b.trade_date
        for b in market.bars(policy["benchmark_id"], end=end)
        if b.trade_date > date.fromisoformat(state["last_day"])
    ]
    for day in days:
        state.pop("error", None)
        order = state.get("pending")
        executable = order and day > date.fromisoformat(order["signal_day"])
        desired: dict[str, float] = order["weights"] if executable and order else {}
        aids = set(state["units"]) | (set(desired) if desired else set())
        quotes = {a: next((b for b in market.bars(a, start=day, end=day)), None) for a in aids}
        if any(b is None or b.volume <= 0 or b.adj_method != "none" for b in quotes.values()):
            state["error"] = f"{day} 持有或待成交资产缺少可执行原始行情，暂停估值"
            break
        quotes = cast(dict[str, Any], {a: b for a, b in quotes.items() if b is not None})
        from industrypulse.services.etf_audit import find_barriers

        if any(find_barriers(market.bars(aid, end=day)[-2:], market.actions(aid)) for aid in aids):
            state["error"] = f"{day} 行情跳点可能涉及未处理公司行动，暂停估值及新增敞口"
            break
        if any(
            a.action_type != "cash_dividend" and a.ex_date == day
            for aid in state["units"]
            for a in market.actions(aid)
        ):
            state["error"] = f"{day} 持有资产发生未实现的份额行动，暂停估值"
            break
        # Entitlement uses holdings before the day's open.
        state["cash"] += sum(
            q * float(a.distribution_per_unit)
            for aid, q in state["units"].items()
            for a in market.actions(aid)
            if a.action_type == "cash_dividend" and a.ex_date == day
        )
        if executable:
            open_nav = state["cash"] + sum(
                q * float(quotes[a].open) for a, q in state["units"].items()
            )
            changes = {
                a: desired.get(a, 0) * open_nav / float(quotes[a].open) - state["units"].get(a, 0)
                for a in aids
            }
            for a in sorted(aids, key=lambda a: changes[a]):  # release cash before entries
                delta = changes[a]
                price = float(quotes[a].open)
                if delta > 0:
                    delta = min(delta, state["cash"] / (price * (1 + cost)))
                state["cash"] -= delta * price + abs(delta) * price * cost
                state["units"][a] = state["units"].get(a, 0) + delta
                if state["units"][a] < 1e-12:
                    state["units"].pop(a, None)
            state.pop("pending", None)
        nav = state["cash"] + sum(
            q * float(quotes[a].close) * (1 - cost) for a, q in state["units"].items()
        )
        state["peak"] = max(state["peak"], nav)
        dd = nav / state["peak"] - 1
        state.update(nav=nav, max_drawdown=min(state["max_drawdown"], dd), last_day=day.isoformat())
        state["history"].append({"day": day.isoformat(), "nav": nav, "drawdown": dd})
        if -dd >= policy["portfolio_drawdown_pause"]:
            state.update(paused=True, pause_reason="组合费后峰谷回撤触发暂停线；待人工复核后恢复")
            state["pending"] = {"signal_day": day.isoformat(), "weights": {}}
    return state


def review_registered(
    service: Any, items: list[dict[str, Any]], now: datetime, policy: dict[str, Any]
) -> dict[str, Any]:
    path = service.root / "data/ledger/recommendations.jsonl"
    current = ledger.latest_versions(path)
    by_asset = {i["asset_id"]: i for i in items}
    actions = []
    for record in current.values():
        if record["category"] != "asset_candidate" or record["status"] in ledger.TERMINAL | {
            "withdrawn"
        }:
            continue
        item = by_asset.get(record["asset"])
        metrics = record.get("metrics") or {}
        reasons = []
        if metrics.get("net_return_pct", 0) <= -100 * policy["asset_loss_revoke"]:
            reasons.append("原登记窗口费后亏损触发撤销线")
        if metrics.get("max_drawdown_pct", 0) <= -100 * policy["asset_drawdown_revoke"]:
            reasons.append("原登记窗口最大回撤触发撤销线")
        if item and item.get("economic_observed") and not item["economic_signal"]:
            reasons.append("已发布基本面不满足利润改善及营收正增长")
        if reasons:
            ledger.mark(
                record["recommendation_id"],
                "withdrawn",
                reason="；".join(reasons),
                now=now,
                path=path,
            )
            actions.append(
                {
                    "id": record["recommendation_id"],
                    "asset": record["asset"],
                    "action": "withdrawn",
                    "reasons": reasons,
                }
            )
        elif item and not item["data_risk_passed"]:
            actions.append(
                {
                    "id": record["recommendation_id"],
                    "action": "suspend_new_exposure",
                    "reasons": item["blocking_reasons"],
                }
            )
    return {"actions": actions, "survivorship": "撤销后继续核对原定3/6月窗口，保留全部原始判断"}
