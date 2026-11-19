from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from industrypulse.smart_money.service import SmartMoneyEntryInput, SmartMoneyService


def test_smart_money_entries_lag_and_consensus(tmp_path, monkeypatch) -> None:
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
    assert summary["ai"]["signal"] == "CROWDED_CONSENSUS"
    assert summary["ai"]["increase_count"] == 3

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
    assert "bridgewater" in res["errors"]


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
