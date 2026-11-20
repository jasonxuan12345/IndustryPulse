"""Evaluate the pilot chains using published prices and explicit conditions."""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import yaml

from industrypulse.ai.evidence import event_evidence

_MOVE = (
    r"(?:同比|环比|较上期|较上月|较去年同期)?\s*"
    r"(?P<move>上涨|上升|增长|增加|上扬|涨|下跌|下降|下滑|减少|回落|下挫|跌|持平)"
)
_PERCENT = r"\s*(?:了|约|达|为)?\s*(?P<pct>\d+(?:\.\d+)?)\s*[%％]"
_DOWN = {"下跌", "下降", "下滑", "减少", "回落", "下挫", "跌"}
_SPECULATIVE = re.compile(r"预计|预测|或|有望|可能|假设|预期")
COMMODITY_MOVE_THRESHOLD = 2.5


def _load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))


def pilot_commodities(root: Path) -> dict[str, str]:
    path = root / "config/industry_chains.yaml"
    if not path.exists():
        return {}
    wanted = {item["commodity"] for item in _load(path)["chains"]}
    markets = _load(root / "config/market/sectors.yaml")
    return {item["id"]: item["symbol"] for item in markets["commodities"] if item["id"] in wanted}


def is_pilot_commodity(item: dict[str, Any], pilots: dict[str, str]) -> bool:
    return (
        item.get("id") in pilots
        or item.get("symbol") in pilots.values()
        or any(f"({symbol})" in str(item.get("title") or "") for symbol in pilots.values())
    )


def _current(value: Any, as_of: datetime, *, days: int) -> bool:
    try:
        recorded = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return False
    if recorded.tzinfo is None:
        recorded = recorded.replace(tzinfo=as_of.tzinfo)
    return timedelta(0) <= as_of - recorded <= timedelta(days=days)


def _find_report(
    stories: list[dict[str, Any]], pattern: re.Pattern[str], *, threshold: float = 0
) -> tuple[dict[str, Any], re.Match[str]] | None:
    for story in stories:
        source = event_evidence(story)
        for clause in re.split(r"[。！？；;\n]", source["excerpt"]):
            match = pattern.search(clause)
            if not match or _SPECULATIVE.search(clause):
                continue
            if threshold and (match["move"] == "持平" or float(match["pct"]) < threshold):
                continue
            return source, match
    return None


def _shock(
    chain: dict[str, Any],
    symbol: str,
    markets: list[dict[str, Any]],
    stories: list[dict[str, Any]],
    as_of: datetime,
) -> tuple[str, dict[str, Any], str] | None:
    for row in markets:
        if row.get("id") != chain["commodity"] and row.get("symbol") != symbol:
            continue
        if not _current(row.get("date"), as_of, days=7):
            continue
        try:
            change = float(row["change_pct"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(change) or abs(change) < COMMODITY_MOVE_THRESHOLD:
            continue
        label = str(row.get("name") or symbol)
        excerpt = f"{label}（{symbol}）行情变动 {change:+.2f}%"
        if row.get("price") is not None:
            excerpt += f"，报价 {row['price']} {row.get('unit') or ''}".rstrip()
        source = {
            "id": f"chain:market:{symbol}:{row['date']}",
            "type": "market_metric",
            "title": label,
            "date": row["date"],
            "source": row.get("source") or "期货行情",
            "excerpt": excerpt,
        }
        return ("down" if change < 0 else "up"), source, excerpt

    terms = "|".join(re.escape(term) for term in chain["news_terms"])
    pattern = re.compile(rf"(?:{terms})[^，。！？；\n]{{0,18}}?{_MOVE}{_PERCENT}", re.IGNORECASE)
    report = _find_report(
        [s for s in stories if _current(s.get("published_at"), as_of, days=3)],
        pattern,
        threshold=COMMODITY_MOVE_THRESHOLD,
    )
    if report:
        source, match = report
        return ("down" if match["move"] in _DOWN else "up"), source, match[0]
    return None


def _observations(
    definitions: dict[str, Any], stories: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    observed = {}
    for key, definition in definitions.items():
        payer = definition.get("payer_match")
        expr = payer or rf"{definition['match']}\s*{_MOVE}{_PERCENT}"
        found = _find_report(stories, re.compile(expr))
        if not found:
            continue
        evidence, match = found
        observed[key] = {
            "name": definition["name"],
            "evidence": evidence,
            "quote": match[0],
            "reading": match["payer"] if payer else f"{match['move']}{match['pct']}%",
        }
    return observed


def _basket(node: dict[str, Any], today: date, symbols: dict[str, str]) -> dict[str, Any] | None:
    applicable = [
        version
        for version in node.get("baskets", [])
        if date.fromisoformat(version["effective_from"]) <= today
    ]
    if not applicable:
        return None
    version = max(applicable, key=lambda row: row["effective_from"])
    members = [{**item, "name": symbols[item["symbol"]]} for item in version["members"]]
    if any(item["weight"] <= 0 for item in members) or not math.isclose(
        sum(item["weight"] for item in members), 1.0
    ):
        raise ValueError(f"产业链篮子权重无效：{node['id']}")
    return {"effective_from": version["effective_from"], "members": members}


def _market_symbols(root: Path) -> dict[str, str]:
    sectors = _load(root / "config/market/sectors.yaml")["domains"]
    symbols = {
        item["symbol"]: item["name"] for domain in sectors.values() for item in domain["companies"]
    }
    for domain in sectors.values():
        for symbol in [domain["etfs"]["primary"], *domain["etfs"]["alternatives"]]:
            symbols[symbol] = f"{domain['name']} ETF"
    return symbols


def active_baskets(root: Path, today: date) -> dict[str, dict[str, Any]]:
    path = root / "config/industry_chains.yaml"
    if not path.exists():
        return {}
    symbols = _market_symbols(root)
    return {
        f"{chain['id']}:{node['id']}": basket
        for chain in _load(path)["chains"]
        for node in chain["nodes"]
        if (basket := _basket(node, today, symbols)) is not None
    }


def assess_chains(
    root: Path, markets: list[dict[str, Any]], events: list[dict[str, Any]], *, as_of: datetime
) -> list[dict[str, Any]]:
    path = root / "config/industry_chains.yaml"
    if not path.exists():
        return []
    today = as_of.date()
    catalog = _load(root / "config/news_sources.yaml")
    industries = {item["id"] for domain in catalog["domains"] for item in domain["industries"]}
    symbols = _market_symbols(root)
    pilots = pilot_commodities(root)
    current = sorted(
        (
            event
            for event in events
            if event.get("id")
            and event.get("title")
            and _current(event.get("published_at"), as_of, days=45)
        ),
        key=lambda event: event["published_at"],
        reverse=True,
    )
    reports = []
    for chain in _load(path)["chains"]:
        nodes = chain["nodes"]
        classes = {category for node in nodes for category in node["industries"]}
        if not classes <= industries:
            raise ValueError(f"未知二级行业：{sorted(classes - industries)}")
        symbol = pilots[chain["commodity"]]
        stories = [
            event for event in current if classes.intersection(event.get("industry_ids") or [])
        ]
        shock = _shock(chain, symbol, markets, stories, as_of)
        if shock is None:
            continue
        direction, source, quotation = shock
        definitions = chain["observations"]
        observed = _observations(definitions, stories)
        impacts = []
        for node in nodes:
            selected = {key: observed[key] for key in node["requires"] if key in observed}
            missing = [definitions[key]["name"] for key in node["requires"] if key not in selected]
            inference = node[direction]
            payer = (selected.get(node.get("payer")) or {}).get("reading")
            if payer in {"买方", "客户", "进口方"}:
                inference = "该报道所述安排由买方承担运费，出口方的直接海运费未必随之变化。"
            elif node.get("payer") and not payer:
                inference += " 运费承担方待验证。"
            demand = selected.get(node.get("demand")) or {}
            if any(demand.get("reading", "").startswith(word) for word in _DOWN):
                inference += " 已有报道显示下游需求走弱，成本或售价单边变化不足以推断利润改善。"
            references = {source["id"]: source}
            facts = [{"evidence_id": source["id"], "quote": quotation}]
            for item in selected.values():
                evidence = item["evidence"]
                references[evidence["id"]] = evidence
                fact = {"evidence_id": evidence["id"], "quote": item["quote"]}
                if fact not in facts:
                    facts.append(fact)
            impacts.append(
                {
                    "id": node["id"],
                    "name": node["name"],
                    "upstream": node.get("upstream"),
                    "industries": node["industries"],
                    "conditions": node["conditions"],
                    "profit_status": "待验证",
                    "facts": facts,
                    "inference": inference,
                    "observables": [definitions[key]["name"] for key in node["requires"]],
                    "observations": {item["name"]: item["reading"] for item in selected.values()},
                    "gaps": missing or ["同一企业同一时期的利润或毛利仍待核对"],
                    "evidence": list(references.values()),
                    "basket": _basket(node, today, symbols),
                    "basket_note": node.get("basket_note"),
                }
            )
        reports.append(
            {
                "id": chain["id"],
                "name": chain["name"],
                "symbol": symbol,
                "direction": direction,
                "shock": {"quote": quotation, "evidence": source},
                "nodes": impacts,
            }
        )
    return reports
