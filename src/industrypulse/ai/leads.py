"""跨次扫描持久化产业线索及其独立的市场观察。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, cast

import yaml

from industrypulse.intelligence.service import write_json

ROOT = Path(__file__).resolve().parents[3]
CATALYST_TYPES = (
    "revenue_acceleration",
    "demand_change",
    "cost_change",
    "price_change",
    "capacity_change",
    "orders",
    "policy",
    "technology",
    "market_setup",
)
INDUSTRY_END = {"已兑现", "已证伪", "超期未验证归档"}
_INDUSTRY_SOURCES = {"sec_filing", "cninfo_ann", "twse_pulse", "announcement"}
_DEFAULT_REVIEW_DAYS = 60
_MARKET_MOVE_PCT = 3.0
_LOCK = Lock()

PriceLookup = Callable[[str], tuple[float | None, str | None]]


def _period(value: Any) -> str | None:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{5}", text):  # TWSE 民国年与月份
        text = f"{int(text[:3]) + 1911}-{text[3:]}"
    if re.fullmatch(r"\d{4}-Q[1-4]", text, re.IGNORECASE):
        return text.upper() if int(text[:4]) else None
    if re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", text):
        try:
            date.fromisoformat(text if len(text) == 10 else f"{text}-01")
            return text
        except ValueError:
            pass
    return None


def tracking_nodes(root: Path) -> dict[str, str]:
    nodes = {}
    for name in ("news_sources", "industry_chains"):
        path = root / "config" / f"{name}.yaml"
        if not path.exists():
            continue
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        for domain in config.get("domains", []):
            nodes[domain["id"]] = domain["name"]
            nodes.update({item["id"]: item["name"] for item in domain["industries"]})
        for chain in config.get("chains", []):
            nodes.update({f"{chain['id']}:{node['id']}": node["name"] for node in chain["nodes"]})
    return nodes


def _deadline(value: Any, today: date) -> str:
    try:
        due = date.fromisoformat(str(value or ""))
    except ValueError:
        due = today + timedelta(days=_DEFAULT_REVIEW_DAYS)
    return max(today, due).isoformat()


def _period_start(period: str) -> date:
    if "-Q" in period:
        year, quarter = period.split("-Q")
        return date(int(year), (int(quarter) - 1) * 3 + 1, 1)
    return date.fromisoformat(period if len(period) == 10 else f"{period}-01")


def _facts(item: dict[str, Any]) -> list[dict[str, Any]]:
    sources = {ref["id"]: ref for ref in item.get("evidence") or []}
    return [
        {"source": sources[fact["evidence_id"]], "quote": fact["quote"]}
        for fact in item.get("facts") or []
        if fact["evidence_id"] in sources
    ]


def _candidate(
    item: dict[str, Any], nodes: dict[str, str], today: date, *, allow_unanchored: bool
) -> dict[str, Any] | None:
    anchor = item.get("anchor") or {}
    sector = str(item.get("sector") or "")
    node = str(anchor.get("node") or "")
    kind = str(anchor.get("catalyst_type") or "")
    period = _period(anchor.get("report_period"))
    facts = _facts(item)
    for fact in facts:
        source = fact["source"]
        if source["type"] == "twse_pulse" and (published_period := _period(source.get("date"))):
            period = published_period
            break
    provisional = node not in nodes or kind not in CATALYST_TYPES or period is None
    if provisional:
        if not allow_unanchored or sector not in nodes:
            return None
        node, kind = sector, "未分类"
    return {
        "node": node,
        "node_name": nodes[node],
        "catalyst_type": kind,
        "report_period": period or today.isoformat(),
        "period_known": period is not None,
        "provisional": provisional,
        "sector": sector,
        "title": item.get("title") or f"{nodes[node]}观察",
        "hypothesis": item.get("inference") or "",
        "validation": item.get("validation") or "",
        "industry_outcome": item.get("industry_outcome"),
        "check_by": _deadline(item.get("check_by"), today),
        "market_direction": item.get("market_direction"),
        "facts": facts,
        "gaps": item.get("gaps") or [],
    }


def _track_items(report: dict[str, Any]) -> list[tuple[dict[str, Any], bool]]:
    items = [(item, True) for item in report.get("watch_list") or []]
    items.extend(
        (item, False)
        for item in report.get("lead_updates") or []
        if any(fact["source"]["type"] != "market_metric" for fact in _facts(item))
    )
    for chain in report.get("chain_assessments") or []:
        source = chain["shock"]["evidence"]
        period = str(source.get("date") or report["generated_at"])[:10]
        for node in chain["nodes"]:
            if node["profit_status"] == "待验证":
                items.append(
                    (
                        {
                            **node,
                            "anchor": {
                                "node": f"{chain['id']}:{node['id']}",
                                "catalyst_type": "cost_change",
                                "report_period": period,
                            },
                            "title": f"{chain['name']} · {node['name']}",
                            "validation": "。".join(node["conditions"]),
                        },
                        False,
                    )
                )
    return items


def _transition(
    lead: dict[str, Any], track: str, status: str, at: str, run_id: str, detail: str
) -> None:
    field = f"{track}_status"
    before = lead[field]
    if before != status:
        lead[field] = status
        lead["changes"].append(
            {
                "type": track,
                "from": before,
                "to": status,
                "at": at,
                "run_id": run_id,
                "detail": detail,
            }
        )


def _record_observation(
    lead: dict[str, Any], candidate: dict[str, Any], at: str, run_id: str
) -> bool:
    period = candidate["report_period"]
    observation = lead["observations"].setdefault(
        period,
        {
            "period": period,
            "at": at,
            "run_id": run_id,
            "evidence": [],
            "hypothesis": candidate["hypothesis"],
            "gaps": candidate["gaps"],
        },
    )
    known = {item["source"]["id"] for item in observation["evidence"]}
    industrial = False
    added = False
    for fact in candidate["facts"]:
        source = fact["source"]
        if source["id"] not in known:
            observation["evidence"].append({**fact, "at": at, "run_id": run_id})
            known.add(source["id"])
            industrial |= source["type"] in _INDUSTRY_SOURCES
            added = True
    if added:
        observation["gaps"] = candidate["gaps"]
    return industrial


def _market_observation(lead: dict[str, Any], quotes: PriceLookup, at: str, run_id: str) -> None:
    basket = lead["market_members"]
    if not basket:
        return
    readings = {member["symbol"]: quotes(member["symbol"]) for member in basket}
    dates = {
        day for close, day in readings.values() if close and math.isfinite(close) and close > 0
    }
    if len(dates) != 1 or any(
        close is None or not math.isfinite(close) or close <= 0 or not day
        for close, day in readings.values()
    ):
        return
    day = next(iter(dates))
    if day is None or day > at[:10] or day <= (lead["market_date"] or ""):
        return
    if lead["market_baseline"] and day < lead["first_seen_at"][:10]:
        return
    closes = {symbol: close for symbol, (close, _) in readings.items()}
    lead["market_date"] = day
    if not lead["market_baseline"]:
        lead["market_baseline"] = {"date": day, "closes": closes}
        _transition(lead, "market", "观察中", at, run_id, f"{day} 首次取得完整收盘价")
        return
    base = lead["market_baseline"]["closes"]
    move = round(
        sum(
            member["weight"] * (closes[member["symbol"]] / base[member["symbol"]] - 1)
            for member in basket
        )
        * 100,
        2,
    )
    lead["market_move_pct"] = move
    direction = lead["market_direction"]
    status = "观察中"
    if abs(move) >= _MARKET_MOVE_PCT:
        status = "已异动"
        if direction in {"up", "down"}:
            status = "已确认" if (move > 0) == (direction == "up") else "反向异动"
    _transition(lead, "market", status, at, run_id, f"{day} 较首次收盘价 {move:+.2f}%")


def _identity(candidate: dict[str, Any]) -> str:
    text = f"{candidate['node']}|{candidate['catalyst_type']}"
    if candidate["provisional"]:
        # An unstructured watch item stays available without merging unrelated theses.
        source_ids = sorted({fact["source"]["id"] for fact in candidate["facts"]})
        text += "|" + ("|".join(source_ids) or " ".join(candidate["hypothesis"].split()))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class LeadLedger:
    def __init__(self, *, root: Path | None = None) -> None:
        self.root = root or Path(os.getenv("INDUSTRYPULSE_ROOT", str(ROOT)))
        data_dir = Path(os.getenv("INDUSTRYPULSE_AI_DIR", str(self.root / "data/ai")))
        self.path = data_dir / "leads.json"

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"leads": {}, "last_run_at": ""}
        return cast(dict[str, Any], json.loads(self.path.read_text(encoding="utf-8")))

    def active_context(self) -> list[dict[str, Any]]:
        leads = self._load()["leads"].values()
        return [
            {
                key: lead[key]
                for key in (
                    "id",
                    "node",
                    "node_name",
                    "sector",
                    "catalyst_type",
                    "title",
                    "hypothesis",
                    "validation",
                    "check_by",
                    "industry_status",
                    "market_status",
                )
            }
            | {"periods": sorted(lead["observations"], key=_period_start)[-4:]}
            for lead in leads
            if lead["industry_status"] not in INDUSTRY_END and not lead["provisional"]
        ]

    def record_run(
        self,
        report: dict[str, Any],
        *,
        sector_symbols: dict[str, str],
        lookup: PriceLookup | None,
    ) -> None:
        at, run_id = report["generated_at"], report["run_id"]
        today = datetime.fromisoformat(at).date()
        items = _track_items(report)
        nodes = tracking_nodes(self.root) if items else {}
        baskets = {
            f"{chain['id']}:{node['id']}": node["basket"]
            for chain in report.get("chain_assessments") or []
            for node in chain["nodes"]
            if node["basket"]
        }
        with _LOCK:
            state = self._load()
            leads = state["leads"]
            latest = at >= state["last_run_at"]
            before = json.dumps(state, ensure_ascii=False)
            if latest:
                for lead in leads.values():
                    if (
                        lead["industry_status"] not in INDUSTRY_END
                        and today.isoformat() > lead["check_by"]
                    ):
                        _transition(
                            lead, "industry", "超期未验证归档", at, run_id, "核验截止日已过"
                        )
            for item, allow_unanchored in items:
                candidate = _candidate(item, nodes, today, allow_unanchored=allow_unanchored)
                if candidate is None:
                    continue
                lead_id = _identity(candidate)
                lead = leads.get(lead_id)
                if lead is None:
                    basket = baskets.get(candidate["node"])
                    symbol = (
                        sector_symbols.get(candidate["sector"])
                        if ":" not in candidate["node"]
                        else None
                    )
                    members = (
                        basket["members"]
                        if basket
                        else ([{"symbol": symbol, "weight": 1.0}] if symbol else [])
                    )
                    lead = leads[lead_id] = {
                        **{
                            k: candidate[k]
                            for k in (
                                "node",
                                "node_name",
                                "catalyst_type",
                                "title",
                                "hypothesis",
                                "sector",
                                "provisional",
                                "validation",
                                "check_by",
                                "market_direction",
                            )
                        },
                        "id": lead_id,
                        "first_seen_at": at,
                        "first_run_id": run_id,
                        "observations": {},
                        "changes": [],
                        "industry_status": "待验证",
                        "market_status": "等待行情" if members else "暂无市场参照",
                        "market_members": members,
                        "market_baseline": None,
                        "market_date": None,
                        "market_move_pct": None,
                    }
                elif candidate["provisional"] and not candidate["period_known"]:
                    candidate["report_period"] = lead["first_seen_at"][:10]
                industrial = _record_observation(lead, candidate, at, run_id)
                current_period = max(
                    lead["observations"], key=_period_start, default=candidate["report_period"]
                )
                if (
                    latest
                    and industrial
                    and candidate["report_period"] == current_period
                    and lead["industry_status"] not in INDUSTRY_END
                ):
                    outcome = candidate["industry_outcome"]
                    if (
                        outcome not in {"已兑现", "已证伪"}
                        or candidate["provisional"]
                        or not lead["validation"]
                        or at <= lead["first_seen_at"]
                    ):
                        outcome = "验证中"
                    _transition(lead, "industry", outcome, at, run_id, candidate["hypothesis"])
            if lookup is not None and latest:
                quotes: dict[str, tuple[float | None, str | None]] = {}

                def once(symbol: str) -> tuple[float | None, str | None]:
                    if symbol not in quotes:
                        quotes[symbol] = lookup(symbol)
                    return quotes[symbol]

                for lead in leads.values():
                    _market_observation(lead, once, at, run_id)
            if leads:
                state["last_run_at"] = max(at, state["last_run_at"])
            if json.dumps(state, ensure_ascii=False) != before:
                write_json(self.path, state)

    def list(self) -> list[dict[str, Any]]:
        items = []
        for lead in self._load()["leads"].values():
            observations = list(lead["observations"].values())
            timeline = [
                {
                    "type": "discovery",
                    "at": lead["first_seen_at"],
                    "run_id": lead["first_run_id"],
                }
            ]
            for observation in observations:
                timeline.append(
                    {
                        "type": "observation",
                        "period": observation["period"],
                        "at": observation["at"],
                        "run_id": observation["run_id"],
                        "detail": observation["hypothesis"],
                    }
                )
                timeline.extend(
                    {
                        "type": "evidence",
                        "period": observation["period"],
                        **fact,
                    }
                    for fact in observation["evidence"]
                )
            timeline.extend(lead["changes"])
            items.append(
                {
                    **lead,
                    "observations": sorted(
                        observations, key=lambda item: _period_start(item["period"])
                    ),
                    "timeline": sorted(timeline, key=lambda item: item["at"]),
                }
            )
        return sorted(items, key=lambda item: item["timeline"][-1]["at"], reverse=True)
