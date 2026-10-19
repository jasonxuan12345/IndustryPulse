"""按月回填编排：翻页发现、单快照发布、失败文档必须带原因上报。"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from pathlib import Path

import pytest

from industrypulse.contracts import (
    TZ_SHANGHAI,
    IndicatorSpec,
    IndustryNode,
    RawArtifact,
    SeriesSpec,
    SourceDescriptor,
)
from industrypulse.repositories import DuckDBObservationRepository
from industrypulse.services import MonthlyBackfill

ROW = "黑色金属冶炼和压延加工业"
BASE = "https://www.stats.gov.cn/sj/zxfb/"
DOC_URL = BASE + "202509/t20250927_1961400.html"
OLD_URL = BASE + "202508/t20250827_1960884.html"


def financial_page(profit: str, publication: str) -> str:
    return f"""<html><head><meta name="PubDate" content="{publication}"></head><body>
<table>
<tr><th>行 业</th><th>营业收入</th><th>营业收入</th><th>营业成本</th><th>营业成本</th>
<th>利润总额</th><th>利润总额</th></tr>
<tr><th>行 业</th><th>金额</th><th>同比增长</th><th>金额</th><th>同比增长</th>
<th>金额</th><th>同比增长</th></tr>
<tr><th>行 业</th><th>（亿元）</th><th>（%）</th><th>（亿元）</th><th>（%）</th>
<th>（亿元）</th><th>（%）</th></tr>
<tr><td>总计</td><td>1</td><td>2</td><td>3</td><td>4</td><td>5</td><td>6</td></tr>
<tr><td>{ROW}</td><td>44246.2</td><td>-6.6</td><td>41934.0</td><td>-8.4</td>
<td>{profit}</td><td>10.5</td></tr>
</table></body></html>"""


def _listing(*rows: tuple[str, str]) -> bytes:
    anchors = "".join(f'<a href="{href}">{title}</a>' for href, title in rows)
    return f"<html><body>{anchors}</body></html>".encode()


class FakeFetcher:
    """按 URL 返回预置内容，落盘后给出 RawArtifact（模拟 S 线 Fetcher 的契约）。"""

    def __init__(self, pages: dict[str, bytes], root: Path) -> None:
        self.pages = pages
        self.root = root
        self.calls: list[str] = []

    def fetch(self, source_id: str, url: str, *, discovery_url: str | None = None) -> RawArtifact:
        self.calls.append(url)
        content = self.pages[url]
        digest = hashlib.sha256(content).hexdigest()
        path = self.root / digest
        path.write_bytes(content)
        return RawArtifact(
            artifact_id=digest,
            source_id=source_id,
            uri=str(path),
            sha256=digest,
            mime="text/html",
            byte_size=len(content),
            retrieved_at=datetime(2026, 10, 19, 18, tzinfo=TZ_SHANGHAI),
            source_url=url,
        )


SERIES = (
    "cn.nbs.industry_c31.revenue_ytd",
    "cn.nbs.industry_c31.revenue_ytd_yoy",
    "cn.nbs.industry_c31.cost_ytd",
    "cn.nbs.industry_c31.cost_ytd_yoy",
    "cn.nbs.industry_c31.profit_ytd",
    "cn.nbs.industry_c31.profit_ytd_yoy",
)


def _repository(tmp_path: Path) -> DuckDBObservationRepository:
    repository = DuckDBObservationRepository(tmp_path / "pulse.duckdb")
    repository.register_source(
        SourceDescriptor(
            source_id="cn.nbs",
            owner="S",
            url="https://www.stats.gov.cn/",
            data_fields=["profit"],
            frequency="M",
            revision_behavior="revised_later",
            legal_status="public_official",
            priority="S0",
        )
    )
    for series_id in SERIES:
        unit = "%" if series_id.endswith("_yoy") else "亿元"
        repository.register_series(
            SeriesSpec(
                series_id=series_id,
                source_id="cn.nbs",
                unit=unit,
                frequency="M",
                revision_policy="revised_later",
                timestamp_precision="minute",
                description="测试序列",
                is_ytd=True,
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
                indicator_id=f"ind_{series_id.split('.')[-1]}",
                series_ids=[series_id],
                dimension="profitability",
                economic_direction="positive",
                level="CORE",
                industry_ids=["CN_NBS_C31"],
                role="feature",
                valid_from=date(2017, 10, 1),
                description="测试指标",
            )
            for series_id in SERIES
        ],
    )
    return repository


def test_discover_walks_pages_until_range_covered(tmp_path: Path) -> None:
    pages = {
        BASE: _listing(
            ("./202509/t20250927_1961400.html", "2025年1—8月份全国规模以上工业企业利润增长0.9%")
        ),
        BASE + "index_1.html": _listing(
            ("./202508/t20250827_1960884.html", "2025年1—7月份全国规模以上工业企业利润下降1.7%")
        ),
        BASE + "index_2.html": _listing(),  # 不应被访问
    }
    repository = _repository(tmp_path)
    backfill = MonthlyBackfill(repository, FakeFetcher(pages, tmp_path))
    found = backfill.discover(source_id="cn.nbs", kinds=("profit",), start="2025-07", end="2025-08")
    assert [item.period for item in found] == ["2025-07", "2025-08"]
    assert BASE + "index_3.html" not in backfill.fetcher.calls  # type: ignore[attr-defined]


def test_run_publishes_single_snapshot_and_reports_failures(tmp_path: Path) -> None:
    pages = {
        BASE: _listing(
            ("./202509/t20250927_1961400.html", "2025年1—8月份全国规模以上工业企业利润增长0.9%"),
            ("./202508/t20250827_1960884.html", "2025年1—7月份全国规模以上工业企业利润下降1.7%"),
        ),
        BASE + "index_1.html": _listing(),  # 列表到底：本页无新增即停
        DOC_URL: financial_page("1115.0", "2025/09/27 09:30").encode(),
        OLD_URL: b"<html><body>no table here</body></html>",
    }
    repository = _repository(tmp_path)
    fetcher = FakeFetcher(pages, tmp_path)
    backfill = MonthlyBackfill(repository, fetcher)
    announcements = backfill.discover(
        source_id="cn.nbs", kinds=("profit",), start="2025-07", end="2025-08"
    )
    outcome = backfill.run(announcements, source_id="cn.nbs", snapshot_id="monthly-test")

    assert outcome.discovered == 2
    assert len(outcome.skipped) == 1
    assert outcome.skipped[0]["period"] == "2025-07"
    assert outcome.report.created == 6
    assert outcome.report.snapshot_id == "monthly-test"
    # 只有解析成功的那份进入了快照
    assert {row.observation_start for row in outcome.report.vintages} == {date(2025, 8, 1)}


def test_run_rejects_empty_batch(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    backfill = MonthlyBackfill(repository, FakeFetcher({}, tmp_path))
    with pytest.raises(ValueError, match="没有可回填"):
        backfill.run([], source_id="cn.nbs", snapshot_id="x")
