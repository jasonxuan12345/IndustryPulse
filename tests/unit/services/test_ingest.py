"""IngestService：归档 -> 构造观测 -> 原子发布，且绝不升格 / 绝不猜时点。"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from industrypulse.contracts import (
    TZ_SHANGHAI,
    AvailabilityBasis,
    EvidenceGrade,
    IndicatorSpec,
    IndustryNode,
    RawArtifact,
    ResearchMode,
    SeriesSpec,
    SourceDescriptor,
)
from industrypulse.contracts.pit import SkipReason, select_vintages_as_of
from industrypulse.contracts.sources import ReleasePolicy
from industrypulse.datahub.adapters import ParsedObservation, parse_profit
from industrypulse.repositories import DuckDBObservationRepository, PublicationError
from industrypulse.services import AsPublishedClaim, IngestError, IngestReport, IngestService

SERIES_ID = "cn.nbs.industry_c31.profit_ytd"
ROW = "黑色金属冶炼和压延加工业"
URL = "https://www.stats.gov.cn/sj/zxfb/202508/t20250827_1.html"


def page(value: str, *, publication: str | None = "2025-08-27 09:30:00") -> str:
    meta = f'<meta name="pubdate" content="{publication}">' if publication else ""
    return (
        f"<html><head>{meta}</head><body><table>"
        "<tr><th>行业</th><th>利润总额</th></tr>"
        f"<tr><td>{ROW}</td><td>{value}</td></tr>"
        "</table></body></html>"
    )


def parse(html: str, *, unit: str = "亿元", period: str = "2025-07") -> list[ParsedObservation]:
    return [parse_profit(html, period=period, row=ROW, column="利润总额", unit=unit)]


def artifact(content: bytes, *, retrieved_at: datetime | None = None) -> RawArtifact:
    digest = hashlib.sha256(content).hexdigest()
    return RawArtifact(
        artifact_id=digest,
        source_id="cn.nbs",
        uri="pending",
        sha256=digest,
        mime="text/html",
        byte_size=len(content),
        retrieved_at=retrieved_at or datetime(2025, 8, 28, 10, tzinfo=TZ_SHANGHAI),
        source_url=URL,
    )


def make_repository(
    tmp_path: Path, *, legal_status: str = "public_official"
) -> DuckDBObservationRepository:
    repository = DuckDBObservationRepository(tmp_path / "pulse.duckdb")
    repository.register_source(
        SourceDescriptor(
            source_id="cn.nbs",
            owner="S",
            url="https://www.stats.gov.cn/",
            data_fields=["profit_ytd"],
            frequency="M",
            revision_behavior="revised_later",
            legal_status=legal_status,
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
            description="测试用钢铁利润序列",
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


def ingest(
    repository: DuckDBObservationRepository,
    content: bytes,
    *,
    parsed: list[ParsedObservation] | None = None,
    retrieved_at: datetime | None = None,
    as_published: AsPublishedClaim | None = None,
) -> IngestReport:
    return IngestService(repository).ingest_document(
        artifact=artifact(content, retrieved_at=retrieved_at),
        content=content,
        parsed=parsed if parsed is not None else parse(content.decode("utf-8")),
        specs={SERIES_ID: repository.series_spec(SERIES_ID)},
        as_published=as_published,
    )


def test_publishes_vintage_with_conservative_value_grade(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    content = page("1580.4").encode()
    report = ingest(repository, content)

    assert report.created == 1 and report.reused == 0
    row = report.vintages[0]
    assert row.value == Decimal("1580.4")
    assert row.observation_start == date(2025, 7, 1)
    assert row.observation_end == date(2025, 7, 31)
    assert row.availability_basis == AvailabilityBasis.OBSERVED_PUBLICATION
    assert row.available_at == row.publication_at
    assert row.release_evidence == EvidenceGrade.A
    # 没有人工主张"同期原件"，数值证据只能是 C——不得自动升格
    assert row.value_evidence == EvidenceGrade.C
    assert row.vintage_id == 1
    assert row.artifact_id == hashlib.sha256(content).hexdigest()


def test_capture_claim_within_window_grades_a(tmp_path: Path) -> None:
    """只有提供同期捕获证据且落在 48h 窗口内，数值证据才可能到 A。"""
    repository = make_repository(tmp_path)
    report = ingest(
        repository,
        page("1580.4").encode(),
        as_published=AsPublishedClaim(method="capture", captured_at=None),
    )
    assert report.vintages[0].value_evidence == EvidenceGrade.A
    assert "as_published=capture" in report.vintages[0].notes


def test_capture_claim_outside_window_stays_c(tmp_path: Path) -> None:
    """抓取晚于发布 48h：正是历史回填的真实情况，必须停在 C。"""
    repository = make_repository(tmp_path)
    late = datetime(2025, 9, 30, tzinfo=TZ_SHANGHAI)
    report = ingest(
        repository,
        page("1580.4").encode(),
        retrieved_at=late,
        as_published=AsPublishedClaim(method="capture", captured_at=None),
    )
    assert report.vintages[0].value_evidence == EvidenceGrade.C
    assert "rejected" in report.vintages[0].notes


def test_reingest_same_bytes_is_idempotent(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    content = page("1580.4").encode()
    first = ingest(repository, content)
    second = ingest(repository, content)

    assert second.created == 0 and second.reused == 1
    assert first.digest == second.digest
    assert first.snapshot_id == second.snapshot_id
    assert len(repository.vintages_for(SERIES_ID, date(2025, 7, 1))) == 1


def test_revision_creates_new_vintage_and_keeps_old(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    ingest(repository, page("1580.4").encode())
    report = ingest(repository, page("1600.0").encode())

    rows = repository.vintages_for(SERIES_ID, date(2025, 7, 1))
    assert report.created == 1
    assert [row.vintage_id for row in rows] == [1, 2]
    assert rows[0].value == Decimal("1580.4")
    assert rows[1].value == Decimal("1600.0")


def test_missing_publication_falls_back_to_retrieval_time(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    report = ingest(repository, page("1580.4", publication=None).encode())
    row = report.vintages[0]

    assert row.availability_basis == AvailabilityBasis.RETRIEVAL_TIME
    assert row.release_evidence == EvidenceGrade.D
    assert row.publication_at is None
    assert row.available_at == artifact(b"x").retrieved_at

    # 没有可核验发布时点的行，STRICT 模式必须排除而不是"就地可用"
    selection = select_vintages_as_of(
        [row], as_of=datetime(2025, 9, 1, tzinfo=TZ_SHANGHAI), mode=ResearchMode.STRICT_PIT
    )
    assert selection.selected == []
    assert selection.skipped[0].reason == SkipReason.BASIS_NOT_ALLOWED


def test_declared_release_policy_gives_assumed_lag(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    spec = repository.series_spec(SERIES_ID).model_copy(
        update={
            "release_policy": ReleasePolicy(
                policy_version="test-v1", description="仅测试", release_lag_days=27, safety_days=2
            )
        }
    )
    report = IngestService(repository).ingest_document(
        artifact=artifact(
            page("1", publication=None).encode(),
            retrieved_at=datetime(2025, 10, 1, tzinfo=TZ_SHANGHAI),
        ),
        content=page("1", publication=None).encode(),
        parsed=parse(page("1", publication=None)),
        specs={SERIES_ID: spec},
    )
    row = report.vintages[0]
    assert row.availability_basis == AvailabilityBasis.ASSUMED_POLICY_LAG
    assert row.release_evidence == EvidenceGrade.C
    # 期末 2025-07-31 + 27 + 2 天 = 2025-08-29 09:30
    assert row.available_at.date() == date(2025, 8, 29)


def test_pending_license_blocks_archival_and_publishes_nothing(tmp_path: Path) -> None:
    repository = make_repository(tmp_path, legal_status="pending_review")
    with pytest.raises(PublicationError):
        ingest(repository, page("1580.4").encode())
    assert repository.vintages_for(SERIES_ID, date(2025, 7, 1)) == ()


def test_unit_mismatch_is_rejected_without_conversion(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    with pytest.raises(IngestError, match="单位不一致"):
        ingest(repository, page("1580.4").encode(), parsed=parse(page("1580.4"), unit="万元"))


def test_empty_parse_is_rejected(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    with pytest.raises(IngestError, match="空文档"):
        ingest(repository, page("1580.4").encode(), parsed=[])


def test_unregistered_series_is_rejected(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    with pytest.raises(IngestError, match="未登记 SeriesSpec"):
        IngestService(repository).ingest_document(
            artifact=artifact(page("1").encode()),
            content=page("1").encode(),
            parsed=parse(page("1")),
            specs={},
        )


def test_rearchive_same_bytes_keeps_first_capture_time(tmp_path: Path) -> None:
    """artifact_id 是内容哈希：同一字节重复归档必须幂等，不得因 retrieved_at 变化而冲突。"""
    repository = make_repository(tmp_path)
    content = page("1580.4").encode()
    first = repository.archive(artifact(content), content)
    second = repository.archive(
        artifact(content, retrieved_at=datetime(2026, 1, 1, tzinfo=TZ_SHANGHAI)), content
    )
    assert first.retrieved_at == second.retrieved_at
    assert first.uri == second.uri


def test_regrade_creates_new_snapshot_and_vintage(tmp_path: Path) -> None:
    """同一原件重新定级必须留下新版本，而不是撞上不可变快照冲突。"""
    repository = make_repository(tmp_path)
    content = page("1580.4").encode()
    conservative = ingest(repository, content)
    asserted = ingest(
        repository,
        content,
        as_published=AsPublishedClaim(method="capture", captured_at=None),
    )

    assert asserted.snapshot_id != conservative.snapshot_id
    assert asserted.created == 1
    row = asserted.vintages[0]
    assert row.vintage_id == 2
    assert row.value_evidence == EvidenceGrade.A
    # 旧版本仍在，且被新版本按 vintage_id tie-break 覆盖
    assert len(repository.vintages_for(SERIES_ID, date(2025, 7, 1))) == 2
