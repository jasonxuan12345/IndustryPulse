"""国内外知名投资机构与投资人趋势跟踪服务。

包含：
1. 机构名录加载（config/smart_money.yaml）
2. 公开 SEC EDGAR 13F-HR 最新申报状态检查（需明确 User-Agent，失败优雅降级）
3. 本地机构持仓变动 / 公开观点笔记持久化与滞后天数自动计算
4. 按 12 行业聚合成共识方向与拥挤度提示，直接喂给 AI 推理
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

import httpx
import yaml
from pydantic import BaseModel, Field

from industrypulse.core import TZ_SHANGHAI, replace_file

ROOT = Path(__file__).resolve().parents[3]


class SmartMoneyEntryInput(BaseModel):
    institution_id: str = Field(min_length=1, max_length=64)
    sector: str = Field(min_length=1, max_length=64)
    direction: Literal["INCREASE", "REDUCE", "HOLD", "VIEW"] = "VIEW"
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    disclosed_at: date
    source_url: str = ""
    lag_note: str = ""


class SmartMoneyService:
    """机构动向加载、SEC 13F 状态同步与行业共识聚合。"""

    def __init__(self, *, root: Path | None = None) -> None:
        base = root or Path(os.getenv("INDUSTRYPULSE_ROOT", str(ROOT)))
        self.root = base
        self.config_path = base / "config/smart_money.yaml"
        self.data_dir = Path(
            os.getenv("INDUSTRYPULSE_SMART_MONEY_DIR", str(base / "data/smart-money"))
        )
        self.entries_path = self.data_dir / "entries.json"
        self.filings_path = self.data_dir / "filings.json"

    def institutions(self) -> list[dict[str, Any]]:
        if not self.config_path.exists():
            return []
        raw = cast(dict[str, Any], yaml.safe_load(self.config_path.read_text(encoding="utf-8")))
        return list(raw.get("institutions") or [])

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

    def delete_entry(self, entry_id: str) -> bool:
        rows = list(self._read_json(self.entries_path, []))
        filtered = [row for row in rows if row.get("id") != entry_id]
        if len(filtered) == len(rows):
            return False
        self._write_json(self.entries_path, filtered)
        return True

    def sync_sec_filings(self, *, timeout: float = 10.0) -> dict[str, Any]:
        """从 SEC EDGAR 公开接口获取配置了 CIK 机构的最新 13F-HR 申报日期。"""
        headers = {
            "User-Agent": os.getenv(
                "INDUSTRYPULSE_SEC_USER_AGENT", "IndustryPulse/2.0 (local-research-workbench)"
            )
        }
        cached = dict(self._read_json(self.filings_path, {"filings": {}, "errors": {}}))
        filings: dict[str, Any] = dict(cached.get("filings") or {})
        errors: dict[str, str] = {}
        with httpx.Client(timeout=timeout, headers=headers) as client:
            for inst in self.institutions():
                cik = str(inst.get("cik") or "").strip()
                if not cik:
                    continue
                padded = cik.zfill(10)
                url = f"https://data.sec.gov/submissions/CIK{padded}.json"
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    data = resp.json()
                    recent = data.get("filings", {}).get("recent", {})
                    forms = recent.get("form", [])
                    dates = recent.get("filingDate", [])
                    reports = recent.get("reportDate", [])
                    acc_nums = recent.get("accessionNumber", [])
                    found = None
                    for idx, form in enumerate(forms):
                        if str(form).startswith("13F-HR"):
                            acc = str(acc_nums[idx]).replace("-", "")
                            found = {
                                "form": form,
                                "filing_date": dates[idx] if idx < len(dates) else None,
                                "report_date": reports[idx] if idx < len(reports) else None,
                                "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}",
                                "synced_at": datetime.now(TZ_SHANGHAI).isoformat(),
                            }
                            break
                    if found:
                        filings[inst["id"]] = found
                except Exception as exc:  # noqa: BLE001 - 单机构失败不拖垮其他机构
                    errors[inst["id"]] = f"{type(exc).__name__}: {str(exc)[:120]}"
        result = {
            "updated_at": datetime.now(TZ_SHANGHAI).isoformat(),
            "filings": filings,
            "errors": errors,
        }
        self._write_json(self.filings_path, result)
        return result

    def sector_summary(self) -> dict[str, dict[str, Any]]:
        """按 12 行业汇总机构动向与共识风险。"""
        by_sector: dict[str, list[dict[str, Any]]] = {}
        for item in self.entries():
            by_sector.setdefault(str(item["sector"]), []).append(item)
        summary: dict[str, dict[str, Any]] = {}
        for sector_id, items in by_sector.items():
            inc = sum(1 for i in items if i.get("direction") == "INCREASE")
            red = sum(1 for i in items if i.get("direction") == "REDUCE")
            views = sum(1 for i in items if i.get("direction") in {"VIEW", "HOLD"})
            if inc >= 3 and red == 0:
                signal, label = "CROWDED_CONSENSUS", "机构高度一致增配（注意抱团拥挤）"
            elif inc > red:
                signal, label = "ACCUMULATING", "机构净增配/看好"
            elif red > inc:
                signal, label = "REDUCING", "机构净减配/分歧"
            else:
                signal, label = "OBSERVING", "机构观点跟踪中"
            summary[sector_id] = {
                "signal": signal,
                "label": label,
                "increase_count": inc,
                "reduce_count": red,
                "view_count": views,
                "recent_notes": [
                    {
                        "institution": i["institution_name"],
                        "direction": i["direction"],
                        "title": i["title"],
                        "summary": i["summary"],
                        "disclosed_at": i["disclosed_at"],
                        "lag_warning": i["lag_warning"],
                    }
                    for i in items[:4]
                ],
            }
        return summary

    def overview(self) -> dict[str, Any]:
        filings_state = dict(self._read_json(self.filings_path, {}))
        filings = dict(filings_state.get("filings") or {})
        insts = []
        for inst in self.institutions():
            insts.append({**inst, "latest_filing": filings.get(inst["id"])})
        return {
            "institutions": insts,
            "entries": self.entries(),
            "sector_summary": self.sector_summary(),
            "last_sync_at": filings_state.get("updated_at"),
            "sync_errors": filings_state.get("errors") or {},
        }
