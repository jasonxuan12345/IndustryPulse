"""Reuse archived official financial tables for independent industry registries."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from industrypulse.contracts import ObservationVintage, RawArtifact
from industrypulse.core.html_tables import tables
from industrypulse.datahub.adapters import parse_table, resolve_table_column
from industrypulse.services.as_published import evaluate_claim, parse_claim
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
    "oil-gas",
    "agri-food",
    "food",
    "beverage",
    "refining",
    "building-materials",
    "transport-equipment",
    "instruments",
    "power",
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
        prefix = next(
            spec.series_id.removesuffix(".profit_ytd")
            for spec in registry.series
            if spec.series_id.endswith(".profit_ytd")
        )
        row_label = registry.nodes[0].names["zh"]
        specs = {spec.series_id: spec for spec in registry.series}
        financial_series = [sid for sid in specs if sid.startswith(prefix + ".")]
        existing = repository.all_vintages(financial_series)
        already = {(r.artifact_id, r.series_id) for r in existing}
        reused = 0
        for raw, period in originals:
            artifact = RawArtifact.model_validate_json(raw)
            matched = [r for r in existing if r.artifact_id == artifact.artifact_id]
            eligible = (
                bool(matched)
                and evaluate_claim(
                    parse_claim("auto"),
                    publication_at=matched[0].publication_at,
                    fallback_captured_at=artifact.retrieved_at,
                ).ok
            )
            upgraded = all(
                any(r.series_id == sid and r.value_evidence.value == "A" for r in matched)
                for sid in financial_series
            )
            if all((artifact.artifact_id, sid) in already for sid in financial_series) and (
                not eligible or upgraded
            ):
                reused += 1
                continue
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
                                        series_id=f"{prefix}.{suffix}_ytd"
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
            # Equal document counts may contain revised official originals.
            # Content identity prevents a count-only snapshot collision.
            from hashlib import sha256

            material = "|".join(sorted(item.artifact.sha256 for item in items))
            report = IngestService(repository, parser_version="industry-expand-v2").ingest_batch(
                items,
                snapshot_id="expand-v2-"
                + industry
                + "-"
                + sha256(material.encode()).hexdigest()[:16],
                as_published=parse_claim("auto"),
            )
            reports.append(
                {
                    "industry": industry,
                    "report": report.model_dump(mode="json"),
                    "skipped": failures,
                    "unchanged_documents": reused,
                }
            )
        else:
            reports.append(
                {
                    "industry": industry,
                    "status": "UP_TO_DATE" if reused else "NO_DATA",
                    "skipped": failures,
                    "unchanged_documents": reused,
                }
            )
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
