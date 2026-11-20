from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from industrypulse.smart_money.service import SmartMoneyEntryInput, SmartMoneyService


def test_manual_notes_do_not_count_as_holdings_consensus(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDUSTRYPULSE_SMART_MONEY_DIR", str(tmp_path / "sm"))
    svc = SmartMoneyService()
    assert len(svc.institutions()) >= 5

    old_date = date.today() - timedelta(days=40)
    for inst_id in ("berkshire", "bridgewater", "coatue"):
        svc.add_entry(
            SmartMoneyEntryInput(
                institution_id=inst_id,
                sector="ai",
                direction="INCREASE",
                title=f"{inst_id} 增配 AI 算力",
                summary="公开披露增仓算力基础设施",
                disclosed_at=old_date,
            )
        )

    entries = svc.entries()
    assert len(entries) == 3
    assert "滞后" in entries[0]["lag_warning"]

    summary = svc.sector_summary()
    assert summary["ai"]["signal"] == "OBSERVING"
    assert summary["ai"]["increase_count"] == 0
    assert summary["ai"]["view_count"] == 3

    assert svc.delete_entry(entries[0]["id"]) is True
    assert len(svc.entries()) == 2

    with pytest.raises(ValueError, match="未知机构"):
        svc.add_entry(
            SmartMoneyEntryInput(
                institution_id="nonexistent",
                sector="ai",
                direction="VIEW",
                title="test",
                summary="test",
                disclosed_at=date.today(),
            )
        )


def test_sync_sec_filings_handles_success_and_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDUSTRYPULSE_SMART_MONEY_DIR", str(tmp_path / "sm"))
    svc = SmartMoneyService()

    def handler(request: httpx.Request) -> httpx.Response:
        if "0001067983" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "filings": {
                        "recent": {
                            "form": ["8-K", "13F-HR"],
                            "filingDate": ["2026-10-01", "2026-11-14"],
                            "reportDate": ["2026-10-01", "2026-09-30"],
                            "accessionNumber": ["0001-01", "0001067983-26-000012"],
                        }
                    }
                },
            )
        return httpx.Response(503, text="unavailable")

    orig_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: orig_client(**kw, transport=httpx.MockTransport(handler)),
    )
    res = svc.sync_sec_filings()
    assert res["filings"]["berkshire"]["filing_date"] == "2026-11-14"
    assert res["filings"]["berkshire"]["accessionNumber"] == "0001067983-26-000012"
    assert "缺少连续上季" in res["errors"]["berkshire"]
    assert "bridgewater" in res["errors"]


def test_compare_quarters_and_exclude_split_from_consensus(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDUSTRYPULSE_SMART_MONEY_DIR", str(tmp_path / "sm"))
    svc = SmartMoneyService()
    period = "2026-09-30"
    prior = "2026-06-30"
    nvda, amd, tsm = "67066G104", "007903107", "874039100"
    metadata = {}
    submissions = {}
    downloads = []

    def add_filing(cik, seq, form, end, day, holdings, amendment=""):
        accession = f"{cik}-26-{seq:06}"
        recent = metadata.setdefault(cik, [])
        recent.insert(0, (form, end, day, accession))
        cover = f"<edgarSubmission><amendmentType>{amendment}</amendmentType></edgarSubmission>"
        table = '<informationTable xmlns="urn:sec:13f">'
        for cusip, count, value, manager, option in holdings:
            table += (
                f"<infoTable><nameOfIssuer>Issuer {cusip}</nameOfIssuer><cusip>{cusip}</cusip>"
                f"<value>{value}</value><shrsOrPrnAmt><sshPrnamt>{count}</sshPrnamt>"
                "<sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>"
                f"<otherManager>{manager}</otherManager>{option}</infoTable>"
            )
        submissions[accession] = (
            f"<DOCUMENT>\n<TYPE>{form}\n<FILENAME>cover.xml\n<TEXT><XML>{cover}</XML>"
            "</TEXT></DOCUMENT>\n<DOCUMENT>\n<TYPE>INFORMATION TABLE\n<FILENAME>custom-q.xml\n"
            f"<TEXT><XML>{table}</informationTable></XML></TEXT></DOCUMENT>"
        )
        return accession

    berkshire = "0001067983"
    prior_id = add_filing(
        berkshire,
        1,
        "13F-HR",
        prior,
        "2026-08-14",
        [(nvda, 100, 1000, "1", ""), (tsm, 5, 500, "", ""), (amd, 20, 300, "", "")],
    )
    add_filing(berkshire, 2, "13F-HR", period, "2026-11-12", [(nvda, 999, 2000, "", "")])
    revised_id = add_filing(
        berkshire,
        3,
        "13F-HR/A",
        period,
        "2026-11-13",
        [
            (nvda, 75, 800, "1", ""),
            (nvda, 55, 720, "2", ""),
            (nvda, 200, 1000, "1", "<putCall>Call</putCall>"),
        ],
        "RESTATEMENT",
    )
    added_id = add_filing(
        berkshire,
        4,
        "13F-HR/A",
        period,
        "2026-11-14",
        [
            (amd, 35, 510, "", ""),
        ],
        "NEW HOLDINGS",
    )
    coatue = "0001135730"
    add_filing(coatue, 1, "13F-HR", prior, "2026-08-14", [(nvda, 100, 1000, "", "")])
    add_filing(coatue, 2, "13F-HR", period, "2026-11-14", [(nvda, 200, 1040, "", "")])
    bridgewater = "0001350694"
    add_filing(bridgewater, 1, "13F-HR", "2026-03-31", "2026-05-15", [(amd, 5, 100, "", "")])
    add_filing(bridgewater, 2, "13F-HR", prior, "2026-08-14", [(amd, 8, 130, "", "")])

    def handler(request: httpx.Request) -> httpx.Response:
        filename = request.url.path.rsplit("/", 1)[-1]
        if request.url.host == "data.sec.gov":
            if filename[3:-5] not in metadata:
                return httpx.Response(503)
            columns = zip(*metadata[filename[3:-5]], strict=True)
            recent = dict(
                zip(("form", "reportDate", "filingDate", "accessionNumber"), columns, strict=True)
            )
            return httpx.Response(200, json={"filings": {"recent": recent}})
        accession = filename.removesuffix(".txt")
        downloads.append(accession)
        return httpx.Response(200, text=submissions[accession])

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(**kw, transport=httpx.MockTransport(handler))
    )
    synced = svc.sync_sec_filings()
    assert synced["filings"]["berkshire"]["accessionNumber"] == added_id
    portfolios = {
        item["institution_id"]: item for item in svc.holdings("semiconductor")["institutions"]
    }
    berk = portfolios["berkshire"]
    positions = {(row["ticker"], row["option"]): row for row in berk["rows"]}
    shares = positions["NVDA", "NONE"]
    assert shares["quantity"] == 130
    assert shares["previous_quantity"] == 100
    assert shares["quantity_change"] == 30
    assert shares["weight_pct"] == pytest.approx(1520 / 3030 * 100, abs=0.0001)
    assert shares["other_managers"] == ["1", "2"]
    assert shares["sources"][0]["accessionNumber"] == revised_id
    assert shares["previous_sources"][0]["accessionNumber"] == prior_id
    assert positions["NVDA", "CALL"]["status"] == "NEW"
    assert positions["AMD", "NONE"]["sources"][0]["filing_date"] == "2026-11-14"
    assert positions["TSM", "NONE"]["status"] == "NOT_DISCLOSED"
    assert positions["TSM", "NONE"]["quantity"] is None
    assert [v["amendment"] for v in berk["versions"]] == [None, "RESTATEMENT", "NEW HOLDINGS"]
    assert berk["versions"][0]["table_files"] == ["custom-q.xml"]
    flagged = portfolios["coatue"]["rows"][0]
    assert flagged["status"] == "REVIEW"
    assert flagged["quantity_change"] is None
    assert "疑似拆股" in flagged["review_reason"]
    summary = svc.sector_summary()["semiconductor"]
    assert summary["report_date"] == period
    assert summary["increase_institutions"] == ["berkshire"]
    assert summary["increase_count"] == 1
    assert summary["reduce_count"] == 0
    count = len(downloads)
    svc.sync_sec_filings()
    assert len(downloads) == count
    assert svc.sector_summary()["semiconductor"] == summary


def test_smart_money_ai_parse_and_add(tmp_path, monkeypatch) -> None:
    import json

    from industrypulse.ai import gateway

    monkeypatch.setenv("INDUSTRYPULSE_SMART_MONEY_DIR", str(tmp_path / "sm"))
    monkeypatch.setenv("INDUSTRYPULSE_MODEL_CONFIG", str(tmp_path / "model.json"))
    gateway.save_connection(
        gateway.ModelConnection(
            base_url="https://mock.example/v1", model="deepseek-chat", api_key="k", enabled=True
        )
    )

    def reply(_request: httpx.Request) -> httpx.Response:
        content = json.dumps(
            {
                "institution_id": "coatue",
                "sector": "semiconductor",
                "direction": "INCREASE",
                "title": "Coatue Q3 加仓半导体设备",
                "summary": "13F 披露增持先进封装与设备龙头",
                "disclosed_at": "2026-11-15",
            },
            ensure_ascii=False,
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    orig = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: orig(**kw, transport=httpx.MockTransport(reply))
    )
    svc = SmartMoneyService()
    saved = svc.parse_and_add("新闻报道 Coatue 三季度大幅加仓半导体设备公司")
    assert saved["institution_id"] == "coatue"
    assert saved["sector"] == "semiconductor"
    assert saved["direction"] == "INCREASE"
