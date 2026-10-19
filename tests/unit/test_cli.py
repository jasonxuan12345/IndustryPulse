"""CLI 是 G2 的唯一操作入口：这里验证它真的能串起 permit -> init -> ingest。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from industrypulse.cli import app

runner = CliRunner()

ROW = "黑色金属冶炼和压延加工业"
PROFIT_PARAMS = json.dumps({"row": ROW, "column": "利润总额", "unit": "亿元"}, ensure_ascii=False)


def page(value: str) -> str:
    return (
        '<html><head><meta name="pubdate" content="2025-08-27 09:30:00"></head><body><table>'
        "<tr><th>行业</th><th>利润总额</th></tr>"
        f"<tr><td>{ROW}</td><td>{value}</td></tr>"
        "</table></body></html>"
    )


def permit(database: Path, catalog_id: str, contract_id: str, audit: Path) -> None:
    result = runner.invoke(
        app,
        [
            "source",
            "permit",
            catalog_id,
            "--as",
            contract_id,
            "--database",
            str(database),
            "--reason",
            "离线测试：核验依据为公开官网页面",
            "--actor",
            "pytest",
            "--audit",
            str(audit),
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr


def test_doctor_reports_environment() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["python_supported"] is True
    assert payload["contracts_version"] == "1.0.0"


def test_source_list_shows_candidates() -> None:
    result = runner.invoke(app, ["source", "list", "--catalog", "source_catalog.csv"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert any(row["source_id"] == "src.nbs.profit" for row in rows)
    # 目录默认不授予采集许可
    assert all(row["legal_status"] == "pending_review" for row in rows)


def test_calendar_validate_accepts_fixture() -> None:
    result = runner.invoke(app, ["calendar", "validate", "tests/fixtures/g0/market_calendar.json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["trading_days"] == 222
    assert len(payload["source_sha256"]) == 64


def test_calendar_validate_rejects_weekend(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"trading_days": ["2025-01-03", "2025-01-04"]}), encoding="utf-8")
    result = runner.invoke(app, ["calendar", "validate", str(bad)])
    assert result.exit_code == 1


def test_ingest_before_init_fails_with_guidance(tmp_path: Path) -> None:
    database = tmp_path / "pulse.duckdb"
    permit(database, "SRC-NBS-PROFIT", "cn.nbs", tmp_path / "audit.jsonl")
    document = tmp_path / "doc.html"
    document.write_text(page("1580.4"), encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "ingest",
            "--database",
            str(database),
            "--source-id",
            "cn.nbs",
            "--file",
            str(document),
            "--period",
            "2025-07",
            "--source-url",
            "https://www.stats.gov.cn/sj/zxfb/202508/t20250827_1.html",
            "--parser",
            "profit",
            "--params",
            PROFIT_PARAMS,
        ],
    )
    assert result.exit_code == 1
    assert "未登记 SeriesSpec" in result.stderr


def test_permit_init_ingest_end_to_end(tmp_path: Path) -> None:
    database = tmp_path / "pulse.duckdb"
    audit = tmp_path / "audit.jsonl"
    permit(database, "SRC-NBS-PROFIT", "cn.nbs", audit)
    permit(database, "SRC-SHFE-RB", "cn.shfe", audit)

    initialized = runner.invoke(
        app, ["init", "--database", str(database), "--taxonomy", "config/industries/steel.yaml"]
    )
    assert initialized.exit_code == 0, initialized.stdout + initialized.stderr
    registered = json.loads(initialized.stdout)
    assert "cn.nbs.industry_c31.profit_ytd" in registered["series"]

    document = tmp_path / "doc.html"
    document.write_text(page("1580.4"), encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "ingest",
            "--database",
            str(database),
            "--source-id",
            "cn.nbs",
            "--file",
            str(document),
            "--period",
            "2025-07",
            "--source-url",
            "https://www.stats.gov.cn/sj/zxfb/202508/t20250827_1.html",
            "--parser",
            "profit",
            "--params",
            PROFIT_PARAMS,
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["created"] == 1 and payload["reused"] == 0
    vintage = payload["vintages"][0]
    assert vintage["series_id"] == "cn.nbs.industry_c31.profit_ytd"
    assert vintage["observation_start"] == "2025-07-01"
    assert vintage["release_evidence"] == "A"
    # CLI 未主张"同期原件"，数值证据必须停在 C
    assert vintage["value_evidence"] == "C"
    assert vintage["availability_basis"] == "observed_publication"


FINANCIAL_PAGE = """<html><head><meta name="PubDate" content="2026/09/28 09:30"></head><body>
<table>
<tr><th>行 业</th><th>营业收入</th><th>营业收入</th><th>营业成本</th><th>营业成本</th>
<th>利润总额</th><th>利润总额</th></tr>
<tr><th>行 业</th><th>金额</th><th>同比增长</th><th>金额</th><th>同比增长</th>
<th>金额</th><th>同比增长</th></tr>
<tr><th>行 业</th><th>（亿元）</th><th>（%）</th><th>（亿元）</th><th>（%）</th>
<th>（亿元）</th><th>（%）</th></tr>
<tr><td>总计</td><td>930851.2</td><td>6.6</td><td>791893.6</td><td>6.1</td>
<td>52719.8</td><td>15.7</td></tr>
<tr><td>黑色金属冶炼和压延加工业</td><td>49288.6</td><td>-1.1</td><td>46993.8</td>
<td>-0.4</td><td>294.3</td><td>-62.4</td></tr>
</table></body></html>"""


def test_backfill_expands_one_document_into_six_series(tmp_path: Path) -> None:
    database = tmp_path / "pulse.duckdb"
    permit(database, "SRC-NBS-PROFIT", "cn.nbs", tmp_path / "audit.jsonl")
    initialized = runner.invoke(
        app, ["init", "--database", str(database), "--taxonomy", "config/industries/steel-g2.yaml"]
    )
    assert initialized.exit_code == 0, initialized.stdout + initialized.stderr

    document = tmp_path / "financials.html"
    document.write_text(FINANCIAL_PAGE, encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "source_id": "cn.nbs",
                    "period": "2026-08",
                    "file": str(document),
                    "url": "https://www.stats.gov.cn/sj/zxfb/202609/t20260928_1965425.html",
                    "parser": "profit-table",
                    "params": {"row": ROW, "table_index": 1},
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "backfill",
            "--database",
            str(database),
            "--manifest",
            str(manifest),
            "--snapshot-id",
            "g2-test",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["created"] == 6
    series = {item["series_id"] for item in payload["vintages"]}
    assert series == {
        "cn.nbs.industry_c31.revenue_ytd",
        "cn.nbs.industry_c31.revenue_ytd_yoy",
        "cn.nbs.industry_c31.cost_ytd",
        "cn.nbs.industry_c31.cost_ytd_yoy",
        "cn.nbs.industry_c31.profit_ytd",
        "cn.nbs.industry_c31.profit_ytd_yoy",
    }
