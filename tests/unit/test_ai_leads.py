from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from industrypulse.ai import butterfly
from industrypulse.ai.gateway import ModelConnection
from industrypulse.ai.leads import LeadLedger
from industrypulse.api import ai
from industrypulse.core import TZ_SHANGHAI
from industrypulse.intelligence.service import NewsService


def test_cross_day_leads_in_api_with_no_early_opportunities(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDUSTRYPULSE_AI_DIR", str(tmp_path / "ai"))
    rounds = {"day": "2026-11-20", "month": "11510", "median": 30.2, "close": 100.0, "round": 0}
    context = []

    class Clock:
        @staticmethod
        def now(_tz):
            return datetime.fromisoformat(f"{rounds['day']}T17:00:00").replace(tzinfo=TZ_SHANGHAI)

    def bars(_symbol, **_options):
        return [{"close": rounds["close"], "date": rounds["day"]}]

    market = SimpleNamespace(
        sectors_config={"domains": {"semiconductor": {"board": "BK1036"}}},
        macro=lambda: {"indicators": [{"value": 1.0}] * 5},
        _overview_snapshot=lambda: ({}, None),
        cached_bars=bars,
        latest_sector_bars=bars,
    )

    def radars(*_args):
        return (
            [
                {
                    "id": "semiconductor",
                    "name": "半导体",
                    "available": True,
                    "return_20d": 4.0,
                    "twse_pulse": {"period": rounds["month"], "median_yoy": rounds["median"]},
                }
            ],
            [],
            {},
        )

    def model(_conn, prompt, **_kwargs):
        inputs = json.loads(prompt)
        context.append(inputs["lead_tracking"]["active"])
        attempt = rounds["round"]
        if attempt == 3:
            return {"early_opportunities": []}
        evidence = "market:semiconductor" if attempt == 2 else "twse:semiconductor"
        quote = (
            "近20个交易日涨跌幅 4.0%"
            if attempt == 2
            else f"{rounds['month']} 月营收同比中位数 {rounds['median']}%"
        )
        item = {
            "sector": "semiconductor",
            "title": "台股半导体营收是否持续改善",
            "anchor": {
                "node": "semiconductor",
                "catalyst_type": "revenue_acceleration",
                "report_period": "2026-10" if attempt == 0 else "2026-11",
            },
            "market_direction": "up",
            "validation": "下一期月营收同比中位数仍高于 30%",
            "check_by": "2026-12-21",
            "inference": "依次核对 10 月、11 月的实际营收",
            "facts": [{"evidence_id": evidence, "quote": quote}],
            "industry_outcome": "已兑现" if attempt else None,
        }
        return {
            "early_opportunities": [],
            ("watch_list" if attempt == 0 else "lead_updates"): [item],
        }

    monkeypatch.setattr(ai, "market_service", lambda: market)
    monkeypatch.setattr(ai, "collect_sector_radars", radars)
    monkeypatch.setattr(butterfly, "datetime", Clock)
    monkeypatch.setattr(
        butterfly,
        "load_connection",
        lambda: ModelConnection(base_url="https://mock.example/v1", model="test"),
    )
    monkeypatch.setattr(butterfly, "complete_json", model)
    app = FastAPI()
    app.include_router(ai.create_ai_router(NewsService(tmp_path / "news")))

    with TestClient(app) as client:

        def scan():
            response = client.post("/api/ai/butterfly/run", json={"refresh_signals": False})
            assert response.status_code == 200, response.text
            report = response.json()
            assert report["early_opportunities"] == []
            state = client.get("/api/ai/leads").json()
            assert state["count"] == 1
            return report, state["items"][0]

        first_report, first = scan()
        assert first["industry_status"] == "验证中"
        assert first["market_status"] == "观察中"
        assert first["first_run_id"] == first_report["run_id"]

        rounds.update(day="2026-11-23", close=104.0, round=1)
        _, repeated = scan()
        assert repeated["id"] == first["id"]
        assert repeated["industry_status"] == "验证中"
        assert repeated["market_status"] == "已确认"
        assert [obs["period"] for obs in repeated["observations"]] == ["2026-10"]
        assert len(repeated["observations"][0]["evidence"]) == 1

        rounds.update(day="2026-11-24", close=105.0, round=2)
        _, price_only = scan()
        assert price_only["industry_status"] == "验证中"
        assert len(price_only["observations"]) == 1

        rounds.update(day="2026-11-25", close=105.0, round=3)
        empty, carried = scan()
        assert empty["watch_list"] == [] and empty["lead_updates"] == []
        assert carried["id"] == first["id"]
        assert len(carried["observations"]) == 1

        rounds.update(day="2026-12-10", month="11511", median=35.5, round=4)
        _, closed = scan()
        assert closed["industry_status"] == "已兑现"
        assert closed["market_status"] == "已确认"
        assert [obs["period"] for obs in closed["observations"]] == ["2026-10", "2026-11"]
        timeline = closed["timeline"]
        assert [row["type"] for row in timeline].count("discovery") == 1
        assert [row["period"] for row in timeline if row["type"] == "evidence"] == [
            "2026-10",
            "2026-11",
        ]
        assert [row["at"] for row in timeline] == sorted(row["at"] for row in timeline)
        assert any(row["type"] == "market" and row["to"] == "已确认" for row in timeline)
        assert timeline[-1]["type"] == "industry" and timeline[-1]["to"] == "已兑现"
        assert context[-1][0]["periods"] == ["2026-10"]
        assert client.get("/api/ai/leads").json()["active_count"] == 0


def test_archives_when_due_and_records_refutation_separately(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDUSTRYPULSE_AI_DIR", str(tmp_path))
    ledger = LeadLedger()
    pending = {
        "sector": "energy",
        "inference": "订单仍需核对",
        "anchor": {"node": "batteries", "catalyst_type": "orders", "report_period": "2026-10"},
        "validation": "下期公告确认订单未取消",
        "check_by": "2026-12-20",
    }
    expired = {
        **pending,
        "anchor": {**pending["anchor"], "catalyst_type": "capacity_change"},
        "check_by": "2026-11-22",
    }
    refutation = {
        **pending,
        "anchor": {**pending["anchor"], "report_period": "2026-11"},
        "industry_outcome": "已证伪",
        "inference": "公司公告明确取消订单",
        "facts": [{"evidence_id": "cancel", "quote": "公司确认取消订单"}],
        "evidence": [{"id": "cancel", "type": "cninfo_ann", "excerpt": "公司确认取消订单"}],
    }
    ledger.record_run(
        {
            "generated_at": "2026-11-20T08:00:00+08:00",
            "run_id": "first",
            "watch_list": [pending, expired],
        },
        sector_symbols={},
        lookup=None,
    )
    ledger.record_run(
        {
            "generated_at": "2026-11-23T08:00:00+08:00",
            "run_id": "followup",
            "lead_updates": [refutation],
        },
        sector_symbols={},
        lookup=None,
    )
    states = {item["catalyst_type"]: item for item in ledger.list()}
    assert states["orders"]["industry_status"] == "已证伪"
    assert states["capacity_change"]["industry_status"] == "超期未验证归档"
    assert all(item["market_status"] == "暂无市场参照" for item in states.values())
