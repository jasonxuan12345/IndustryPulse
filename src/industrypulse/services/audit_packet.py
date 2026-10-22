"""Prepare an independently reviewable 30-document extraction audit packet."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from industrypulse.services.workbench import Workbench


def prepare(service: Workbench) -> dict[str, Any]:
    repository = service.repository()
    with repository.database.transaction() as connection:
        rows = connection.execute(
            "SELECT payload FROM observation_vintage "
            "WHERE series_id='cn.nbs.industry_c31.profit_ytd' ORDER BY observation_start"
        ).fetchall()
    by_document: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = json.loads(row[0])
        by_document.setdefault(item["artifact_id"], item)
    documents = list(by_document.values())
    stride = max(1, len(documents) // 30)
    selected = documents[::stride][:30]
    with repository.database.transaction() as connection:
        raw = {
            item[0]: json.loads(item[1])
            for item in connection.execute(
                "SELECT artifact_id,payload FROM raw_artifact"
            ).fetchall()
        }
    return {
        "status": "AWAITING_INDEPENDENT_LABELS",
        "documents": [
            {
                "artifact_id": item["artifact_id"],
                "sha256": raw[item["artifact_id"]]["sha256"],
                "source_url": item["source_url"],
                "period": item["observation_start"],
                "extracted_value": item["value"],
                "unit": item["unit"],
                "original_path": raw[item["artifact_id"]]["uri"],
                "expected_value": None,
                "reviewer": None,
            }
            for item in selected
        ],
        "message": (
            "实际原件待标注包；expected_value 需独立核对原件填写，不能把提取结果当作金标准"
        ),
    }


def evaluate(packet: dict[str, Any]) -> dict[str, Any]:
    import hashlib
    from decimal import Decimal

    results = []
    for item in packet["documents"]:
        if item.get("expected_value") is None or not item.get("reviewer"):
            results.append({"artifact_id": item["artifact_id"], "status": "UNREVIEWED"})
            continue
        digest = hashlib.sha256(Path(item["original_path"]).read_bytes()).hexdigest()
        matched = digest == item["sha256"] and Decimal(str(item["expected_value"])) == Decimal(
            str(item["extracted_value"])
        )
        results.append(
            {"artifact_id": item["artifact_id"], "status": "MATCH" if matched else "MISMATCH"}
        )
    reviewed = [item for item in results if item["status"] != "UNREVIEWED"]
    return {
        "reviewed": len(reviewed),
        "total": len(results),
        "matches": sum(item["status"] == "MATCH" for item in reviewed),
        "status": "COMPLETE" if len(reviewed) >= 30 else "AWAITING_INDEPENDENT_LABELS",
        "results": results,
    }
