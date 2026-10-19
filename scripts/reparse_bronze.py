"""用已归档的 Bronze 原件重解析，补全缺失的发布时点等元数据。

背景：2021–2024 的国家统计局公告页没有 ``PubDate`` meta，早期提取器因此拿不到
发布时间，导致这些版本只能以 ``RETRIEVAL_TIME`` 入库（PIT 查询不可见）。
改进提取器后，本脚本从 Bronze 重解析**同一批原件**（不重新下载），
生成元数据更完整的版本，并发布为新快照。

用法::

    python scripts/reparse_bronze.py --database data/g2.duckdb --snapshot-id g2-steel-5y-v2
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from industrypulse.contracts.observation import RawArtifact
from industrypulse.repositories import DuckDBObservationRepository, PublicationError
from industrypulse.services import BatchItem, IngestService
from industrypulse.services.monthly_backfill import (
    parse_iva_document,
    parse_pmi_document,
    parse_profit_document,
)

PARSERS = {
    "profit": parse_profit_document,
    "pmi": parse_pmi_document,
    "iva": parse_iva_document,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--parser-version", default="g2-v2")
    args = parser.parse_args()

    repository = DuckDBObservationRepository(args.database)
    with repository.database.transaction() as conn:
        rows = conn.execute("SELECT payload FROM observation_vintage").fetchall()
    vintages = [json.loads(row[0]) for row in rows]

    groups: dict[str, list[dict[str, object]]] = collections.defaultdict(list)
    for item in vintages:
        groups[str(item["artifact_id"])].append(item)

    items: list[BatchItem] = []
    rebuilt = 0
    skipped: list[str] = []
    for artifact_id, members in groups.items():
        path = Path("data/bronze") / artifact_id[:2] / artifact_id
        if not path.is_file():
            skipped.append(f"{artifact_id[:12]} 原件缺失")
            continue
        content = path.read_bytes()
        text = content.decode("utf-8", "ignore")
        kind = None
        for candidate in PARSERS:
            sample = str(members[0]["series_id"])
            if candidate == "profit" and "industry_c31" in sample:
                kind = candidate
            elif candidate == "pmi" and ".pmi." in sample:
                kind = candidate
            elif candidate == "iva" and sample.endswith("industry.c31"):
                kind = candidate
            if kind:
                break
        if kind is None:
            skipped.append(f"{artifact_id[:12]} 无法判定类别")
            continue
        period = str(members[0]["observation_start"])[:7]
        try:
            parsed = PARSERS[kind](text, period)
            specs = {item.series_id: repository.series_spec(item.series_id) for item in parsed}
        except (ValueError, PublicationError) as exc:
            skipped.append(f"{kind} {period}: {str(exc)[:60]}")
            continue
        items.append(
            BatchItem(
                artifact=RawArtifact(
                    artifact_id=artifact_id,
                    source_id="cn.nbs",
                    uri=str(path),
                    sha256=artifact_id,
                    mime="text/html",
                    byte_size=len(content),
                    retrieved_at=members[0]["retrieved_at"],  # type: ignore[arg-type]
                    source_url=members[0].get("source_url"),  # type: ignore[arg-type]
                ),
                content=content,
                parsed=parsed,
                specs=specs,
            )
        )
        rebuilt += 1

    report = IngestService(repository, parser_version=args.parser_version).ingest_batch(
        items, snapshot_id=args.snapshot_id
    )
    print(
        json.dumps(
            {
                "snapshot_id": report.snapshot_id,
                "documents": rebuilt,
                "created": report.created,
                "reused": report.reused,
                "vintages": len(report.vintages),
                "skipped": skipped[:10],
                "skipped_count": len(skipped),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
