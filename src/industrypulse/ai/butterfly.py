"""AI 蝴蝶效应引擎：并行扫描各行业弱信号，再结合量化位置与机构共识做交叉推演。"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from industrypulse.ai.gateway import ModelConnection, complete_json, load_connection
from industrypulse.core import TZ_SHANGHAI, replace_file

ROOT = Path(__file__).resolve().parents[3]


def _default_report_path(root: Path | None = None) -> Path:
    base = root or Path(os.getenv("INDUSTRYPULSE_ROOT", str(ROOT)))
    ai_dir = Path(os.getenv("INDUSTRYPULSE_AI_DIR", str(base / "data/ai")))
    return ai_dir / "radar-latest.json"


def load_latest_report(*, root: Path | None = None) -> dict[str, Any] | None:
    path = _default_report_path(root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return dict(data) if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_latest_report(report: dict[str, Any], *, root: Path | None = None) -> None:
    path = _default_report_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{uuid4().hex}.tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    replace_file(temp, path)


def _screen_sector_stories(
    sector_id: str,
    sector_name: str,
    stories: list[dict[str, Any]],
    radar: dict[str, Any],
    ask_json: Callable[[str, str], dict[str, Any]],
) -> dict[str, Any]:
    if not stories:
        return {"sector": sector_id, "sector_name": sector_name, "signals": []}
    compact_stories = [
        {
            "id": s.get("id"),
            "title": s.get("title"),
            "summary": str(s.get("summary") or "")[:240],
            "source": s.get("source"),
            "published_at": s.get("published_at"),
            "category": s.get("category"),
        }
        for s in stories[:18]
    ]
    prompt = json.dumps(
        {
            "sector": sector_id,
            "sector_name": sector_name,
            "quant_zone": radar.get("zone_label"),
            "position_score": radar.get("position_score"),
            "stories": compact_stories,
        },
        ensure_ascii=False,
    )
    system = (
        "你是行业弱信号初筛助手。从给定新闻中过滤常规水文和纯粹事后解释，"
        "只提取可能引发上游/下游蝴蝶效应的结构性变化"
        "（如政策微调、供给出清、价格拐点、海外龙头CapEx/技术突破）。"
        '返回严格 JSON：{"signals":[{"headline":"核心变化一句话","chain":"可能传导路径",'
        '"strength":"high|medium|low","story_ids":["新闻id"]}]}。'
        "最多 3 条；没有明显结构变化返回空数组。"
    )
    try:
        res = ask_json(prompt, system)
        valid_ids = {str(s.get("id")) for s in compact_stories if s.get("id")}
        signals = []
        for sig in list(res.get("signals") or [])[:3]:
            if not isinstance(sig, dict):
                continue
            sids = [str(i) for i in (sig.get("story_ids") or []) if str(i) in valid_ids]
            signals.append(
                {
                    "headline": str(sig.get("headline") or "")[:180],
                    "chain": str(sig.get("chain") or "")[:260],
                    "strength": sig.get("strength")
                    if sig.get("strength") in {"high", "medium", "low"}
                    else "medium",
                    "story_ids": sids,
                }
            )
        return {"sector": sector_id, "sector_name": sector_name, "signals": signals}
    except Exception as exc:  # noqa: BLE001
        return {
            "sector": sector_id,
            "sector_name": sector_name,
            "signals": [],
            "error": type(exc).__name__,
        }


def _rule_fallback_report(
    *,
    run_id: str,
    generated_at: str,
    radars: list[dict[str, Any]],
    stories_by_sector: dict[str, list[dict[str, Any]]],
    smart_money_summary: dict[str, dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    """未配置模型或离线时的结构化规则基线，保证前端象限和列表依然能看清线索。"""
    early: list[dict[str, Any]] = []
    high_risk: list[dict[str, Any]] = []
    watch: list[dict[str, Any]] = []

    for r in radars:
        sid = str(r["id"])
        zone = r.get("zone")
        s_stories = stories_by_sector.get(sid, [])
        story_ids = [str(s["id"]) for s in s_stories[:4] if s.get("id")]
        sm = smart_money_summary.get(sid, {})
        sm_note = sm.get("label") or "暂无机构动向记录"
        if zone == "LURKING":
            early.append(
                {
                    "sector": sid,
                    "sector_name": r["name"],
                    "title": f"{r['name']}：信息催化升温且市场处于低位",
                    "butterfly_chain": (
                        f"近24小时新闻 {r.get('news_count_24h', 0)} 篇，"
                        f"60日价格分位 {r.get('price_percentile_60d')}%，尚未出现明显拥挤。"
                        f"（当前为规则基线：{reason}，配置大模型后可生成完整产业链传导推演）"
                    ),
                    "quant_alignment": (
                        f"位置分 {r.get('position_score')} / 催化分 {r.get('catalyst_score')}"
                    ),
                    "smart_money_note": sm_note,
                    "disproof_signal": "若后续 5 个交易日无实质订单/价格跟随，则仅属短期消息噪音",
                    "evidence_story_ids": story_ids,
                }
            )
        elif zone == "HIGH_RISK":
            high_risk.append(
                {
                    "sector": sid,
                    "sector_name": r["name"],
                    "title": f"{r['name']}：进入高危拥挤区",
                    "reason": "；".join(r.get("risk_flags") or ["价格与成交拥挤度处于高位区间"]),
                    "risk_flags": list(r.get("risk_flags") or []),
                    "disproof_signal": "若成交回落后继续创出新高且基本面持续超预期，可解除警示",
                    "evidence_story_ids": story_ids[:2],
                }
            )
        elif zone in {"RISING", "CROWDED"}:
            watch.append(
                {
                    "sector": sid,
                    "sector_name": r["name"],
                    "status": r.get("zone_label"),
                    "note": "趋势已启动或偏拥挤，留意位置风险",
                }
            )

    return {
        "run_id": run_id,
        "generated_at": generated_at,
        "mode": "rule_fallback",
        "mode_note": reason,
        "macro_regime": {
            "summary": (
                f"当前未运行大模型推演（{reason}），以下完全基于真实行情位置分位与新闻热度生成。"
            ),
            "favored_styles": ["低位置+高催化板块"],
            "key_risks": ["高位放量拥挤板块回调风险"],
        },
        "sector_signals": [],
        "early_opportunities": early[:4],
        "high_risk_warnings": high_risk[:4],
        "watch_list": watch[:5],
    }


def run_butterfly_analysis(
    *,
    radars: list[dict[str, Any]],
    stories: list[dict[str, Any]],
    macro_items: list[dict[str, Any]],
    commodities: list[dict[str, Any]],
    smart_money_summary: dict[str, dict[str, Any]],
    connection: ModelConnection | None = None,
    use_ai: bool = True,
    root: Path | None = None,
    max_workers: int = 4,
) -> dict[str, Any]:
    """执行两阶段分析：并行行业新闻筛查 → 跨行业蝴蝶效应与量化位置联合推演。"""
    run_id = uuid4().hex[:12]
    generated_at = datetime.now(TZ_SHANGHAI).isoformat()
    stories_by_sector: dict[str, list[dict[str, Any]]] = {}
    for story in stories:
        sid = str(story.get("sector") or "")
        if sid:
            stories_by_sector.setdefault(sid, []).append(story)

    stories_by_id = {str(s["id"]): s for s in stories if s.get("id")}
    conn = connection if connection is not None else load_connection()

    if not use_ai or conn is None or not conn.enabled or not conn.model:
        reason = "已选择离线规则模式" if not use_ai else "尚未配置并启用大模型 API"
        fallback_report = _rule_fallback_report(
            run_id=run_id,
            generated_at=generated_at,
            radars=radars,
            stories_by_sector=stories_by_sector,
            smart_money_summary=smart_money_summary,
            reason=reason,
        )
        save_latest_report(fallback_report, root=root)
        return fallback_report

    def ask_json(prompt: str, system: str, timeout: float = 120.0) -> dict[str, Any]:
        return complete_json(conn, prompt, system=system, timeout=timeout)

    # 第一步：按行业并行筛选结构性弱信号
    radar_map = {str(r["id"]): r for r in radars}
    active_sectors = [r for r in radars if stories_by_sector.get(str(r["id"]))]
    sector_signals: list[dict[str, Any]] = []
    if active_sectors:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(active_sectors))) as pool:
            sector_signals = list(
                pool.map(
                    lambda r: _screen_sector_stories(
                        str(r["id"]),
                        str(r["name"]),
                        stories_by_sector.get(str(r["id"]), []),
                        r,
                        ask_json,
                    ),
                    active_sectors,
                )
            )

    # 第二步：全局交叉推演（结合宏观、大宗、量化位置分位、机构趋势）
    compact_macro = [
        {
            "name": m.get("name"),
            "value": m.get("value"),
            "previous": m.get("previous_value"),
            "unit": m.get("unit"),
            "period": m.get("period"),
            "status": m.get("status"),
        }
        for m in macro_items
        if m.get("value") is not None
    ][:18]

    compact_radars = [
        {
            "sector": r["id"],
            "name": r["name"],
            "zone": r.get("zone"),
            "zone_label": r.get("zone_label"),
            "position_score": r.get("position_score"),
            "catalyst_score": r.get("catalyst_score"),
            "return_5d": r.get("return_5d"),
            "return_20d": r.get("return_20d"),
            "relative_20d": r.get("relative_20d"),
            "price_percentile_60d": r.get("price_percentile_60d"),
            "turnover_percentile_60d": r.get("turnover_percentile_60d"),
            "risk_flags": r.get("risk_flags") or [],
            "smart_money": smart_money_summary.get(str(r["id"]), {}),
        }
        for r in radars
    ]

    compact_signals = [
        {
            "sector": s.get("sector"),
            "sector_name": s.get("sector_name"),
            "signals": [
                {
                    "headline": sig.get("headline"),
                    "chain": sig.get("chain"),
                    "strength": sig.get("strength"),
                    "story_ids": list(sig.get("story_ids") or [])[:2],
                }
                for sig in (s.get("signals") or [])[:3]
            ],
        }
        for s in sector_signals
        if s.get("signals")
    ]

    synthesis_prompt = json.dumps(
        {
            "as_of": generated_at,
            "macro_indicators": compact_macro[:12],
            "commodities": commodities[:8],
            "sector_quant_and_smart_money": compact_radars,
            "sector_weak_signals": compact_signals,
        },
        ensure_ascii=False,
    )
    synthesis_system = (
        "你是冷静务实的行业投研分析师。核心目标：通过行业新闻弱信号与宏观/大宗变动找出【蝴蝶效应传导链】，"
        "并结合量化位置分位（避免推荐已经暴涨拥挤的板块去接盘）与机构动向（注意机构披露有滞后，过度扎堆反而是高危），"
        "输出两类关键清单：1）早期潜伏机会（高催化且位置不高）；2）高危离场警示（位置极高/放量滞涨/拥挤或逻辑证伪）。\n"
        "严格约束：\n"
        "- 可以引用输入中的真实宏观、涨跌幅、分位数字，禁止编造未提供的精确目标价或收益率预测；\n"
        "- evidence_story_ids 只能填输入 sector_weak_signals 里出现的真实 story_ids；\n"
        "- 必须输出合法 JSON，结构如下：\n"
        "{\n"
        '  "macro_regime": {"summary": "宏观周期与主线判断", '
        '"favored_styles": ["风格1"], "key_risks": ["风险1"]},\n'
        '  "early_opportunities": [{\n'
        '    "sector": "领域id", "sector_name": "中文名", "title": "一句话标题",\n'
        '    "butterfly_chain": "微小变动 -> 传导路径 -> 受益点（可带入真实数据）",\n'
        '    "quant_alignment": "说明当前量化位置分位与拥挤度是否合适潜伏",\n'
        '    "smart_money_note": "机构风向参考（如有）",\n'
        '    "disproof_signal": "什么迹象出现说明逻辑错了",\n'
        '    "evidence_story_ids": ["真实新闻id"]\n'
        "  }],\n"
        '  "high_risk_warnings": [{\n'
        '    "sector": "领域id", "sector_name": "中文名", "title": "高危警示标题",\n'
        '    "reason": "拥挤/滞涨/估值或逻辑证伪原因",\n'
        '    "disproof_signal": "解除警示条件",\n'
        '    "evidence_story_ids": ["真实新闻id"]\n'
        "  }],\n"
        '  "watch_list": [{"sector": "领域id", "sector_name": "中文名", '
        '"status": "跟踪观察", "note": "一句话原因"}]\n'
        "}"
    )

    try:
        try:
            parsed = ask_json(synthesis_prompt, synthesis_system, 240.0)
        except ValueError:
            parsed = ask_json(synthesis_prompt, synthesis_system, 240.0)
    except Exception as exc:  # noqa: BLE001 - AI 请求异常时明确降级，保留弱信号结果
        fallback = _rule_fallback_report(
            run_id=run_id,
            generated_at=generated_at,
            radars=radars,
            stories_by_sector=stories_by_sector,
            smart_money_summary=smart_money_summary,
            reason=f"综合推演调用失败：{exc}",
        )
        fallback["sector_signals"] = sector_signals
        save_latest_report(fallback, root=root)
        return fallback

    def attach_evidence(items: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        cleaned: list[dict[str, Any]] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("sector") or "")
            raw_ids = [
                str(i) for i in (item.get("evidence_story_ids") or []) if str(i) in stories_by_id
            ]
            if not raw_ids and sid in stories_by_sector:
                raw_ids = [str(s["id"]) for s in stories_by_sector[sid][:3] if s.get("id")]
            evidence_stories = [
                {
                    "id": stories_by_id[i]["id"],
                    "title": stories_by_id[i]["title"],
                    "url": stories_by_id[i].get("url"),
                    "source": stories_by_id[i].get("source"),
                    "published_at": stories_by_id[i].get("published_at"),
                }
                for i in raw_ids
                if i in stories_by_id
            ]
            radar = radar_map.get(sid, {})
            cleaned.append(
                {
                    **item,
                    "sector_name": item.get("sector_name") or radar.get("name") or sid,
                    "evidence_story_ids": raw_ids,
                    "evidence_stories": evidence_stories,
                    "position_score": radar.get("position_score"),
                    "catalyst_score": radar.get("catalyst_score"),
                    "zone": radar.get("zone"),
                    "zone_label": radar.get("zone_label"),
                }
            )
        return cleaned

    report: dict[str, Any] = {
        "run_id": run_id,
        "generated_at": generated_at,
        "mode": "ai",
        "model": conn.model,
        "macro_regime": parsed.get("macro_regime")
        if isinstance(parsed.get("macro_regime"), dict)
        else {"summary": "模型未返回宏观摘要", "favored_styles": [], "key_risks": []},
        "sector_signals": sector_signals,
        "early_opportunities": attach_evidence(
            list(parsed.get("early_opportunities") or []), limit=5
        ),
        "high_risk_warnings": attach_evidence(
            list(parsed.get("high_risk_warnings") or []), limit=5
        ),
        "watch_list": list(parsed.get("watch_list") or [])[:6],
    }
    save_latest_report(report, root=root)
    return report
