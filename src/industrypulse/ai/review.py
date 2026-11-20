"""Daily review cohorts and fixed-horizon outcomes, stored apart from legacy history."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, cast

from industrypulse.ai.leads import target_period, tracking_nodes
from industrypulse.core import TZ_SHANGHAI
from industrypulse.intelligence.service import write_json
from industrypulse.signals.industry_chains import active_baskets

ROOT = Path(__file__).resolve().parents[3]
WINDOWS = (5, 20, 60)
MISSING = "数据不足"
WAITING = "待到期"
_CLOSE = time(15)
_LOCK = Lock()
_CNINFO = {"大额中标", "重大长协合同", "产能收缩/停产"}
_SEC = {"1.01", "1.02", "2.01", "2.02", "2.05", "2.06"}

Closes = dict[str, dict[str, float]]
ReadCloses = Callable[[set[str], date, date], Closes]


def closed_through(as_of: datetime) -> date:
    local = as_of.astimezone(TZ_SHANGHAI)
    return local.date() - timedelta(days=local.time() < _CLOSE)


def _first_session_day(at: str) -> str:
    local = datetime.fromisoformat(at).astimezone(TZ_SHANGHAI)
    return (local.date() + timedelta(days=local.time() >= _CLOSE)).isoformat()


def universe_for_day(
    root: Path, sector_symbols: dict[str, str], today: date
) -> dict[str, dict[str, Any]]:
    baskets = active_baskets(root, today)
    result = {}
    for node, name in tracking_nodes(root).items():
        basket = baskets.get(node)
        symbol = sector_symbols.get(node)
        result[node] = {
            "name": name,
            "members": basket["members"]
            if basket
            else ([{"symbol": symbol, "weight": 1.0}] if symbol else []),
            "effective_from": basket["effective_from"] if basket else None,
        }
    return result


def universe_symbols(universe: dict[str, dict[str, Any]]) -> set[str]:
    return {member["symbol"] for item in universe.values() for member in item["members"]}


def _price(closes: Closes, symbol: str, day: str) -> float | None:
    value = closes.get(symbol, {}).get(day)
    return value if value is not None and math.isfinite(value) and value > 0 else None


def _basket_prices(closes: Closes, members: list[dict[str, Any]], day: str) -> dict[str, float]:
    prices = {}
    for item in members:
        symbol = item["symbol"]
        if (price := _price(closes, symbol, day)) is None:
            return {}
        prices[symbol] = price
    return prices


def _visible_prices(closes: Closes, members: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not members:
        return None
    for day in sorted(closes.get(members[0]["symbol"], {}), reverse=True):
        if prices := _basket_prices(closes, members, day):
            return {"date": day, "closes": prices}
    return None


def _major_announcements(signals: dict[str, Any], at: str) -> dict[str, dict[str, Any]]:
    results = {}
    for bucket in (signals.get("by_sector") or {}).values():
        for item in bucket.get("hard_signals") or []:
            cninfo = item.get("kind") == "cninfo_announcement" and item.get("tag") in _CNINFO
            sec = (
                item.get("kind") == "sec_chain_filing"
                and item.get("content_verified")
                and (_SEC.intersection(item.get("items") or []))
            )
            if not (cninfo or sec) or not item.get("date") or not item.get("sector"):
                continue
            key = item.get("url") or f"{item['sector']}|{item['date']}|{item.get('title', '')}"
            codes = re.findall(r"\((\d{6})\)", str(item.get("title") or "")) if cninfo else []
            results[hashlib.sha256(key.encode()).hexdigest()[:20]] = {
                "first_seen_at": at,
                "date": item["date"],
                "sector": item["sector"],
                "title": item.get("title"),
                "url": item.get("url"),
                "codes": codes,
            }
    return results


def _subject(item: dict[str, Any]) -> Any:
    return (item.get("anchor") or {}).get("node") or item.get("sector")


def _decision(node: str, item: dict[str, Any], stance: str, day: str) -> dict[str, Any]:
    events = sorted(
        {ref["id"] for ref in item.get("evidence") or [] if ref["type"] != "market_metric"}
    )
    episode = hashlib.sha256("|".join([node, stance, *(events or [day])]).encode()).hexdigest()[:20]
    return {
        "stance": stance,
        "title": item.get("title"),
        "thesis": item.get("inference"),
        "episode": episode,
    }


def _lead_period(lead: dict[str, Any]) -> str | None:
    observations = lead.get("observations") or []
    return str(observations[-1]["period"]) if observations else None


def _industry_result(check: dict[str, Any], lead: dict[str, Any], start: str) -> dict[str, Any]:
    metric = check["metric"]
    if not metric:
        return {"status": "未约定核验指标"}
    for entry in lead.get("timeline") or []:
        if entry["type"] != "industry" or entry["at"] <= start:
            continue
        outcome = entry["to"]
        if outcome == "超期未验证归档":
            return {"status": outcome, "at": entry["at"], "run_id": entry["run_id"]}
        if outcome not in {"已兑现", "已证伪"}:
            continue
        for observation in lead["observations"]:
            if check["expected_period"] and observation["period"] != check["expected_period"]:
                continue
            proof = [
                fact
                for fact in observation["evidence"]
                if fact["run_id"] == entry["run_id"]
                and fact["source"]["type"]
                in {"sec_filing", "cninfo_ann", "twse_pulse", "announcement"}
            ]
            if proof:
                return {
                    "status": outcome,
                    "period": observation["period"],
                    "evidence": proof,
                    "at": entry["at"],
                    "run_id": entry["run_id"],
                    "detail": entry["detail"],
                }
    return {"status": "待核验"}


def _interpret(move: float, benchmark: float, excess: float) -> str:
    if move < 0 and benchmark < 0 and abs(excess) <= 1:
        return "与大盘同步下跌"
    if excess < -1:
        return "跑输大盘"
    if excess > 1:
        return "跑赢大盘"
    return "与大盘接近"


def _window(
    closes: Closes, members: list[dict[str, Any]], benchmark: str, sessions: list[str]
) -> dict[str, Any]:
    series = [_basket_prices(closes, members, day) for day in sessions]
    if not all(series):
        return {"status": MISSING, "date": sessions[-1]}
    base = series[0]
    values = [
        sum(
            member["weight"] * prices[member["symbol"]] / base[member["symbol"]]
            for member in members
        )
        for prices in series
    ]
    benchmark_base, benchmark_close = (
        closes[benchmark][sessions[0]],
        closes[benchmark][sessions[-1]],
    )
    market_move = (values[-1] - 1) * 100
    benchmark_move = (benchmark_close / benchmark_base - 1) * 100
    excess = market_move - benchmark_move
    peak, drawdown = 1.0, 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = max(drawdown, (1 - value / peak) * 100)
    return {
        "status": "已锁定",
        "date": sessions[-1],
        "return_pct": round(market_move, 4),
        "benchmark_return_pct": round(benchmark_move, 4),
        "excess_pp": round(excess, 4),
        "max_gain_pct": round((max(values) - 1) * 100, 4),
        "max_drawdown_pct": round(drawdown, 4),
        "interpretation": _interpret(market_move, benchmark_move, excess),
        "calculation_start_closes": base,
        "end_closes": series[-1],
        "benchmark_start_close": benchmark_base,
        "benchmark_end_close": benchmark_close,
    }


def _market_result(
    item: dict[str, Any],
    locked: dict[str, Any],
    sessions: list[str],
    closes: Closes,
    benchmark: str,
) -> dict[str, Any]:
    members = item["members"]
    if members and "start" not in locked:
        for day in sessions:
            if prices := _basket_prices(closes, members, day):
                locked["start"] = {
                    "date": day,
                    "closes": prices,
                    "benchmark_close": closes[benchmark][day],
                }
                break
    start = locked.get("start")
    windows = dict(locked.get("windows") or {})
    if not start or start["date"] not in sessions:
        status = (
            MISSING if not members or sessions or start or not item["visible_at_scan"] else WAITING
        )
        for days in WINDOWS:
            windows.setdefault(str(days), {"status": status})
    else:
        origin = sessions.index(start["date"])
        for days in WINDOWS:
            key = str(days)
            if key in windows:
                continue
            span = sessions[origin : origin + days + 1]
            outcome = (
                _window(closes, members, benchmark, span)
                if len(span) == days + 1
                else {"status": WAITING}
            )
            windows[key] = outcome
            if outcome["status"] == "已锁定":
                locked.setdefault("windows", {})[key] = outcome
    return {"start": start, "windows": windows}


def _omission(
    node: str,
    item: dict[str, Any],
    window: dict[str, Any],
    at: str,
    due: str | None,
    announcements: list[dict[str, Any]],
    judgment_days: set[str],
) -> dict[str, Any] | None:
    if item["decision"] or not due or any(at[:10] <= day <= due for day in judgment_days):
        return None
    codes = {member["symbol"][2:] for member in item["members"]}
    relevant = [
        event
        for event in announcements
        if at[:10] <= event["date"] <= due
        and (event["date"] > at[:10] or event["first_seen_at"] >= at)
        and (event["sector"] == node or codes.intersection(event["codes"]))
    ]
    excess = window.get("excess_pp")
    moved = excess is not None and abs(excess) >= 8
    insufficient = window["status"] == MISSING
    if not (moved or insufficient or relevant):
        return None
    reasons = []
    if moved:
        reasons.append("T+20 超额达到 ±8 个百分点")
    if relevant:
        reasons.append("重大公告复核")
    if insufficient:
        reasons.append("T+20 行情数据不足")
    return {
        "node": node,
        "name": item["name"],
        "classification": "缺数据" if insufficient else item["coverage"],
        "scan_classification": item["coverage"],
        "excess_pp": excess,
        "reasons": reasons,
        "announcements": relevant,
    }


def _sessions(closes: Closes, benchmark: str, at: str) -> list[str]:
    first = _first_session_day(at)
    return sorted(
        day
        for day in closes.get(benchmark, {})
        if day >= first and _price(closes, benchmark, day) is not None
    )


def _review_item(
    item: dict[str, Any],
    locked: dict[str, Any],
    sessions: list[str],
    closes: Closes,
    benchmark: str,
    leads: dict[str, dict[str, Any]],
    at: str,
) -> dict[str, Any]:
    market = _market_result(item, locked, sessions, closes, benchmark)
    industry = []
    for check in item["checks"]:
        lead_id = check["lead_id"]
        stored = locked.get("industry", {}).get(lead_id)
        result = stored or _industry_result(check, leads.get(lead_id, {}), at)
        if stored is None and result["status"] in {"已兑现", "已证伪", "超期未验证归档"}:
            locked.setdefault("industry", {})[lead_id] = result
        industry.append({**check, **result})
    return {**item, "market": market, "industry": industry}


class ReviewHistory:
    def __init__(self, *, root: Path | None = None) -> None:
        self.root = root or Path(os.getenv("INDUSTRYPULSE_ROOT", str(ROOT)))
        data_dir = Path(os.getenv("INDUSTRYPULSE_AI_DIR", str(self.root / "data/ai")))
        self.path = data_dir / "review-v2.json"

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"days": {}, "calls": {}, "announcements": {}}
        return cast(dict[str, Any], json.loads(self.path.read_text(encoding="utf-8")))

    def record_run(
        self,
        report: dict[str, Any],
        universe: dict[str, dict[str, Any]],
        benchmark: dict[str, str],
        visible: Closes,
        signals: dict[str, Any],
        leads: list[dict[str, Any]],
        *,
        published_at: str | None = None,
        visible_captured_at: str | None = None,
    ) -> None:
        at = report["generated_at"]
        day = at[:10]
        with _LOCK:
            state = self._load()
            announcements = _major_announcements(signals, at)
            fresh = {
                key: value
                for key, value in announcements.items()
                if key not in state["announcements"]
            }
            state["announcements"].update(fresh)
            decisions = {
                _subject(item): _decision(_subject(item), item, stance, day)
                for key, stance in (
                    ("early_opportunities", "潜伏关注"),
                    ("high_risk_warnings", "高危预警"),
                )
                for item in report.get(key) or []
                if _subject(item)
            }
            watched = {_subject(item) for item in report.get("watch_list") or []}
            watched.update(
                f"{chain['id']}:{node['id']}"
                for chain in report.get("chain_assessments") or []
                for node in chain["nodes"]
                if node["profit_status"] == "待验证"
            )
            checks: dict[str, list[dict[str, Any]]] = {}
            for lead in leads:
                if lead["industry_status"] in {"已兑现", "已证伪", "超期未验证归档"}:
                    continue
                watched.add(lead["node"])
                metric = lead["validation"]
                period = _lead_period(lead)
                checks.setdefault(lead["node"], []).append(
                    {
                        "lead_id": lead["id"],
                        "metric": metric,
                        "observed_period": period,
                        "expected_period": lead.get(
                            "expected_period", target_period(metric, period)
                        ),
                        "check_by": lead["check_by"],
                    }
                )
            benchmark_visible = _visible_prices(
                visible, [{"symbol": benchmark["symbol"], "weight": 1.0}]
            )
            subjects = {}
            for node, item in universe.items():
                prices = _visible_prices(visible, item["members"])
                decision = decisions.get(node)
                coverage = (
                    "已发判断"
                    if decision
                    else "在观察未升级"
                    if node in watched
                    else "没发现"
                    if prices and benchmark_visible and prices["date"] == benchmark_visible["date"]
                    else "缺数据"
                )
                subjects[node] = {
                    **item,
                    "coverage": coverage,
                    "decision": decision,
                    "visible_at_scan": prices,
                    "checks": checks.get(node, []),
                }
            header = {
                "run_id": report["run_id"],
                "generated_at": at,
                "mode": report["mode"],
                "published_at": published_at or at,
                "visible_captured_at": visible_captured_at or at,
                "benchmark": benchmark,
                "benchmark_visible_at_scan": benchmark_visible,
            }
            changed = bool(fresh)
            if day not in state["days"]:
                state["days"][day] = {**header, "subjects": subjects, "locked": {}, "judgments": []}
                changed = True
            judgments = state["days"][day].setdefault("judgments", [])
            if new_judgments := decisions.keys() - set(judgments):
                judgments.extend(sorted(new_judgments))
                changed = True
            if report["mode"] == "ai":
                for node, item in subjects.items():
                    if (decision := item["decision"]) and decision["episode"] not in state["calls"]:
                        state["calls"][decision["episode"]] = {
                            **header,
                            "node": node,
                            "subject": item,
                            "locked": {},
                        }
                        changed = True
            if changed:
                write_json(self.path, state)

    def symbols(self) -> set[str]:
        return self._symbols(self._load())

    @staticmethod
    def _symbols(state: dict[str, Any]) -> set[str]:
        symbols = set()
        for day in state["days"].values():
            symbols.add(day["benchmark"]["symbol"])
            symbols.update(universe_symbols(day["subjects"]))
        for call in state["calls"].values():
            symbols.add(call["benchmark"]["symbol"])
            symbols.update(universe_symbols({call["node"]: call["subject"]}))
        return symbols

    def evaluate(
        self,
        read: ReadCloses,
        leads: list[dict[str, Any]],
        *,
        as_of: datetime,
        selected: str | None,
        signals: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with _LOCK:
            state = self._load()
            days = sorted(state["days"], reverse=True)
            summary: dict[str, Any] = {
                "samples": 0,
                "locked_windows": {str(days): 0 for days in WINDOWS},
                "industry_verified": 0,
            }
            if not days:
                return {"days": [], "selected": None, "summary": summary}
            closes = read(self._symbols(state), date.fromisoformat(days[-1]), closed_through(as_of))
            lead_by_id = {lead["id"]: lead for lead in leads}
            before = json.dumps(state, ensure_ascii=False)
            if signals:
                seen = signals.get("updated_at") or as_of.isoformat()
                for key, event in _major_announcements(signals, seen).items():
                    state["announcements"].setdefault(key, event)
            announcements = list(state["announcements"].values())
            calls_by_day: dict[str, list[dict[str, Any]]] = {}
            for call in state["calls"].values():
                symbol = call["benchmark"]["symbol"]
                published = call["published_at"]
                row = _review_item(
                    call["subject"],
                    call["locked"],
                    _sessions(closes, symbol, published),
                    closes,
                    symbol,
                    lead_by_id,
                    published,
                )
                calls_by_day.setdefault(call["generated_at"][:10], []).append(
                    {
                        "node": call["node"],
                        "benchmark": call["benchmark"],
                        "published_at": published,
                        **row,
                    }
                )
                summary["samples"] += 1
                for window, value in row["market"]["windows"].items():
                    summary["locked_windows"][window] += value["status"] == "已锁定"
                summary["industry_verified"] += int(
                    any(check["status"] == "已兑现" for check in row["industry"])
                )
            chosen = None
            chosen_mature = False
            judgment_days: dict[str, set[str]] = {}
            for day, run in state["days"].items():
                for node in run.get("judgments", []):
                    judgment_days.setdefault(node, set()).add(day)
            for day in days:
                run = state["days"][day]
                at = run["published_at"]
                symbol = run["benchmark"]["symbol"]
                sessions = _sessions(closes, symbol, at)
                rows = []
                omissions = []
                for node, item in run["subjects"].items():
                    locked = run["locked"].setdefault(node, {})
                    row = _review_item(item, locked, sessions, closes, symbol, lead_by_id, at)
                    if not locked:
                        run["locked"].pop(node, None)
                    rows.append({"node": node, **row})
                    market = row["market"]
                    at_20 = market["windows"]["20"]
                    due = at_20.get("date") or (
                        sessions[20] if market["start"] is None and len(sessions) > 20 else None
                    )
                    if omission := _omission(
                        node,
                        item,
                        at_20,
                        run["generated_at"],
                        due,
                        announcements,
                        judgment_days.get(node, set()),
                    ):
                        omissions.append(omission)
                mature = any(row["market"]["windows"]["20"].get("date") for row in rows)
                mature |= len(sessions) > 20
                if day == selected or (
                    selected not in days and (chosen is None or (mature and not chosen_mature))
                ):
                    chosen_mature = mature
                    chosen = {
                        "day": day,
                        "generated_at": run["generated_at"],
                        "published_at": at,
                        "visible_captured_at": run["visible_captured_at"],
                        "run_id": run["run_id"],
                        "mode": run["mode"],
                        "benchmark": run["benchmark"],
                        "benchmark_visible_at_scan": run["benchmark_visible_at_scan"],
                        "items": rows,
                        "calls": calls_by_day.get(day, []),
                        "omissions": omissions,
                    }
            if json.dumps(state, ensure_ascii=False) != before:
                write_json(self.path, state)
            return {"days": days, "selected": chosen, "summary": summary}
