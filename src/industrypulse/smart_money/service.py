"""国内外知名投资机构与投资人趋势跟踪服务。

包含：
1. 机构名录加载（config/smart_money.yaml）
2. 公开 SEC EDGAR 13F 两季持仓对比与修订处理
3. 本地持仓摘录、公开观点与企业资本开支记录
4. 按同一报告期、行业和不同机构汇总持仓方向
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4
from xml.etree.ElementTree import ParseError

import httpx
import yaml
from pydantic import BaseModel, Field

from industrypulse.core import TZ_SHANGHAI, replace_file
from industrypulse.smart_money.sec13f import (
    compare_holdings,
    parse_submission,
    previous_quarter,
    quarter_holdings,
    recent_filings,
)

ROOT = Path(__file__).resolve().parents[3]


class SmartMoneyEntryInput(BaseModel):
    institution_id: str = Field(min_length=1, max_length=64)
    sector: str = Field(min_length=1, max_length=64)
    direction: Literal["INCREASE", "REDUCE", "HOLD", "VIEW"] = "VIEW"
    kind: Literal["holding", "view", "capex"] | None = None
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    disclosed_at: date
    source_url: str = ""
    lag_note: str = ""


class SmartMoneyService:
    """同步申报持仓，保留独立的手工记录并汇总机构方向。"""

    def __init__(self, *, root: Path | None = None) -> None:
        base = root or Path(os.getenv("INDUSTRYPULSE_ROOT", str(ROOT)))
        self.root = base
        self.config_path = base / "config/smart_money.yaml"
        self.data_dir = Path(
            os.getenv("INDUSTRYPULSE_SMART_MONEY_DIR", str(base / "data/smart-money"))
        )
        self.entries_path = self.data_dir / "entries.json"
        self.filings_path = self.data_dir / "filings.json"
        self._sync_lock = threading.Lock()

    def _config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}
        return cast(dict[str, Any], yaml.safe_load(self.config_path.read_text(encoding="utf-8")))

    def institutions(self) -> list[dict[str, Any]]:
        return list(self._config().get("institutions") or [])

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{uuid4().hex}.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        replace_file(temp, path)

    def entries(self) -> list[dict[str, Any]]:
        rows = list(self._read_json(self.entries_path, []))
        today = datetime.now(TZ_SHANGHAI).date()
        inst_map = {item["id"]: item for item in self.institutions()}
        enriched: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            inst = inst_map.get(item.get("institution_id", ""), {})
            if not item.get("kind"):
                # Legacy notes have no verified holding source or explicit category.
                item["kind"] = (
                    "capex" if item.get("institution_id") == "industrial_capex" else "view"
                )
            item["institution_name"] = inst.get("name") or item.get("institution_id")
            item["region"] = inst.get("region") or "OTHER"
            try:
                disclosed = date.fromisoformat(str(item["disclosed_at"]))
                lag = max(0, (today - disclosed).days)
            except ValueError:
                lag = int(inst.get("disclosure_lag_days") or 30)
            base_lag = int(inst.get("disclosure_lag_days") or 0)
            effective_lag = max(lag, base_lag)
            item["days_since_disclosure"] = lag
            item["lag_warning"] = (
                f"披露已过去 {lag} 天（该类机构常规滞后约 {base_lag} 天），仅作中长期方向校验"
                if effective_lag >= 21
                else f"近期披露（距今 {lag} 天）"
            )
            enriched.append(item)
        enriched.sort(key=lambda x: str(x.get("disclosed_at", "")), reverse=True)
        return enriched

    def add_entry(self, payload: SmartMoneyEntryInput) -> dict[str, Any]:
        inst_ids = {item["id"] for item in self.institutions()}
        if inst_ids and payload.institution_id not in inst_ids:
            raise ValueError(f"未知机构 ID: {payload.institution_id}")
        rows = list(self._read_json(self.entries_path, []))
        record = {
            "id": uuid4().hex[:12],
            "institution_id": payload.institution_id,
            "sector": payload.sector,
            "direction": payload.direction,
            "kind": payload.kind
            or ("capex" if payload.institution_id == "industrial_capex" else "view"),
            "title": payload.title.strip(),
            "summary": payload.summary.strip(),
            "disclosed_at": payload.disclosed_at.isoformat(),
            "source_url": payload.source_url.strip(),
            "lag_note": payload.lag_note.strip(),
            "created_at": datetime.now(TZ_SHANGHAI).isoformat(),
        }
        rows.append(record)
        self._write_json(self.entries_path, rows)
        return next(item for item in self.entries() if item["id"] == record["id"])

    def parse_and_add(self, text: str) -> dict[str, Any]:
        """用当前默认大模型从一段季报/13F/机构新闻文本中自动提取结构化条目并入库。"""
        from industrypulse.ai.gateway import complete_json, load_connection

        conn = load_connection()
        if conn is None or not conn.enabled or not conn.model:
            raise ValueError("请先在设置中配置并启用大模型 API，或使用上方表单手动录入")
        inst_list = [{"id": item["id"], "name": item["name"]} for item in self.institutions()]
        sectors = [
            "semiconductor",
            "ai",
            "energy",
            "steel",
            "health",
            "metals",
            "chemicals",
            "auto",
            "digital",
            "consumer",
            "finance",
            "macro",
        ]
        prompt = json.dumps(
            {
                "text": text[:6000],
                "available_institutions": inst_list,
                "available_sectors": sectors,
            },
            ensure_ascii=False,
        )
        system = (
            "你是机构动向结构化提取助手。从用户粘贴的机构季报、13F新闻或大佬观点中提取 1 条记录。\n"
            "必须返回严格 JSON：\n"
            "{\n"
            '  "institution_id": "从 available_institutions 里选明确匹配的 id，未知填空",\n'
            '  "sector": "从 available_sectors 里选最匹配的 id",\n'
            '  "direction": "INCREASE|REDUCE|HOLD|VIEW",\n'
            '  "kind": "holding（持仓摘录）|view（公开观点）|capex（企业资本开支）",\n'
            '  "title": "不超过40字的标题",\n'
            '  "summary": "核心持仓变动或观点要点（不超过200字）",\n'
            '  "disclosed_at": "YYYY-MM-DD（找不到则填空）"\n'
            "}"
        )
        parsed = complete_json(conn, prompt, system=system)
        inst_ids = {item["id"] for item in self.institutions()}
        inst_id = str(parsed.get("institution_id") or "")
        if inst_id not in inst_ids:
            raise ValueError("无法确认披露机构，请手动录入")
        sec = str(parsed.get("sector") or "macro")
        if sec not in sectors:
            sec = "macro"
        direction = str(parsed.get("direction") or "VIEW")
        if direction not in {"INCREASE", "REDUCE", "HOLD", "VIEW"}:
            direction = "VIEW"
        try:
            disclosed = date.fromisoformat(str(parsed.get("disclosed_at") or "")[:10])
        except ValueError as exc:
            raise ValueError("无法确认披露日期，请手动录入") from exc
        kind = parsed.get("kind")
        return self.add_entry(
            SmartMoneyEntryInput(
                institution_id=inst_id,
                sector=sec,
                direction=cast(Literal["INCREASE", "REDUCE", "HOLD", "VIEW"], direction),
                kind=kind if kind in {"holding", "view", "capex"} else None,
                title=str(parsed.get("title") or "机构动向摘要")[:180],
                summary=str(parsed.get("summary") or text[:200])[:1800],
                disclosed_at=disclosed,
            )
        )

    def delete_entry(self, entry_id: str) -> bool:
        rows = list(self._read_json(self.entries_path, []))
        filtered = [row for row in rows if row.get("id") != entry_id]
        if len(filtered) == len(rows):
            return False
        self._write_json(self.entries_path, filtered)
        return True

    def sync_sec_filings(self, *, timeout: float = 10.0) -> dict[str, Any]:
        """同步有 CIK 的机构，并保存最近两季可比较的披露持仓。"""
        with self._sync_lock:
            return self._sync_filings(timeout)

    def _sync_filings(self, timeout: float) -> dict[str, Any]:
        headers = {
            "User-Agent": os.getenv(
                "INDUSTRYPULSE_SEC_USER_AGENT", "IndustryPulse/2.0 (local-research-workbench)"
            )
        }
        cached = self._read_json(self.filings_path, {})
        filings: dict[str, Any] = dict(cached.get("filings") or {})
        comparisons: dict[str, Any] = dict(cached.get("comparisons") or {})
        errors: dict[str, str] = {}
        issuers = self._config().get("cusip_map") or {}
        synced_at = datetime.now(TZ_SHANGHAI).isoformat()
        with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:

            def load(filing: dict[str, Any]) -> dict[str, Any]:
                path = self.data_dir / "13f" / f"{filing['accessionNumber']}.json"
                document = self._read_json(path, None)
                if document is not None:
                    return cast(dict[str, Any], document)
                response = client.get(filing["submission_url"])
                response.raise_for_status()
                document = parse_submission(response.text, filing["form"])
                self._write_json(path, {**filing, **document})
                return document

            for inst in self.institutions():
                cik = str(inst.get("cik") or "").strip()
                if not cik:
                    continue
                iid = inst["id"]
                try:
                    resp = client.get(f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json")
                    resp.raise_for_status()
                    versions = recent_filings(cik, resp.json().get("filings", {}).get("recent", {}))
                    if not versions:
                        raise ValueError("SEC 无公开 13F 持仓申报")
                    period = max(f["report_date"] for f in versions)
                    prior_period = previous_quarter(period)
                    current_filings = [f for f in versions if f["report_date"] == period]
                    prior_filings = [f for f in versions if f["report_date"] == prior_period]
                    filings[iid] = {**current_filings[0], "synced_at": synced_at}
                    if not prior_filings:
                        raise ValueError(f"缺少连续上季 {prior_period} 的申报")
                    prior = quarter_holdings(prior_filings, load)
                    current = quarter_holdings(current_filings, load)
                    comparisons[iid] = {
                        "report_date": period,
                        "previous_report_date": prior_period,
                        "disclosed_at": current_filings[0]["filing_date"],
                        "previous_disclosed_at": prior_filings[0]["filing_date"],
                        "versions": current["versions"],
                        "previous_versions": prior["versions"],
                        "confidential_omitted": current["confidential_omitted"],
                        "previous_confidential_omitted": prior["confidential_omitted"],
                        "rows": compare_holdings(prior, current, issuers),
                    }
                except (
                    OSError,
                    ValueError,
                    TypeError,
                    LookupError,
                    ParseError,
                    httpx.HTTPError,
                ) as exc:
                    errors[iid] = f"{type(exc).__name__}: {str(exc)[:120]}"
        result = {
            "updated_at": synced_at,
            "filings": filings,
            "comparisons": comparisons,
            "errors": errors,
        }
        self._write_json(self.filings_path, result)
        return {"updated_at": synced_at, "filings": filings, "errors": errors}

    def _sector_summary(
        self, state: dict[str, Any], entries: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        comparisons = state.get("comparisons") or {}
        period = max(
            (
                f["report_date"]
                for f in (state.get("filings") or {}).values()
                if f.get("report_date")
            ),
            default=None,
        )
        directions: dict[str, dict[str, set[str]]] = {}
        for iid, comparison in comparisons.items():
            if iid in (state.get("errors") or {}) or comparison["report_date"] != period:
                continue
            for row in comparison["rows"]:
                if not row["sector"] or row["option"] != "NONE" or row["unit"] != "SH":
                    continue
                if row["status"] in {"NEW", "INCREASE", "REDUCE"}:
                    direction = "REDUCE" if row["status"] == "REDUCE" else "INCREASE"
                    directions.setdefault(row["sector"], {}).setdefault(iid, set()).add(direction)

        notes: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            notes.setdefault(entry["sector"], []).append(entry)
        summary = {}
        for sector in sorted(directions.keys() | notes.keys()):
            institutions = directions.get(sector, {})
            items = notes.get(sector, [])
            increasing = [iid for iid, sides in institutions.items() if sides == {"INCREASE"}]
            reducing = [iid for iid, sides in institutions.items() if sides == {"REDUCE"}]
            mixed = [iid for iid, sides in institutions.items() if len(sides) == 2]
            inc, red = len(increasing), len(reducing)
            if inc >= 3 and red == 0 and not mixed:
                signal, label = "CROWDED_CONSENSUS", "多家机构披露持仓增加（留意拥挤）"
            elif inc > red:
                signal, label = "ACCUMULATING", "披露持仓增加的机构较多"
            elif red > inc:
                signal, label = "REDUCING", "披露持仓减少的机构较多"
            else:
                signal, label = "OBSERVING", "披露持仓暂无一致方向"
            summary[sector] = {
                "signal": signal,
                "label": label,
                "report_date": period,
                "increase_count": inc,
                "reduce_count": red,
                "mixed_count": len(mixed),
                "view_count": sum(i["kind"] == "view" for i in items),
                "increase_institutions": increasing,
                "reduce_institutions": reducing,
                "mixed_institutions": mixed,
                "recent_notes": [
                    {
                        "institution": i["institution_name"],
                        "kind": i["kind"],
                        "title": i["title"],
                        "summary": i["summary"],
                        "disclosed_at": i["disclosed_at"],
                        "lag_warning": i["lag_warning"],
                    }
                    for i in items[:4]
                ],
            }
        return summary

    def sector_summary(self) -> dict[str, dict[str, Any]]:
        return self._sector_summary(self._read_json(self.filings_path, {}), self.entries())

    def holdings(self, sector: str) -> dict[str, Any]:
        state = self._read_json(self.filings_path, {})
        institutions = {item["id"]: item["name"] for item in self.institutions()}
        portfolios = []
        for iid, comparison in (state.get("comparisons") or {}).items():
            rows = [r for r in comparison["rows"] if (r["sector"] or "unmapped") == sector]
            if rows:
                rows.sort(key=lambda row: (row["status"] == "HOLD", -(row["weight_pct"] or 0)))
                portfolios.append(
                    {
                        **comparison,
                        "institution_id": iid,
                        "institution_name": institutions.get(iid, iid),
                        "sync_error": (state.get("errors") or {}).get(iid),
                        "rows": rows,
                    }
                )
        return {"sector": sector, "institutions": portfolios}

    def overview(self) -> dict[str, Any]:
        filings_state = dict(self._read_json(self.filings_path, {}))
        filings = dict(filings_state.get("filings") or {})
        comparisons = filings_state.get("comparisons") or {}
        entries = self.entries()
        insts = []
        for inst in self.institutions():
            compared = comparisons.get(inst["id"]) or {}
            insts.append(
                {
                    **inst,
                    "latest_filing": filings.get(inst["id"]),
                    "holdings_report_date": compared.get("report_date"),
                    "holdings_previous_report_date": compared.get("previous_report_date"),
                }
            )
        coverage = []
        for channel in self._config().get("data_channels") or []:
            if channel["id"] == "sec_13f":
                periods = sorted(
                    {
                        c[key]
                        for c in comparisons.values()
                        for key in ("previous_report_date", "report_date")
                    }
                )
            else:
                periods = [
                    entry["disclosed_at"]
                    for entry in entries
                    if entry["institution_id"] == channel["id"]
                    and entry["kind"] == channel.get("kind")
                ][::-1]
            coverage.append(
                {
                    **channel,
                    "covered_from": periods[0] if periods else None,
                    "covered_to": periods[-1] if periods else None,
                }
            )
        return {
            "institutions": insts,
            "entries": entries,
            "sector_summary": self._sector_summary(filings_state, entries),
            "holding_sectors": sorted(
                {
                    row["sector"] or "unmapped"
                    for comparison in comparisons.values()
                    for row in comparison["rows"]
                }
            ),
            "coverage": coverage,
            "last_sync_at": filings_state.get("updated_at"),
            "sync_errors": filings_state.get("errors") or {},
        }
