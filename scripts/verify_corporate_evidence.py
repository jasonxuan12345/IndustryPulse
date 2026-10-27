"""Archive and verify the explicitly reviewed issuer notices (no inferred ratios)."""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import yaml
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    path = ROOT / "data/baseline/latest.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    events = yaml.safe_load(
        (ROOT / "config/research/corporate-evidence.yaml").read_text(encoding="utf-8")
    )["events"]
    results = []
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for event in events:
            item = {
                **event,
                "retrieved_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                "verified": False,
            }
            try:
                response = client.get(event["source_url"])
                response.raise_for_status()
                if not response.content.startswith(b"%PDF"):
                    raise ValueError("Expected PDF original")
                sha = hashlib.sha256(response.content).hexdigest()
                target = ROOT / "data/baseline/raw" / sha
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != sha:
                    raise ValueError("Archive hash mismatch")
                target.write_bytes(response.content)
                text = "\n".join(
                    p.extract_text() for p in PdfReader(io.BytesIO(response.content)).pages
                )
                compact = "".join(text.split())
                if not all("".join(s.split()) in compact for s in event["required_text"]):
                    raise ValueError("Expected identifying passages missing")
                item.update(
                    verified=True,
                    sha256=sha,
                    path=str(target.relative_to(ROOT)),
                    evidence=(
                        "issuer-authored notice; current capture, "
                        "not contemporary historical capture"
                    ),
                )
                (target.with_suffix(".txt")).write_text(text, encoding="utf-8")
            except Exception as exc:
                item["error"] = str(exc)[:300]
            results.append(item)
            print(
                item["asset_id"],
                item["price_day"],
                "verified" if item["verified"] else item["error"],
            )
    state["corporate_documents"] = results
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
