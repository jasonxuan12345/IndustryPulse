"""轻量级 AI 判断回看记录：记录当时的判断与位置分位，后续自动对比真实板块涨跌。"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from industrypulse.core import TZ_SHANGHAI, replace_file

ROOT = Path(__file__).resolve().parents[3]


class JudgmentHistory:
    def __init__(self, *, root: Path | None = None) -> None:
        base = root or Path(os.getenv("INDUSTRYPULSE_ROOT", str(ROOT)))
        self.data_dir = Path(os.getenv("INDUSTRYPULSE_AI_DIR", str(base / "data/ai")))
        self.path = self.data_dir / "history.json"

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return list(raw) if isinstance(raw, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(f".{uuid4().hex}.tmp")
        temp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        replace_file(temp, self.path)

    def record_run(
        self,
        report: dict[str, Any],
        radars_by_sector: dict[str, dict[str, Any]],
        sector_prices: dict[str, tuple[float | None, str | None]],
    ) -> list[dict[str, Any]]:
        """每次正式 AI 雷达分析生成时，为重点潜伏和高危预警各留一条极简快照。"""
        if report.get("mode") != "ai":
            return []
        existing = self._load()
        run_id = str(report.get("run_id") or "")
        created_at = str(report.get("generated_at") or datetime.now(TZ_SHANGHAI).isoformat())
        new_items: list[dict[str, Any]] = []

        for item in report.get("early_opportunities", []):
            sector = str(item.get("sector") or "")
            if not sector:
                continue
            radar = radars_by_sector.get(sector, {})
            price, price_date = sector_prices.get(sector, (None, None))
            new_items.append(
                {
                    "id": uuid4().hex[:12],
                    "run_id": run_id,
                    "created_at": created_at,
                    "sector": sector,
                    "sector_name": radar.get("name") or item.get("sector_name") or sector,
                    "stance": "LURKING",
                    "title": item.get("title") or "早期潜伏线索",
                    "thesis": item.get("butterfly_chain") or "",
                    "position_score_at_call": radar.get("position_score"),
                    "zone_at_call": radar.get("zone"),
                    "entry_price": price,
                    "entry_date": price_date,
                }
            )

        for item in report.get("high_risk_warnings", []):
            sector = str(item.get("sector") or "")
            if not sector:
                continue
            radar = radars_by_sector.get(sector, {})
            price, price_date = sector_prices.get(sector, (None, None))
            new_items.append(
                {
                    "id": uuid4().hex[:12],
                    "run_id": run_id,
                    "created_at": created_at,
                    "sector": sector,
                    "sector_name": radar.get("name") or item.get("sector_name") or sector,
                    "stance": "HIGH_RISK",
                    "title": item.get("title") or "高危离场警示",
                    "thesis": item.get("reason") or "",
                    "position_score_at_call": radar.get("position_score"),
                    "zone_at_call": radar.get("zone"),
                    "entry_price": price,
                    "entry_date": price_date,
                }
            )

        if not new_items:
            return []
        # 避免同一 run_id 重复写入，最多保留最近 200 条记录
        deduped = [r for r in existing if r.get("run_id") != run_id]
        merged = (new_items + deduped)[:200]
        self._save(merged)
        return new_items

    def list_with_outcomes(
        self,
        latest_prices: dict[str, tuple[float | None, str | None]],
    ) -> list[dict[str, Any]]:
        """对照当前最新板块收盘价，计算发出判断以来的真实涨跌幅。"""
        rows = self._load()
        now = datetime.now(TZ_SHANGHAI)
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            sector = str(item.get("sector") or "")
            entry_price = item.get("entry_price")
            latest_price, latest_date = latest_prices.get(sector, (None, None))
            actual_return: float | None = None
            if (
                isinstance(entry_price, (int, float))
                and entry_price > 0
                and isinstance(latest_price, (int, float))
                and latest_price > 0
            ):
                actual_return = round((latest_price / float(entry_price) - 1.0) * 100.0, 2)
            try:
                created_dt = datetime.fromisoformat(str(item["created_at"]))
                days_elapsed = max(0, (now.date() - created_dt.date()).days)
            except ValueError:
                days_elapsed = 0

            outcome_label = "观察中"
            if actual_return is not None and days_elapsed >= 3:
                if item.get("stance") == "LURKING":
                    if actual_return >= 3.0:
                        outcome_label = "方向命中（上涨）"
                    elif actual_return <= -3.0:
                        outcome_label = "判断失效（下跌）"
                    else:
                        outcome_label = "横盘震荡"
                elif item.get("stance") == "HIGH_RISK":
                    if actual_return <= -3.0:
                        outcome_label = "预警有效（回落）"
                    elif actual_return >= 5.0:
                        outcome_label = "踏空提醒（继续大涨）"
                    else:
                        outcome_label = "高位震荡"

            item["latest_price"] = latest_price
            item["latest_date"] = latest_date
            item["actual_return_pct"] = actual_return
            item["days_elapsed"] = days_elapsed
            item["outcome_label"] = outcome_label
            out.append(item)
        return out

    @staticmethod
    def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
        """统计历史判断的到期可验证样本数与胜率（仅统计已满 3 日且有收益结果的记录）。"""
        evaluated = [
            row
            for row in items
            if row.get("days_elapsed", 0) >= 3
            and isinstance(row.get("actual_return_pct"), (int, float))
        ]
        lurking = [row for row in evaluated if row.get("stance") == "LURKING"]
        high_risk = [row for row in evaluated if row.get("stance") == "HIGH_RISK"]
        lurking_hits = sum(1 for row in lurking if float(row["actual_return_pct"]) >= 3.0)
        high_risk_hits = sum(1 for row in high_risk if float(row["actual_return_pct"]) <= -3.0)
        total_hits = lurking_hits + high_risk_hits
        win_rate = round(total_hits / len(evaluated) * 100.0, 1) if evaluated else None
        return {
            "total_calls": len(items),
            "evaluated_calls": len(evaluated),
            "lurking_evaluated": len(lurking),
            "lurking_hits": lurking_hits,
            "high_risk_evaluated": len(high_risk),
            "high_risk_hits": high_risk_hits,
            "win_rate_pct": win_rate,
        }
