"""随手投喂交叉核验：把外部研报/纪要/文章结合本地新闻、量化位置和机构动向做二次验证。"""

from __future__ import annotations

import json
from typing import Any

from industrypulse.ai.gateway import ModelConnection, complete_json, load_connection
from industrypulse.core import now_shanghai


def _guess_sector(text: str, sectors: list[dict[str, Any]]) -> str:
    lowered = text.lower()
    for s in sectors:
        name = str(s.get("name") or "")
        sid = str(s.get("id") or "")
        if name and name.lower() in lowered:
            return sid
        if sid and sid.lower() in lowered:
            return sid
    return str(sectors[0]["id"]) if sectors else "macro"


def verify_lead(
    *,
    text: str,
    sector: str | None,
    radars: list[dict[str, Any]],
    stories: list[dict[str, Any]],
    smart_money_summary: dict[str, dict[str, Any]],
    connection: ModelConnection | None = None,
) -> dict[str, Any]:
    """用本地行业近况、量化位置和机构共识，检验外部投喂线索是不是早期认知差。"""
    target_sector = sector.strip() if sector and sector.strip() else _guess_sector(text, radars)
    radar = next((r for r in radars if r["id"] == target_sector), None)
    if radar is None:
        raise ValueError(f"未知行业 ID: {target_sector}")

    sector_stories = [
        {
            "title": s.get("title"),
            "source": s.get("source"),
            "published_at": s.get("published_at"),
            "summary": str(s.get("summary") or "")[:200],
        }
        for s in stories
        if s.get("sector") == target_sector
    ][:12]
    sm = smart_money_summary.get(target_sector, {})
    conn = connection if connection is not None else load_connection()
    checked_at = now_shanghai().isoformat()

    if conn is None or not conn.enabled or not conn.model:
        zone = radar.get("zone")
        if zone in {"HIGH_RISK", "CROWDED"}:
            verdict, label = "PRICED_IN", "市场位置已偏高（规则提示谨防接盘）"
        elif zone == "LURKING":
            verdict, label = "EARLY_ALPHA", "市场仍在低位（规则提示具备潜伏位置条件）"
        else:
            verdict, label = "CONFIRMING", "位置中性（未配置大模型，仅展示量化位置对照）"
        return {
            "checked_at": checked_at,
            "mode": "rule_fallback",
            "sector": target_sector,
            "sector_name": radar["name"],
            "verdict": verdict,
            "verdict_label": label,
            "reasoning": (
                f"当前未启用大模型 API。对照本地数据：{radar['name']} "
                f"处于【{radar.get('zone_label')}】，"
                f"近60日价格分位 {radar.get('price_percentile_60d')}%，"
                f"近20日涨幅 {radar.get('return_20d')}%，"
                f"近24小时相关新闻 {len(sector_stories)} 条。"
            ),
            "supporting_signals": [f"机构风向：{sm.get('label') or '暂无记录'}"],
            "counter_evidence": list(
                radar.get("risk_flags") or ["请配置大模型 API 后获取深度语义反证"]
            ),
            "action_hint": "请结合下方量化位置指标和近期同类新闻自行比对是否已充分发酵",
            "quant_snapshot": radar,
            "recent_sector_stories": sector_stories[:6],
        }

    prompt = json.dumps(
        {
            "user_lead": text[:6000],
            "target_sector": {"id": target_sector, "name": radar["name"]},
            "quant_position": radar,
            "smart_money": sm,
            "recent_local_news": sector_stories,
        },
        ensure_ascii=False,
    )
    system = (
        "你是客观严苛的投资线索交叉核验员。用户投喂了一段文章/研报/消息，"
        "请对照本地近期新闻流、当前量化位置与拥挤度、以及机构动向，判断这条线索究竟是：\n"
        "- EARLY_ALPHA（市场尚未充分定价的早期认知差/蝴蝶效应）\n"
        "- CONFIRMING（主升或产业验证进行中，但需关注位置）\n"
        "- PRICED_IN（近期新闻早已刷屏或股价已大涨拥挤，谨防接盘）\n"
        "- NOISE（缺乏可传导的硬逻辑或证伪风险极大）\n"
        "输出严格 JSON：\n"
        "{\n"
        '  "verdict": "EARLY_ALPHA|CONFIRMING|PRICED_IN|NOISE",\n'
        '  "verdict_label": "中文结论标签",\n'
        '  "reasoning": "核心交叉验证分析（说明是新变化还是炒冷饭，以及当前位置是否配合）",\n'
        '  "supporting_signals": ["支持依据1"],\n'
        '  "counter_evidence": ["反面风险或已定价迹象1"],\n'
        '  "action_hint": "一句实操观察建议"\n'
        "}"
    )
    res = complete_json(conn, prompt, system=system)
    raw_verdict = str(res.get("verdict") or "")
    verdict = (
        raw_verdict
        if raw_verdict in {"EARLY_ALPHA", "CONFIRMING", "PRICED_IN", "NOISE"}
        else "CONFIRMING"
    )
    return {
        "checked_at": checked_at,
        "mode": "ai",
        "model": conn.model,
        "sector": target_sector,
        "sector_name": radar["name"],
        "verdict": verdict,
        "verdict_label": str(res.get("verdict_label") or verdict),
        "reasoning": str(res.get("reasoning") or ""),
        "supporting_signals": list(res.get("supporting_signals") or [])[:5],
        "counter_evidence": list(res.get("counter_evidence") or [])[:5],
        "action_hint": str(res.get("action_hint") or ""),
        "quant_snapshot": radar,
        "recent_sector_stories": sector_stories[:6],
    }
