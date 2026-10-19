import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest

from industrypulse.contracts.observation import RawArtifact
from industrypulse.contracts.sources import LegalStatus, SourcePriority
from industrypulse.datahub.adapters import (
    import_manual,
    parse_inventory,
    parse_iva,
    parse_pmi,
    parse_profit,
    parse_shfe,
    parse_tsf,
)
from industrypulse.datahub.fetcher import Fetcher
from industrypulse.datahub.source_registry import SourceRegistry


def registry() -> SourceRegistry:
    result = SourceRegistry.from_csv(Path("source_catalog.csv"))
    result.set_permission("src.nbs.profit", LegalStatus.PUBLIC_OFFICIAL, reason="synthetic test")
    return result


def test_catalog_permissions() -> None:
    result = registry()
    assert len(result.sources) == 18
    assert all(result.filter(priority) for priority in SourcePriority)
    for status in (LegalStatus.DENIED, LegalStatus.PENDING_REVIEW, LegalStatus.LICENSE_REQUIRED):
        result.set_permission("src.nbs.profit", status, reason="test")
        with pytest.raises(PermissionError):
            result.require_permission("src.nbs.profit")


@pytest.mark.parametrize("status", [200, 404, 429])
def test_fetch_status(tmp_path: Path, status: int) -> None:
    attempts = []

    def handle(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        code = 200 if status == 429 and len(attempts) > 1 else status
        return httpx.Response(code, content=b"original", headers={"Retry-After": "0"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        fetcher = Fetcher(registry(), tmp_path, client, sleep=lambda _: None)
        if status == 404:
            with pytest.raises(httpx.HTTPStatusError):
                fetcher.fetch("src.nbs.profit", "https://www.stats.gov.cn/doc")
            assert len(attempts) == 1
        else:
            artifact = fetcher.fetch("src.nbs.profit", "https://www.stats.gov.cn/doc")
            assert artifact.sha256 == hashlib.sha256(b"original").hexdigest()
            assert Path(artifact.uri).read_bytes() == b"original"
            assert len(attempts) == (2 if status == 429 else 1)


def test_timeout_and_host(tmp_path: Path) -> None:
    calls = []

    def fail(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("timeout", request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        fetcher = Fetcher(registry(), tmp_path, client, sleep=lambda _: None)
        with pytest.raises(PermissionError):
            fetcher.fetch("src.nbs.profit", "https://evil.example/doc")
        assert calls == []
        with pytest.raises(httpx.ReadTimeout):
            fetcher.fetch("src.nbs.profit", "https://www.stats.gov.cn/doc")
        assert len(calls) == 3


def test_resume(tmp_path: Path) -> None:
    url = "https://www.stats.gov.cn/doc"
    (tmp_path / (hashlib.sha256(url.encode()).hexdigest() + ".part")).write_bytes(b"orig")
    (tmp_path / (hashlib.sha256(url.encode()).hexdigest() + ".resume.json")).write_text(
        json.dumps({"etag": '"v1"'})
    )

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["Range"] == "bytes=4-"
        assert request.headers["If-Range"] == '"v1"'
        return httpx.Response(
            206, content=b"inal", headers={"Content-Range": "bytes 4-7/8", "ETag": '"v1"'}
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        artifact = Fetcher(registry(), tmp_path, client).fetch("src.nbs.profit", url)
        assert Path(artifact.uri).read_bytes() == b"original"


def test_profit_multiheader_and_missing() -> None:
    html = (
        '<table><tr><th rowspan="2">行业</th><th colspan="2">利润总额</th></tr>'
        "<tr><th>金额</th><th>同比</th></tr><tr><td>黑色金属冶炼和压延加工业</td><td>-12.5</td><td>—</td></tr></table>"
    )
    value = parse_profit(
        html, period="2025-02", row="黑色金属冶炼和压延加工业", column="利润总额 / 金额"
    )
    assert value.value == Decimal("-12.5") and value.is_ytd
    assert "table[1]" in value.locator
    assert (
        parse_profit(
            html,
            period="2025-03",
            row="黑色金属冶炼和压延加工业",
            column="利润总额 / 同比",
            unit="%",
        ).value
        is None
    )
    with pytest.raises(ValueError):
        parse_profit(html, period="2025-01", row="钢铁", column="金额")


def test_iva_pmi_tsf() -> None:
    html = (
        "<table><tr><th>类别</th><th>当月</th></tr><tr><td>粗钢</td><td>7</td></tr>"
        "<tr><td>制造业PMI</td><td>50.1</td></tr></table>"
    )
    assert (
        parse_iva(
            html, period="2025-03", row="粗钢", column="当月", kind="product", unit="万吨"
        ).value
        == 7
    )
    with pytest.raises(ValueError):
        parse_iva(html, period="2025-03", row="粗钢", column="当月", kind="industry", unit="%")
    assert parse_pmi(
        html, period="2025-03", row="制造业PMI", column="当月", category="headline", table_index=1
    ).value == Decimal("50.1")
    tsf = parse_tsf(
        html,
        period="2025-03",
        row="粗钢",
        column="当月",
        is_ytd=True,
        population="明确口径",
        unit="亿元",
    )
    assert tsf.is_ytd and tsf.statistical_population == "明确口径"


def test_shfe_and_inventory() -> None:
    day = date(2025, 3, 7)
    row = {
        "trading_date": str(day),
        "contract": "RB2505",
        "settlement": "3500",
        "volume": "10",
        "open_interest": "20",
    }
    assert parse_shfe([row], day)[0].contract == "RB2505"
    with pytest.raises(ValueError):
        parse_shfe([row], date(2025, 3, 8))
    rows = [
        {"date": str(day), "commodity": product, "unit": "吨", "inventory": "0"}
        for product in ("RB", "HC")
    ]
    result = parse_inventory(rows, {day, date(2025, 3, 14)})
    assert result.coverage == 0.5 and not result.source_verified
    rows[0]["unit"] = "万吨"
    with pytest.raises(ValueError):
        parse_inventory(rows, {day})


@pytest.mark.parametrize("xlsx", [False, True])
def test_manual_import(tmp_path: Path, xlsx: bool) -> None:
    headers = [
        "series_id",
        "period",
        "value",
        "unit",
        "is_ytd",
        "statistical_population",
        "publication_at",
    ]
    values = [
        "cn.nbs.test",
        "2025-03",
        "0",
        "亿元",
        "true",
        "规模以上工业",
        "2025-04-27T09:30:00+08:00",
    ]
    path = tmp_path / ("manual.xlsx" if xlsx else "manual.csv")
    if xlsx:
        with ZipFile(path, "w") as archive:
            xml = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            for index, row in enumerate((headers, values), 1):
                xml += f'<row r="{index}">'
                for column, value in enumerate(row):
                    xml += f'<c r="{chr(65 + column)}{index}" t="inlineStr">'
                    xml += f"<is><t>{value}</t></is></c>"
                xml += "</row>"
            archive.writestr("xl/worksheets/sheet1.xml", xml + "</sheetData></worksheet>")
    else:
        path.write_text(",".join(headers) + "\n" + ",".join(values), encoding="utf-8")
    artifact = RawArtifact(
        artifact_id="manual",
        source_id="src.nbs.profit",
        uri=str(path),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        mime="text/csv",
        byte_size=path.stat().st_size,
        retrieved_at=datetime.now(UTC),
        source_url="https://www.stats.gov.cn/doc",
    )
    parsed = import_manual(path, artifact, registry())[0]
    assert parsed.value == 0 and parsed.publication_at is not None
    assert parsed.artifact_id == "manual"


def test_publication_without_meta_uses_header_datetime() -> None:
    """2021–2024 公告页没有 PubDate meta，日期以裸 "YYYY/MM/DD HH:MM" 出现在页头。"""
    from industrypulse.contracts.sources import TimestampPrecision
    from industrypulse.datahub.publication import extract_publication

    html = "<html><body><h1>标题</h1><span>2022/07/27 09:30</span><p>正文</p></body></html>"
    stamp, precision = extract_publication(html)
    assert stamp is not None and stamp.isoformat() == "2022-07-27T09:30:00+08:00"
    assert precision == TimestampPrecision.MINUTE


def test_publication_conflicting_header_times_are_rejected() -> None:
    from industrypulse.datahub.publication import extract_publication

    html = "<html><body><span>2022/07/27 09:30</span><span>2022/07/28 10:00</span></body></html>"
    with __import__("pytest").raises(ValueError, match="conflicting"):
        extract_publication(html)
