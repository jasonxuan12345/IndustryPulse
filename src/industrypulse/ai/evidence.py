"""Build typed source material and validate quotations in Butterfly judgments."""

from __future__ import annotations

import hashlib
from typing import Any

_SIGNAL_TYPES = {
    "sec_chain_filing": "sec_filing",
    "cninfo_announcement": "cninfo_ann",
    "twse_monthly_revenue": "twse_pulse",
    "commodity_surge": "market_metric",
    "commodity_crash": "market_metric",
    "overseas_lead": "market_metric",
}


def event_evidence(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"event:{event['id']}",
        "type": "news_event",
        "title": event["title"],
        "excerpt": f"{event['title']}\n{str(event.get('summary') or '')[:320]}".strip(),
        "url": event.get("url"),
        "source": event.get("source"),
        "date": event.get("published_at"),
        "reprints": max(0, len(event.get("sources") or []) - 1),
    }


def signal_evidence(sector: str, signal: dict[str, Any]) -> dict[str, Any] | None:
    kind = str(signal.get("kind") or "")
    if kind == "sec_chain_filing" and not (signal.get("content_verified") and signal.get("quote")):
        return None
    title = str(signal.get("title") or "")
    detail = str(signal.get("detail") or "")
    excerpt = str(signal.get("quote") or f"{title}\n{detail}").strip()
    if not excerpt:
        return None
    origin = f"{sector}|{kind}|{signal.get('url')}|{signal.get('date')}|{title}|{excerpt}"
    return {
        "id": "signal:" + hashlib.sha256(origin.encode()).hexdigest()[:20],
        "type": _SIGNAL_TYPES.get(kind, "announcement"),
        "title": title,
        "excerpt": excerpt,
        "url": signal.get("url"),
        "date": signal.get("date"),
        "source": signal.get("tag"),
    }


def market_evidence(radar: dict[str, Any], price: dict[str, Any]) -> dict[str, Any] | None:
    values = (
        ("参考收盘价", price.get("close"), ""),
        ("近5个交易日涨跌幅", radar.get("return_5d"), "%"),
        ("近20个交易日涨跌幅", radar.get("return_20d"), "%"),
        ("近20日相对沪深300", radar.get("relative_20d"), "%"),
        ("180日价格分位", radar.get("price_percentile_60d"), "%"),
        ("180日成交分位", radar.get("turnover_percentile_60d"), "%"),
        (
            "近24小时事件数" if radar.get("available") else "归并事件数",
            radar.get("news_count_24h"),
            "",
        ),
    )
    lines = [f"{label} {value}{unit}" for label, value, unit in values if value is not None]
    lines.extend(
        f"{proxy['name']}（{proxy['symbol']}）近20个交易日涨跌幅 {proxy['return_20d']}%"
        for proxy in radar.get("overseas_proxies") or []
        if proxy.get("name") and proxy.get("symbol") and proxy.get("return_20d") is not None
    )
    if not lines:
        return None
    return {
        "id": f"market:{radar['id']}",
        "type": "market_metric",
        "title": f"{radar['name']} 行情与事件快照",
        "excerpt": "\n".join(lines),
        "date": price.get("date"),
        "source": "行业雷达",
    }


def twse_evidence(radar: dict[str, Any]) -> dict[str, Any] | None:
    pulse = radar.get("twse_pulse") or {}
    median = pulse.get("median_yoy")
    if median is None:
        return None
    period = pulse.get("period")
    return {
        "id": f"twse:{radar['id']}",
        "type": "twse_pulse",
        "title": f"{radar['name']} 台股营收快照",
        "excerpt": f"{period} 月营收同比中位数 {median}%",
        "date": period,
        "source": "台湾证券交易所",
        "url": "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
    }


def audit_judgment(
    item: dict[str, Any], catalog: dict[str, dict[str, Any]], *, inference: str = ""
) -> dict[str, Any]:
    """A fact must quote the excerpt associated with an ID provided to the model."""
    facts = []
    evidence: dict[str, dict[str, Any]] = {}
    claimed = item.get("facts")
    rejected = False
    for fact in claimed[:8] if isinstance(claimed, list) else []:
        if not isinstance(fact, dict):
            rejected = True
            continue
        key = str(fact.get("evidence_id") or "")
        source = catalog.get(key)
        quote = " ".join(str(fact.get("quote") or "").split())
        if not source or not quote or quote not in " ".join(source["excerpt"].split()):
            rejected = True
            continue
        proof = {"evidence_id": key, "quote": quote}
        if proof not in facts:
            facts.append(proof)
        evidence[key] = source
    supplied_gaps = item.get("gaps")
    gaps = (
        [text.strip()[:300] for text in supplied_gaps[:6] if isinstance(text, str) and text.strip()]
        if isinstance(supplied_gaps, list)
        else []
    )
    if rejected:
        gaps.append("部分事实无法与所引用的原文或数据快照核对")
    if not facts:
        gaps.append("暂无与此判断对应的可核验事实")
    reasoning = item.get("inference")
    return {
        "facts": facts,
        "inference": reasoning.strip()
        if isinstance(reasoning, str) and reasoning.strip()
        else inference,
        "gaps": list(dict.fromkeys(gaps)),
        "evidence": list(evidence.values()),
    }
