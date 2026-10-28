"""Bounded, integrity-checked projection of frozen research for the local UI."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from industrypulse.services import ledger

FILES = {
    "opportunity_queue.json",
    "evidence_packet.json",
    "research_review.json",
    "investment_memo.json",
    "daily_report.md",
}


def directory(root: Path, run_id: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", run_id):
        raise ValueError("研究运行标识无效")
    parent = (root / "artifacts/research-mvp").resolve()
    result = (parent / run_id).resolve()
    if result.parent != parent:
        raise ValueError("研究目录无效")
    return result


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def reports(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    runs, issues = [], []
    for path in (root / "artifacts/research-mvp").glob("*/manifest.json"):
        try:
            item = read(path)
            if item["run_id"] != path.parent.name:
                continue
            directory(root, item["run_id"])
            runs.append(
                {
                    key: item.get(key)
                    for key in (
                        "run_id",
                        "as_of",
                        "created_at",
                        "status",
                        "model_version",
                        "memo_count",
                    )
                }
            )
        except (OSError, ValueError, KeyError, TypeError):
            issues.append({"run_id": path.parent.name, "reason": "归档目录或清单无法读取"})
    runs.sort(key=lambda x: (str(x["as_of"]), str(x["created_at"])), reverse=True)
    return runs, issues


def report(root: Path, run_id: str = "") -> dict[str, Any]:
    runs, issues = reports(root)
    if not runs and not run_id:
        return {
            "status": "not_run",
            "runs": [],
            "items": [],
            "issues": issues,
            "reason": "尚未生成跨行业 AI 研究。运行一次后会显示机会、原文证据与观察条件。",
        }
    chosen = run_id or str(runs[0]["run_id"])
    folder = directory(root, chosen)
    if not (folder / "manifest.json").exists():
        raise FileNotFoundError("研究记录不存在或尚未完成")
    manifest = read(folder / "manifest.json")
    artifacts = {}
    for name in FILES - {"daily_report.md"}:
        value = read(folder / name)
        if ledger._hash(value) != manifest["hashes"].get(name):
            raise ValueError("研究文件完整性核验失败，请核对冻结归档或从备份恢复")
        artifacts[name] = value
    queue = artifacts["opportunity_queue.json"]
    opportunities = {o["opportunity_id"]: o for o in queue["opportunities"]}
    packets = {p["opportunity_id"]: p for p in artifacts["evidence_packet.json"]["packets"]}
    reviews = {r["opportunity_id"]: r for r in artifacts["research_review.json"]["reviews"]}
    items = []
    for memo in artifacts["investment_memo.json"]["memos"]:
        oid = memo["opportunity_id"]
        opportunity = opportunities.get(oid, {})
        packet, review = packets.get(oid, {}), reviews.get(oid, {})
        items.append(
            {
                "memo": memo,
                "opportunity": {k: v for k, v in opportunity.items() if k != "inputs"},
                "facts": packet.get("facts", []),
                "evidence_status": packet.get("status"),
                "review": {
                    k: review.get(k)
                    for k in ("status", "rounds", "calls_used", "data_quality", "tool_log")
                },
            }
        )
    return {
        "status": manifest["status"],
        "run_id": chosen,
        "as_of": manifest["as_of"],
        "created_at": manifest["created_at"],
        "model_version": manifest["model_version"],
        "scanned_sectors": queue["scanned_sectors"],
        "coverage": queue["coverage"],
        "candidate_count": queue["candidate_count"],
        "items": items,
        "runs": runs,
        "issues": issues,
        "origin_label": "已归档研究 · 非实时行情",
        "evidence_mode": "RECONSTRUCTED",
        "requires_human_review": True,
    }
