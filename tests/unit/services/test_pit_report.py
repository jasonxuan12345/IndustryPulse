"""多模式 as-of 对照报告：行级原因必须完整、模式差异必须可见。"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from pathlib import Path

import pytest

from industrypulse.contracts import (
    TZ_SHANGHAI,
    EvidenceGrade,
    IndicatorSpec,
    IndustryNode,
    RawArtifact,
    ResearchMode,
    SeriesSpec,
    SourceDescriptor,
)
from industrypulse.contracts.pit import SkipReason
from industrypulse.datahub.adapters import parse_profit
from industrypulse.repositories import DuckDBObservationRepository
from industrypulse.services import BatchItem, IngestService, compare_modes, format_table

SERIES_ID = "cn.nbs.industry_c31.profit_ytd"
ROW = "黑色金属冶炼和压延加工业"
URL = "https://www.stats.gov.cn/sj/zxfb/202508/t20250827_1960884.html"


def page(value: str, publication: str) -> str:
    return (
        f'<html><head><meta name="pubdate" content="{publication}"></head><body><table>'
        "<tr><th>行业</th><th>利润总额</th></tr>"
        f"<tr><td>{ROW}</td><td>{value}</td></tr>"
        "</table></body></html>"
    )


def _repository(tmp_path: Path) -> DuckDBObservationRepository:
    repository = DuckDBObservationRepository(tmp_path / "pulse.duckdb")
    repository.register_source(
        SourceDescriptor(
            source_id="cn.nbs",
            owner="S",
            url="https://www.stats.gov.cn/",
            data_fields=["profit_ytd"],
            frequency="M",
            revision_behavior="revised_later",
            legal_status="public_official",
            priority="S0",
        )
    )
    repository.register_series(
        SeriesSpec(
            series_id=SERIES_ID,
            source_id="cn.nbs",
            unit="亿元",
            frequency="M",
            revision_policy="revised_later",
            timestamp_precision="minute",
            description="测试用序列",
        )
    )
    repository.register_taxonomy(
        [
            IndustryNode(
                industry_id="CN_NBS_C31",
                standard="GB/T 4754-2017",
                code="31",
                names={"zh": "钢铁"},
                valid_from=date(2017, 10, 1),
            )
        ],
        [
            IndicatorSpec(
                indicator_id="steel_profit_ytd",
                series_ids=[SERIES_ID],
                dimension="profitability",
                economic_direction="positive",
                level="CORE",
                industry_ids=["CN_NBS_C31"],
                role="label",
                valid_from=date(2017, 10, 1),
                description="测试用指标",
            )
        ],
    )
    return repository


def _batch_item(content: bytes, period: str, retrieved: datetime) -> BatchItem:
    digest = hashlib.sha256(content).hexdigest()
    parsed = parse_profit(
        content.decode("utf-8"), period=period, row=ROW, column="利润总额", unit="亿元"
    )
    return BatchItem(
        artifact=RawArtifact(
            artifact_id=digest,
            source_id="cn.nbs",
            uri="pending",
            sha256=digest,
            mime="text/html",
            byte_size=len(content),
            retrieved_at=retrieved,
            source_url=URL,
        ),
        content=content,
        parsed=[parsed],
        specs={SERIES_ID: None},  # type: ignore[dict-item]
    )


def _prepared(tmp_path: Path) -> DuckDBObservationRepository:
    repository = _repository(tmp_path)
    spec = repository.series_spec(SERIES_ID)
    retrieved = datetime(2026, 10, 1, tzinfo=TZ_SHANGHAI)
    items = []
    for value, period, publication in (
        ("643.6", "2025-07", "2025-08-27 09:30:00"),
        ("1115.0", "2025-11", "2025-12-27 09:30:00"),
    ):
        content = page(value, publication).encode()
        item = _batch_item(content, period, retrieved)
        items.append(
            BatchItem(
                artifact=item.artifact,
                content=item.content,
                parsed=item.parsed,
                specs={SERIES_ID: spec},
            )
        )
    IngestService(repository).ingest_batch(items, snapshot_id="frozen-1")
    return repository


def test_mode_difference_is_explicit(tmp_path: Path) -> None:
    repository = _prepared(tmp_path)
    points = [
        datetime(2025, 8, 27, 9, 29, tzinfo=TZ_SHANGHAI),
        datetime(2025, 8, 27, 9, 30, tzinfo=TZ_SHANGHAI),
        datetime(2025, 12, 27, 9, 30, tzinfo=TZ_SHANGHAI),
    ]
    comparison = compare_modes(repository, [SERIES_ID], points, snapshot_id="frozen-1")

    by_point = {(summary.as_of, summary.mode): summary for summary in comparison.summaries}
    strict_at_release = by_point[(points[1], ResearchMode.STRICT_PIT)]
    recon_at_release = by_point[(points[1], ResearchMode.RECONSTRUCTED_PIT)]
    # 未主张"同期原件"-> 数值证据 C -> STRICT 必须排除，RECONSTRUCTED 才可用
    assert strict_at_release.selected_rows == 0
    assert recon_at_release.selected_rows == 1
    assert strict_at_release.coverage == 0.0

    recon_later = by_point[(points[2], ResearchMode.RECONSTRUCTED_PIT)]
    assert recon_later.selected_rows == 2

    # 证据门槛先于时点判定：STRICT 在任何时点都因数值证据不足被排除，
    # 因此"尚未发布"要在一个证据可接受的模式下观察。
    before = [
        row
        for row in comparison.rows
        if row.as_of == points[0] and row.mode == ResearchMode.RECONSTRUCTED_PIT
    ]
    assert before and all(row.reason == SkipReason.FUTURE_RELEASE for row in before)

    strict_rows = [
        row
        for row in comparison.rows
        if row.mode == ResearchMode.STRICT_PIT and row.status == "skipped"
    ]
    assert all(row.reason == SkipReason.INSUFFICIENT_VALUE_EVIDENCE for row in strict_rows)
    assert {row.value_evidence for row in strict_rows} == {EvidenceGrade.C}


def test_table_renders_reasons_and_coverage(tmp_path: Path) -> None:
    repository = _prepared(tmp_path)
    comparison = compare_modes(
        repository,
        [SERIES_ID],
        [datetime(2025, 8, 27, 9, 30, tzinfo=TZ_SHANGHAI)],
        snapshot_id="frozen-1",
    )
    table = format_table(comparison)
    assert "snapshot=frozen-1" in table
    assert "insufficient_value_evidence" in table
    assert "SELECTED" in table
    assert "reconstructed_pit" in table and "strict_pit" in table


def test_invalid_inputs_are_rejected(tmp_path: Path) -> None:
    repository = _prepared(tmp_path)
    point = datetime(2025, 8, 27, 9, 30, tzinfo=TZ_SHANGHAI)
    with pytest.raises(ValueError, match="series_id"):
        compare_modes(repository, [], [point], snapshot_id="frozen-1")
    with pytest.raises(ValueError, match="as_of"):
        compare_modes(repository, [SERIES_ID], [], snapshot_id="frozen-1")
    with pytest.raises(ValueError, match="时区"):
        compare_modes(repository, [SERIES_ID], [datetime(2025, 8, 27)], snapshot_id="frozen-1")
    with pytest.raises(ValueError, match="冻结快照"):
        compare_modes(repository, [SERIES_ID], [point])
    with pytest.raises(ValueError, match="未注册|unregistered"):
        compare_modes(repository, [SERIES_ID], [point], snapshot_id="absent")
