import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from industrypulse.contracts.sources import LegalStatus, SourceStatus, TimestampPrecision
from industrypulse.datahub.adapters import parse_pmi, parse_profit, parse_tsf
from industrypulse.datahub.fetcher import Fetcher
from industrypulse.datahub.publication import extract_publication
from industrypulse.datahub.source_registry import SourceRegistry


def test_status_transitions_are_audited(tmp_path: Path) -> None:
    catalog = SourceRegistry.from_csv(Path("source_catalog.csv"))
    registry = SourceRegistry(list(catalog.sources.values()), tmp_path / "audit.jsonl")
    source_id = "src.nbs.profit"
    registry.set_permission(
        source_id, LegalStatus.PUBLIC_OFFICIAL, reason="checked", actor="tester"
    )
    assert len(registry.filter(status=SourceStatus.CANDIDATE)) == 18
    with pytest.raises(ValueError):
        registry.set_status(
            source_id, SourceStatus.ACTIVE, reason="missing acceptance", actor="tester"
        )
    registry.set_status(
        source_id,
        SourceStatus.ACTIVE,
        reason="synthetic acceptance",
        actor="tester",
        smoke_test_at=datetime.now(UTC),
    )
    assert len(registry.filter(status=SourceStatus.ACTIVE)) == 1
    assert len(registry.events) == 2
    assert json.loads((tmp_path / "audit.jsonl").read_text().splitlines()[1])["actor"] == "tester"
    registry.set_permission(source_id, LegalStatus.DENIED, reason="revoked")
    with pytest.raises(ValueError):
        registry.set_permission(
            source_id, LegalStatus.PUBLIC_OFFICIAL, reason="cannot bypass review"
        )


def test_metric_series_do_not_alias() -> None:
    html = (
        "<table><tr><th>指标</th><th>当月</th></tr><tr><td>新订单</td><td>50</td></tr>"
        "<tr><td>生产</td><td>51</td></tr></table>"
    )
    orders = parse_pmi(
        html, period="2025-03", row="新订单", column="当月", category="component", table_index=1
    )
    production = parse_pmi(
        html, period="2025-03", row="生产", column="当月", category="component", table_index=1
    )
    assert orders.series_id == "cn.nbs.pmi.new_orders" and orders.series_id != production.series_id
    args = dict(period="2025-03", row="新订单", column="当月", population="社融口径v1", unit="亿元")
    monthly = parse_tsf(
        html,
        period=args["period"],
        row=args["row"],
        column=args["column"],
        population=args["population"],
        unit=args["unit"],
        is_ytd=False,
    )
    ytd = parse_tsf(
        html,
        period=args["period"],
        row=args["row"],
        column=args["column"],
        population=args["population"],
        unit=args["unit"],
        is_ytd=True,
    )
    stock = parse_tsf(
        html,
        period=args["period"],
        row=args["row"],
        column=args["column"],
        population=args["population"],
        unit=args["unit"],
        is_ytd=False,
        kind="stock",
    )
    assert len({monthly.series_id, ytd.series_id, stock.series_id}) == 3
    with pytest.raises(ValueError):
        parse_profit(html, period="2025-03", row="有色金属", column="同比")
    with pytest.raises(ValueError):
        parse_profit(
            html,
            period="2025-03",
            row="黑色金属冶炼和压延加工业",
            column="利润总额 / 同比",
            unit="亿元",
        )


@pytest.mark.parametrize("etag", ['"v2"', 'W/"v1"'])
def test_resume_rejects_changed_version(tmp_path: Path, etag: str) -> None:
    catalog = SourceRegistry.from_csv(Path("source_catalog.csv"))
    catalog.set_permission("src.nbs.profit", LegalStatus.PUBLIC_OFFICIAL, reason="synthetic")
    url = "https://www.stats.gov.cn/doc"
    key = hashlib.sha256(url.encode()).hexdigest()
    (tmp_path / f"{key}.part").write_bytes(b"old")
    (tmp_path / f"{key}.resume.json").write_text(json.dumps({"etag": '"v1"'}))
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                206, content=b"new", headers={"Content-Range": "bytes 3-5/6", "ETag": etag}
            )
        )
    ) as client:
        with pytest.raises(ValueError, match="resume"):
            Fetcher(catalog, tmp_path, client).fetch("src.nbs.profit", url)
    assert not (tmp_path / hashlib.sha256(b"oldnew").hexdigest()).exists()


def test_archive_never_overwrites_corruption(tmp_path: Path) -> None:
    catalog = SourceRegistry.from_csv(Path("source_catalog.csv"))
    catalog.set_permission("src.nbs.profit", LegalStatus.PUBLIC_OFFICIAL, reason="synthetic")
    digest = hashlib.sha256(b"original").hexdigest()
    (tmp_path / digest).write_bytes(b"corrupt")
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"original"))
    ) as client:
        with pytest.raises(ValueError, match="checksum"):
            Fetcher(catalog, tmp_path, client).fetch(
                "src.nbs.profit", "https://www.stats.gov.cn/doc"
            )
    assert (tmp_path / digest).read_bytes() == b"corrupt"


@pytest.mark.parametrize(
    "date_text,precision",
    [
        ("2025-03-27", TimestampPrecision.DAY),
        ("2025-03-27T09:30+08:00", TimestampPrecision.MINUTE),
        ("2025-03-27T09:30:00+08:00", TimestampPrecision.SECOND),
    ],
)
def test_publication_metadata(date_text: str, precision: TimestampPrecision) -> None:
    timestamp, actual_precision = extract_publication(
        f'<meta content="{date_text}" name="pubdate">'
    )
    assert timestamp is not None and timestamp.tzinfo is not None
    assert actual_precision == precision


def test_publication_does_not_guess_from_document_dates() -> None:
    assert extract_publication("<p>2025-03-27 report; HTTP Last-Modified 2025-03-28</p>")[0] is None
    with pytest.raises(ValueError):
        extract_publication(
            '<meta name="pubdate" content="2025-03-27"><meta name="pubdate" content="2025-03-28">'
        )
