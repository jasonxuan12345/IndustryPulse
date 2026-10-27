"""可冻结、离线重放的产业链示范：领先事件 → 基本面 → ETF → 费后交易。

规则为回顾性工程示范，不将今天写出的 AI 假设伪装成历史信号。
输入包含原始版本、行情、公司行动、映射与策略；重放不访问网络或数据库。
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import yaml

from industrypulse.contracts import TZ_SHANGHAI
from industrypulse.contracts.context import ResearchMode
from industrypulse.contracts.domain import AssetMaster, CorporateAction, MarketBar
from industrypulse.contracts.observation import ObservationVintage
from industrypulse.contracts.pit import effective_available_at, select_vintages_as_of
from industrypulse.economy.price_check import (
    adjacent_periods,
    benjamini_hochberg,
    compare,
)
from industrypulse.investment import BacktestEngine, CostModel, PortfolioConstraints
from industrypulse.investment.market import HistoricalUniverse, MarketDataset
from industrypulse.repositories import DuckDBMarketRepository, DuckDBObservationRepository
from industrypulse.services.etf_audit import find_barriers

MODE = ResearchMode.RECONSTRUCTED_PIT


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def freeze_inputs(
    repository: DuckDBObservationRepository,
    market: DuckDBMarketRepository,
    *,
    root: Path,
    snapshot_id: str,
    as_of: datetime,
    start: date,
    config_path: Path | None = None,
) -> dict[str, Any]:
    if as_of.tzinfo is None:
        raise ValueError("as_of 必须带时区")
    config = yaml.safe_load(
        (config_path or root / "config/research/chain-demo.yaml").read_text(encoding="utf-8")
    )
    mapping = yaml.safe_load(
        (root / "config/market/industry_price_lines.yaml").read_text(encoding="utf-8")
    )
    series = [config["leading_series"]]
    for spec in config["industries"].values():
        series.extend(
            f"{spec['prefix']}.{suffix}_ytd_yoy" for suffix in ("profit", "revenue", "cost")
        )
    rows = repository.all_vintages(series, snapshot_id=snapshot_id)
    # 保留截止时点前实际可用的全部修订（含缺失），不只保留最后版本。
    eligible: list[ObservationVintage] = []
    for sid in series:
        selection = select_vintages_as_of(
            [row for row in rows if row.series_id == sid],
            as_of=as_of,
            mode=MODE,
            trading_days=repository.trading_days,
            conservative_lag_days=repository.series_lag_days.get(
                sid, repository.conservative_lag_days
            ),
        )
        rejected = {(r.observation_start, r.vintage_id) for r in selection.skipped}
        eligible.extend(
            row
            for row in rows
            if row.series_id == sid and (row.observation_start, row.vintage_id) not in rejected
        )
    artifact_ids = sorted({row.artifact_id for row in eligible if row.artifact_id})
    with repository.database.transaction() as connection:
        archived_sources = (
            [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT payload FROM raw_artifact WHERE artifact_id IN ("
                    + ",".join("?" for _ in artifact_ids)
                    + ") ORDER BY artifact_id",
                    artifact_ids,
                ).fetchall()
            ]
            if artifact_ids
            else []
        )
    specs = {iid: mapping["industries"][iid] for iid in config["industries"]}
    asset_ids = sorted(
        {spec["asset_id"] for spec in specs.values()} | {mapping["benchmark"]["asset_id"]}
    )
    local_day = as_of.astimezone(TZ_SHANGHAI).date()
    # 当天未收盘时不能使用日线；盘中 as_of 仅截止前一天。
    from datetime import timedelta

    price_end = (
        local_day
        if as_of.astimezone(TZ_SHANGHAI).time() >= time(15)
        else local_day - timedelta(days=1)
    )
    hypotheses = []
    path = root / "data/research/hypotheses.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("chain") != config["name"]:
                continue
            created = record.get("created_at")
            if created and datetime.fromisoformat(created) <= as_of:
                hypotheses.append(record)
    return {
        "schema": "chain-demo-input-v1",
        "as_of": as_of.isoformat(),
        "start": start.isoformat(),
        "snapshot_id": snapshot_id,
        "config": config,
        "mapping": specs,
        "benchmark": mapping["benchmark"],
        "policy": {
            "mode": MODE.value,
            "trading_days": [day.isoformat() for day in repository.trading_days],
            "lag_days": repository.conservative_lag_days,
            "series_lag_days": dict(repository.series_lag_days),
        },
        "vintages": [row.model_dump(mode="json") for row in eligible],
        "archived_sources": archived_sources,
        "assets": [market.asset(aid).model_dump(mode="json") for aid in asset_ids],
        "bars": [
            bar.model_dump(mode="json")
            for aid in asset_ids
            for bar in market.bars(aid, end=price_end)
        ],
        "actions": [
            action.model_dump(mode="json")
            for aid in asset_ids
            for action in market.actions(aid)
            if action.ex_date <= price_end
        ],
        "hypotheses": hypotheses,
        "code_sha256": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in (
                "src/industrypulse/services/chain_demo.py",
                "src/industrypulse/investment/backtest.py",
                "src/industrypulse/contracts/pit.py",
                "src/industrypulse/repositories/store.py",
                "src/industrypulse/economy/price_check.py",
                "src/industrypulse/services/etf_audit.py",
                "src/industrypulse/investment/market.py",
                "src/industrypulse/services/chain_demo_report.py",
            )
        },
    }


class EvidenceReplay:
    def __init__(self, inputs: dict[str, Any]) -> None:
        self.rows = [ObservationVintage.model_validate(row) for row in inputs["vintages"]]
        policy = inputs["policy"]
        self.calendar = [date.fromisoformat(day) for day in policy["trading_days"]]
        self.lag = int(policy["lag_days"])
        self.series_lags = policy["series_lag_days"]

    def available(self, row: ObservationVintage) -> datetime:
        return effective_available_at(
            row,
            MODE,
            trading_days=self.calendar,
            conservative_lag_days=self.series_lags.get(row.series_id, self.lag),
        )

    def select(self, at: datetime) -> dict[str, list[ObservationVintage]]:
        grouped: dict[str, list[ObservationVintage]] = {}
        for sid in sorted({r.series_id for r in self.rows}):
            grouped[sid] = select_vintages_as_of(
                [r for r in self.rows if r.series_id == sid],
                as_of=at,
                mode=MODE,
                trading_days=self.calendar,
                conservative_lag_days=self.series_lags.get(sid, self.lag),
            ).selected
        return grouped

    def fact(self, row: ObservationVintage | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "series_id": row.series_id,
            "period": row.observation_start.isoformat(),
            "vintage_id": row.vintage_id,
            "value": None if row.value is None else float(row.value),
            "available_at": self.available(row).isoformat(),
            "source_url": row.source_url,
            "artifact_id": row.artifact_id,
            "release_evidence": row.release_evidence.value,
            "value_evidence": row.value_evidence.value,
        }


def _latest(rows: list[ObservationVintage]) -> ObservationVintage | None:
    return max(rows, key=lambda row: row.observation_start) if rows else None


def _value(row: ObservationVintage | None) -> float | None:
    return None if row is None or row.value is None else float(row.value)


def _pair(
    rows: list[ObservationVintage],
) -> tuple[ObservationVintage | None, ObservationVintage | None]:
    latest = _latest(rows)
    earlier = [r for r in rows if latest and r.observation_start < latest.observation_start]
    previous = _latest(earlier)
    return latest, previous


def leading_state(
    rows: list[ObservationVintage], config: dict[str, Any], day: date
) -> tuple[bool, list[str], ObservationVintage | None, ObservationVintage | None]:
    now, previous = _pair(rows)
    reasons = []
    if now is None or previous is None or now.value is None or previous.value is None:
        reasons.append("领先指标当期或上期缺失")
    elif (
        now.observation_start.year - previous.observation_start.year
    ) * 12 + now.observation_start.month - previous.observation_start.month != 1:
        reasons.append("领先指标不相邻，禁止跨缺月比较")
    else:
        if float(now.value) < config["leading_floor"]:
            reasons.append("领先指标低于扩张线")
        if float(now.value - previous.value) < config["leading_delta"]:
            reasons.append("领先指标增幅未达固定阈值")
    if now and (day - now.observation_start).days > config["leading_max_age_days"]:
        reasons.append("领先指标过期")
    return not reasons, reasons, now, previous


def screen_asset(
    spec: dict[str, Any],
    asset: AssetMaster,
    bars: list[MarketBar],
    day: date,
    config: dict[str, Any],
) -> list[str]:
    reasons = []
    if spec["fit"] != "对口":
        reasons.append(f"行业代理匹配为{spec['fit']}，交易示范只纳入对口 ETF")
    if not asset.is_listed_on(day) or asset.instrument_type.value != "ETF":
        reasons.append("尚未上市、已退市或不是 ETF")
    past = [bar for bar in bars if bar.trade_date <= day]
    window = past[-int(config["min_history_days"]) :]
    if len(window) < config["min_history_days"]:
        reasons.append("历史成交样本不足")
    if not past or past[-1].trade_date != day or any(bar.volume <= 0 for bar in window):
        reasons.append("当日无报价或观察窗含零成交量")
    if any(bar.adj_method != "none" for bar in window):
        reasons.append("复权口径不是原始价")
    return reasons


def decision(
    replay: EvidenceReplay,
    inputs: dict[str, Any],
    day: date,
    assets: dict[str, AssetMaster],
    bars: dict[str, list[MarketBar]],
) -> dict[str, Any]:
    config = inputs["config"]
    at = min(datetime.combine(day, time(17), TZ_SHANGHAI), datetime.fromisoformat(inputs["as_of"]))
    selected = replay.select(at)
    on, reasons, pmi, prior = leading_state(selected.get(config["leading_series"], []), config, day)
    items = []
    for iid, industry in config["industries"].items():
        prefix = industry["prefix"]
        profit, previous = _pair(selected.get(f"{prefix}.profit_ytd_yoy", []))
        revenue = next(
            (
                r
                for r in selected.get(f"{prefix}.revenue_ytd_yoy", [])
                if profit is not None and r.observation_start == profit.observation_start
            ),
            None,
        )
        cost = next(
            (
                r
                for r in selected.get(f"{prefix}.cost_ytd_yoy", [])
                if profit is not None and r.observation_start == profit.observation_start
            ),
            None,
        )
        blocked = list(reasons)
        if (
            profit is None
            or previous is None
            or not adjacent_periods(previous.observation_start, profit.observation_start)
        ):
            blocked.append("利润相邻期不足")
        elif profit.value is None or previous.value is None or profit.value <= previous.value:
            blocked.append("利润累计同比未改善或缺失")
        if revenue is None or revenue.value is None or revenue.value <= 0:
            blocked.append("同期营收累计同比非正或缺失")
        if profit and (day - profit.observation_start).days > config["fundamental_max_age_days"]:
            blocked.append("基本面数据过期")
        spec = inputs["mapping"][iid]
        aid = spec["asset_id"]
        blocked.extend(screen_asset(spec, assets[aid], bars[aid], day, config))
        facts = [
            replay.fact(row)
            for row in (pmi, prior, profit, previous, revenue, cost)
            if row is not None
        ]
        items.append(
            {
                "industry_id": iid,
                "name": industry["name"],
                "asset_id": aid,
                "asset_name": spec["name"],
                "fit": spec["fit"],
                "mapping_note": spec.get("note", ""),
                "selected": not blocked,
                "reasons": blocked,
                "counter_evidence": ["成本同比高于营收同比"]
                if cost
                and revenue
                and cost.value is not None
                and revenue.value is not None
                and cost.value > revenue.value
                else [],
                "facts": facts,
                "max_ancestor_available_at": (
                    max(datetime.fromisoformat(f["available_at"]) for f in facts if f).isoformat()
                    if facts
                    else None
                ),
            }
        )
    accepted = [item for item in items if item["selected"]]
    weights = {item["asset_id"]: config["max_weight"] / len(accepted) for item in accepted}
    return {
        "day": day.isoformat(),
        "as_of": at.isoformat(),
        "leading_on": on,
        "candidates": items,
        "weights": weights,
    }


def event_study(
    events: list[dict[str, Any]],
    inputs: dict[str, Any],
    bars: dict[str, list[MarketBar]],
    actions: list[CorporateAction],
) -> list[dict[str, Any]]:
    """事件窗允许重叠，仅描述；逐笔净收益包含双边成本，不等同组合回测。"""
    config = inputs["config"]
    benchmark = inputs["benchmark"]["asset_id"]
    costs = float(config["commission_bps"]) / 10000
    slip = float(config["slippage_bps"]) / 10000
    outcomes = []
    for iid, spec in inputs["mapping"].items():
        aid = spec["asset_id"]
        a_bars, b_bars = bars[aid], bars[benchmark]
        by_asset = {bar.trade_date: bar for bar in a_bars}
        by_bench = {bar.trade_date: bar for bar in b_bars}
        common = sorted(set(by_asset) & set(by_bench))
        barriers = {
            date.fromisoformat(item["day"])
            for asset_id, seq in ((aid, a_bars), (benchmark, b_bars))
            for item in find_barriers(seq, [a for a in actions if a.asset_id == asset_id])
        }
        for horizon in config["event_windows"]:
            records = []
            pending = blocked = unavailable = 0
            for event in events:
                released = date.fromisoformat(event["day"])
                ix = next((i for i, day in enumerate(common) if day > released), None)
                if ix is None:
                    pending += 1
                    continue
                if (common[ix] - released).days > 10:
                    unavailable += 1
                    continue
                end_ix = ix + int(horizon) - 1
                if end_ix >= len(common):
                    pending += 1
                    continue
                entry, end = common[ix], common[end_ix]
                if any(entry < barrier <= end for barrier in barriers):
                    blocked += 1
                    continue

                def net(
                    asset_id: str,
                    seq: dict[date, MarketBar],
                    entry: date = entry,
                    end: date = end,
                ) -> float:
                    div = sum(
                        float(a.distribution_per_unit)
                        for a in actions
                        if a.asset_id == asset_id
                        and entry < a.ex_date <= end
                        and a.action_type == "cash_dividend"
                    )
                    return (float(seq[end].close) * (1 - slip) * (1 - costs) + div) / (
                        float(seq[entry].open) * (1 + slip) * (1 + costs)
                    ) - 1

                asset_return, benchmark_return = net(aid, by_asset), net(benchmark, by_bench)
                records.append(
                    {
                        "event_id": event["event_id"],
                        "signal": event["on"],
                        "entry": entry.isoformat(),
                        "exit": end.isoformat(),
                        "asset_net_return": asset_return,
                        "benchmark_net_return": benchmark_return,
                        "net_excess": asset_return - benchmark_return,
                    }
                )
            stats = compare([(r["signal"], r["net_excess"]) for r in records])
            outcomes.append(
                {
                    "industry_id": iid,
                    "asset_id": aid,
                    "fit": spec["fit"],
                    "trading_days": horizon,
                    "pending": pending,
                    "blocked": blocked,
                    "unavailable": unavailable,
                    "stats": stats,
                    "records": records,
                }
            )
    tested = [o for o in outcomes if o["stats"]["p_value"] is not None]
    adjusted = benjamini_hochberg([o["stats"]["p_value"] for o in tested])
    for outcome, q_value in zip(tested, adjusted, strict=True):
        outcome["stats"]["q_value"] = q_value
    return outcomes


def run_chain_demo(inputs: dict[str, Any]) -> dict[str, Any]:
    """确定性执行；输入 bundle 足以在原库更新后重现相同结果。"""
    config = inputs["config"]
    if inputs["policy"]["mode"] != MODE.value:
        raise ValueError("示范仅支持明确标注的 RECONSTRUCTED_PIT")
    replay = EvidenceReplay(inputs)
    assets = {
        a.asset_id: a for a in (AssetMaster.model_validate(item) for item in inputs["assets"])
    }
    cutoff = datetime.fromisoformat(inputs["as_of"])
    if cutoff.tzinfo is None:
        raise ValueError("as_of 必须带时区")
    actions = [
        action
        for action in (CorporateAction.model_validate(item) for item in inputs["actions"])
        if datetime.combine(action.ex_date, time(15), TZ_SHANGHAI) <= cutoff
    ]
    bars = {aid: [] for aid in assets}  # type: dict[str, list[MarketBar]]
    for item in inputs["bars"]:
        bar = MarketBar.model_validate(item)
        if datetime.combine(bar.trade_date, time(15), TZ_SHANGHAI) <= cutoff:
            bars[bar.asset_id].append(bar)
    for seq in bars.values():
        seq.sort(key=lambda bar: bar.trade_date)
    eligible = sorted(
        {spec["asset_id"] for spec in inputs["mapping"].values() if spec["fit"] == "对口"}
    )
    benchmark = inputs["benchmark"]["asset_id"]
    shared = set(bar.trade_date for bar in bars[benchmark])
    for aid in eligible:
        shared &= {bar.trade_date for bar in bars[aid]}
    days = sorted(day for day in shared if day >= date.fromisoformat(inputs["start"]))
    if not eligible or len(days) < 3:
        raise ValueError("没有对口资产或共同交易日不足")
    for aid in [*eligible, benchmark]:
        seq = [bar for bar in bars[aid] if days[0] <= bar.trade_date <= days[-1]]
        if any(bar.adj_method != "none" for bar in seq):
            raise ValueError(f"{aid} 不是不复权行情")
        if find_barriers(seq, [a for a in actions if a.asset_id == aid]):
            raise ValueError(f"{aid} 交易区间存在未核实拆并，拒绝组合回测")
    decisions = [decision(replay, inputs, day, assets, bars) for day in days]
    events = []
    excluded_events = []
    seen_periods: set[str] = set()
    for row in decisions:
        facts = row["candidates"][0]["facts"]
        leading = [f for f in facts if f["series_id"] == config["leading_series"]]
        if not leading:
            continue
        latest = leading[0]
        if latest["period"] in seen_periods:
            continue
        seen_periods.add(latest["period"])
        # 首日已有的旧发布只作初始化，不能冒充首日新事件。
        released = datetime.fromisoformat(latest["available_at"])
        if released.date() < days[0]:
            excluded_events.append(
                {"period": latest["period"], "reason": "区间首日已知旧发布，仅作初始化"}
            )
            continue
        if len(leading) < 2 or any(f["value"] is None for f in leading):
            excluded_events.append({"period": latest["period"], "reason": "相邻期数值不足"})
            continue
        current_period, prior_period = (date.fromisoformat(f["period"]) for f in leading)
        if (
            current_period.year - prior_period.year
        ) * 12 + current_period.month - prior_period.month != 1:
            excluded_events.append({"period": latest["period"], "reason": "缺月，不进入事件对照"})
            continue
        verifications = []
        for item in row["candidates"]:
            sid = f"{config['industries'][item['industry_id']]['prefix']}.profit_ytd_yoy"
            known = [f for f in item["facts"] if f["series_id"] == sid]
            known_period = known[0]["period"] if known else ""
            following = next(
                (
                    (later, candidate, fact)
                    for later in decisions
                    if later["day"] > row["day"]
                    for candidate in later["candidates"]
                    if candidate["industry_id"] == item["industry_id"]
                    for fact in candidate["facts"]
                    if fact["series_id"] == sid and fact["period"] > known_period
                ),
                None,
            )
            if following is None:
                verifications.append({"industry_id": item["industry_id"], "status": "PENDING"})
            else:
                later, candidate, fact = following
                # 同一天的 facts 中当期在前、上期在后；利润改善需相邻公布期。
                profit_facts = [f for f in candidate["facts"] if f["series_id"] == sid]
                valid = (
                    len(profit_facts) == 2
                    and all(f["value"] is not None for f in profit_facts)
                    and adjacent_periods(
                        date.fromisoformat(profit_facts[1]["period"]),
                        date.fromisoformat(profit_facts[0]["period"]),
                    )
                )
                delta = profit_facts[0]["value"] - profit_facts[1]["value"] if valid else None
                verifications.append(
                    {
                        "industry_id": item["industry_id"],
                        "status": "OBSERVED" if valid else "MISSING",
                        "verification_day": later["day"],
                        "profit_delta_pp": delta,
                        "improved": None if delta is None else delta > 0,
                        "lead_calendar_days": (
                            datetime.fromisoformat(fact["available_at"]) - released
                        ).total_seconds()
                        / 86400,
                        "facts": profit_facts,
                    }
                )
        events.append(
            {
                "event_id": f"demo-{digest([latest['series_id'], latest['period']])[:16]}",
                "day": row["day"],
                "period": latest["period"],
                "on": row["leading_on"],
                "facts": leading,
                "available_at": latest["available_at"],
                "economic_verifications": verifications,
            }
        )
    # 只在目标变化时调仓；终点前一天明确下达清仓，末日开盘结算双边费。
    targets: dict[date, dict[str, float]] = {}
    last_weights: dict[str, float] | None = None
    for row in decisions[:-1]:
        if row["weights"] != last_weights:
            targets[date.fromisoformat(row["day"])] = row["weights"]
            last_weights = row["weights"]
    targets[days[-2]] = {}
    decisions[-2]["execution_override"] = "预设回测终点清仓：末日开盘退出并结算费用"
    decisions[-2]["submitted_weights"] = {}
    dataset = MarketDataset(
        HistoricalUniverse([assets[aid] for aid in [*eligible, benchmark]]),
        [
            bar
            for aid in [*eligible, benchmark]
            for bar in bars[aid]
            if bar.trade_date in shared and bar.trade_date >= days[0]
        ],
        [a for a in actions if a.asset_id in [*eligible, benchmark]],
        days,
    )
    costs = CostModel(config["commission_bps"], config["slippage_bps"], config["cash_rate_annual"])
    constraints = PortfolioConstraints(max_weight=config["max_weight"])
    engine = BacktestEngine(dataset, costs=costs, constraints=constraints)
    strategy = engine.run(targets)
    baselines = {}
    for name, weights in {
        "cash": {},
        "benchmark_matched_weight": {benchmark: config["max_weight"]},
        "industry_buy_hold_matched_weight": {
            aid: config["max_weight"] / len(eligible) for aid in eligible
        },
    }.items():
        baselines[name] = engine.run({days[0]: weights, days[-2]: {}}).model_dump(mode="json")
    stressed = BacktestEngine(
        dataset,
        costs=CostModel(10, 10, config["cash_rate_annual"]),
        constraints=constraints,
    ).run(targets)
    reasons = Counter(
        reason for row in decisions for item in row["candidates"] for reason in item["reasons"]
    )
    economic_summary = []
    for iid in config["industries"]:
        observed = [
            v
            for event in events
            if event["on"]
            for v in event["economic_verifications"]
            if v["industry_id"] == iid and v["status"] == "OBSERVED"
        ]
        economic_summary.append(
            {
                "industry_id": iid,
                "on_events_observed": len(observed),
                "next_profit_improved": sum(v["improved"] for v in observed),
                "lead_days_mean": (
                    sum(v["lead_calendar_days"] for v in observed) / len(observed)
                    if observed
                    else None
                ),
            }
        )
    return {
        "schema": "chain-demo-report-v1",
        "input_sha256": digest(inputs),
        "chain": config["name"],
        "snapshot_id": inputs["snapshot_id"],
        "as_of": inputs["as_of"],
        "start": days[0].isoformat(),
        "end": days[-1].isoformat(),
        "engineering_status": "COMPLETE",
        "research_status": "NOT_VALIDATED",
        "config": config,
        "decisions": decisions,
        "events": events,
        "excluded_events": excluded_events,
        "event_study": event_study(events, inputs, bars, actions),
        "economic_summary": economic_summary,
        "selection_summary": {
            "decision_days": len(days),
            "event_count": len(events),
            "leading_on_events": sum(event["on"] for event in events),
            "days_with_candidate": sum(bool(row["weights"]) for row in decisions),
            "rejections": dict(reasons),
            "latest": decisions[-1],
        },
        "targets": {day.isoformat(): weights for day, weights in sorted(targets.items())},
        "backtest": strategy.model_dump(mode="json"),
        "baselines": baselines,
        "stress_10bps_each": stressed.model_dump(mode="json"),
        "hypotheses": inputs.get("hypotheses", []),
        "limitations": [
            "本规则于 2026-10-27 固定，属于回顾性示范，无历史事前注册或独立留出验收；"
            "NOT_VALIDATED。",
            "AI 假设按实际创建时间展示，不参与历史信号、筛选和参数选择。",
            "PMI 是全国制造业宏观代理，事件关联不证明电力行业的因果传导。",
            "研究数据为 RECONSTRUCTED_PIT：现时取得的历史版本，不主张同期原件或 STRICT 证据。",
            "映射为当前行业与 ETF 代理，未重建历史成分；无估值、市场一致预期或完整分红证明。",
            "事件窗可重叠；统计量使用循环平移及本示范全部行业周期的 BH 校正，仍不能当独立交易。",
            "成交采用次日开盘、整数份额、双边佣金和滑点；不模拟盘口、最小交易单位和市场冲击。",
            "历史零成交量作为可交易性代理；缺少完整停牌档案。期货连续与资金流不进入历史交易。",
            "所有组合从相同初始资金、首日现金开始，末日前一天清仓指令、末日开盘退出；"
            "费用与分红按相同口径。",
        ],
    }


def format_report(report: dict[str, Any]) -> str:
    summary = report["selection_summary"]
    lines = [
        f"# {report['chain']} · 完整产业链示范",
        "",
        f"工程：{report['engineering_status']}；有效性：{report['research_status']}",
        f"区间：{report['start']} 至 {report['end']}；快照：{report['snapshot_id']}",
        f"输入 SHA256：{report['input_sha256']}",
        "",
        "## 固定规则与链路",
        "",
        f"{report['config'].get('leading_name', report['config']['leading_series'])}"
        f"≥{report['config']['leading_floor']}、"
        f"较相邻月上升≥{report['config']['leading_delta']}点 → "
        "行业利润累计同比改善且同期营收同比>0 → 对口ETF且最近观察窗有成交 → "
        f"总权重{report['config']['max_weight']:.0%}，其余现金。",
        "逐日17:00仅用可见版本，目标变化后次交易日开盘执行；条件不满足退出。无阈值搜索。",
        f"领先指标所属期距决策日≤{report['config']['leading_max_age_days']}日，"
        f"财务所属期≤{report['config']['fundamental_max_age_days']}日；不同期财务不混用。",
        f"费用：单边佣金{report['config']['commission_bps']}bps+"
        f"滑点{report['config']['slippage_bps']}bps，压力情景各10bps；"
        f"分红现金入账，现金年利率{report['config']['cash_rate_annual']:.2%}。",
        "",
        "## 事件研究与资产筛选",
        "",
        f"{summary['decision_days']} 个决策日，{summary['event_count']} 个新发布事件，"
        f"{summary['leading_on_events']} 次领先阈值通过，"
        f"{summary['days_with_candidate']} 日形成研究候选。",
    ]
    for outcome in report["event_study"]:
        stats = outcome["stats"]
        mean = stats["mean_on"]
        lines.append(
            f"- {outcome['asset_id']}（{outcome['fit']}）{outcome['trading_days']}交易日："
            f"事件组{stats['n_on']}、对照组{stats['n_off']}，"
            f"事件组平均费后超额{'不可计算' if mean is None else f'{mean:.2%}'}，"
            f"q={stats['q_value']}；未成熟{outcome['pending']}，异常窗口剔除{outcome['blocked']}。"
        )
    for item in report["economic_summary"]:
        lead = item["lead_days_mean"]
        lines.append(
            f"- {item['industry_id']}：领先阈值通过后已观察到"
            f"{item['on_events_observed']}次下一期利润，"
            f"其中{item['next_profit_improved']}次改善；平均公布提前量"
            f"{'不可计算' if lead is None else f'{lead:.1f}天'}（非本系统实际发现提前量）。"
        )
    lines += ["", "最新筛选："]
    for item in summary["latest"]["candidates"]:
        lines.append(
            f"- {item['asset_name']}：{'研究候选' if item['selected'] else '未入选'}；"
            + ("；".join(item["reasons"]) or "全部固定条件通过")
        )
    lines += ["", "## 组合回测（事件窗之外的独立持仓路径）", ""]
    runs = {
        "策略": report["backtest"],
        **report["baselines"],
        "压力成本": report["stress_10bps_each"],
    }
    for name, result in runs.items():
        metrics = result["metrics"]
        lines.append(
            f"- {name}：费后收益 {metrics['total_return']:.2%}，"
            f"最大回撤 {metrics['max_drawdown']:.2%}，成交 {metrics['trades']} 笔。"
        )
    lines += ["", "## 现实 AI 研究（不回灌历史）", ""]
    for hypothesis in report["hypotheses"]:
        lines += [
            f"- {hypothesis['created_at']}：{hypothesis['statement']}",
            f"  独立反证：{hypothesis['counter_verdict']}；假设ID：{hypothesis['hypothesis_id']}",
        ]
        lines.extend(
            f"  - 失效条件：{condition}"
            for condition in hypothesis.get("invalidation_conditions", [])
        )
    if not report["hypotheses"]:
        lines.append("该截止时点前没有本地 AI 假设；历史研究及交易回放不依赖 AI。")
    lines += ["", "## 结论边界", "", *[f"- {item}" for item in report["limitations"]]]
    return "\n".join(lines) + "\n"
