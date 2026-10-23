"""Reuse archived official financial tables for independent industry registries."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from industrypulse.contracts import ObservationVintage, RawArtifact
from industrypulse.core.html_tables import tables
from industrypulse.datahub.adapters import parse_table, resolve_table_column
from industrypulse.services.ingest import BatchItem, IngestService

if TYPE_CHECKING:
    from industrypulse.services.workbench import Workbench


# 同一张规模以上工业企业利润表里的行业。名称必须与公告行名一致。
FINANCIAL_INDUSTRIES = (
    "coal",
    "chemical",
    "nonferrous-mining",
    "nonferrous-smelting",
    "auto",
    "pharma",
    "electronics",
    "electrical",
    "general-equipment",
    "special-equipment",
)


def expand_industries(service: Workbench, options: dict[str, Any]) -> dict[str, Any]:
    repository = service.repository()
    with repository.database.transaction() as connection:
        originals = connection.execute(
            "SELECT DISTINCT r.payload, o.observation_start FROM raw_artifact r "
            "JOIN observation_vintage o "
            "ON json_extract_string(o.payload,'$.artifact_id')=r.artifact_id "
            "WHERE o.series_id='cn.nbs.industry_c31.profit_ytd'"
        ).fetchall()
    reports = []
    requested = tuple(options.get("industries") or FINANCIAL_INDUSTRIES)
    for industry in requested:
        registry = service.taxonomy(industry)
        for spec in registry.series:
            repository.register_series(spec)
        repository.register_taxonomy(registry.nodes, registry.indicators)
        items = []
        failures = []
        code = registry.nodes[0].code
        row_label = registry.nodes[0].names["zh"]
        specs = {spec.series_id: spec for spec in registry.series}
        for raw, period in originals:
            artifact = RawArtifact.model_validate_json(raw)
            content = Path(artifact.uri).read_bytes()
            html = content.decode("utf-8")
            parsed = []
            try:
                for index in range(1, len(tables(html)) + 1):
                    try:
                        candidate = []
                        for metric, suffix in (
                            ("营业收入", "revenue"),
                            ("营业成本", "cost"),
                            ("利润总额", "profit"),
                        ):
                            for sub in ("金额", "同比增长"):
                                column = resolve_table_column(
                                    html, table_index=index, metric=metric, sub=sub
                                )
                                candidate.append(
                                    parse_table(
                                        html,
                                        series_id=f"cn.nbs.industry_c{code}.{suffix}_ytd"
                                        + ("_yoy" if sub == "同比增长" else ""),
                                        period=period.strftime("%Y-%m"),
                                        row=row_label,
                                        column=column.label,
                                        unit=column.unit,
                                        is_ytd=True,
                                        population="规模以上工业企业",
                                        table_index=index,
                                    )
                                )
                        parsed = candidate
                        break
                    except ValueError:
                        continue
                if parsed:
                    items.append(
                        BatchItem(artifact=artifact, content=content, parsed=parsed, specs=specs)
                    )
                else:
                    failures.append({"period": str(period), "reason": "无法精确定位该行业财务行列"})
            except ValueError as exc:
                failures.append({"period": str(period), "reason": str(exc)})
        if items:
            report = IngestService(repository, parser_version="industry-expand-v1").ingest_batch(
                items, snapshot_id="expand-" + industry + "-" + str(len(items))
            )
            reports.append(
                {
                    "industry": industry,
                    "report": report.model_dump(mode="json"),
                    "skipped": failures,
                }
            )
        else:
            reports.append({"industry": industry, "status": "NO_DATA", "skipped": failures})
    with repository.database.transaction() as connection:
        rows = [
            ObservationVintage.model_validate_json(row[0])
            for row in connection.execute(
                "SELECT payload FROM observation_vintage "
                "ORDER BY series_id,observation_start,vintage_id"
            ).fetchall()
        ]
    from hashlib import sha256

    digest = sha256("".join(row.model_dump_json() for row in rows).encode()).hexdigest()
    snapshot = "all-industries-" + digest[:16]
    repository.publish(snapshot, rows)
    return {"industries": reports, "snapshot": snapshot, "rows": len(rows)}
