from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from industrypulse.ai.review import ReviewHistory
from industrypulse.api.app import create_app
from industrypulse.core import TZ_SHANGHAI


def test_fixed_horizons_industry_confirmation_and_omissions(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDUSTRYPULSE_AI_DIR", str(tmp_path))
    history = ReviewHistory()
    generated_at = "2026-11-20T14:59:00+08:00"
    published_at = "2026-11-20T15:05:00+08:00"
    report = {
        "run_id": "scan-1",
        "generated_at": generated_at,
        "mode": "ai",
        "high_risk_warnings": [
            {
                "sector": "semiconductor",
                "title": "半导体预警",
                "evidence": [{"id": "event-1", "type": "news_event"}],
            },
            {"sector": "digital", "title": "通信预警"},
        ],
        "watch_list": [{"sector": "energy"}],
    }
    names = {
        "semiconductor": "半导体",
        "digital": "通信",
        "energy": "新能源",
        "metals": "有色",
        "finance": "金融",
        "materials": "材料",
        "consumer": "消费",
        "lithium:salt": "锂盐加工",
    }
    universe = {
        node: {
            "name": name,
            "effective_from": None,
            "members": [{"symbol": node, "weight": 1.0}] if ":" not in node else [],
        }
        for node, name in names.items()
    }
    benchmark = {"symbol": "sh000300", "name": "沪深300"}
    dates = [date(2026, 11, 23) + timedelta(days=i) for i in range(100)]
    dates = [day.isoformat() for day in dates if day.weekday() < 5]
    before = {node: {"2026-11-19": 99.0} for node in names if ":" not in node}
    before["sh000300"] = {"2026-11-19": 97.0}
    lead = {
        "id": "lead-1",
        "node": "semiconductor",
        "industry_status": "验证中",
        "validation": "下一期月营收同比中位数仍高于 30%",
        "check_by": "2026-12-21",
        "observations": [{"period": "2026-10", "evidence": []}],
    }
    history.record_run(
        report,
        universe,
        benchmark,
        before,
        {},
        [lead],
        published_at=published_at,
        visible_captured_at="2026-11-20T14:58:00+08:00",
    )
    # Intraday scans do not change the first daily cohort.
    history.record_run(
        {
            **report,
            "run_id": "scan-later",
            "generated_at": "2026-11-20T15:20:00+08:00",
            "watch_list": [],
            "early_opportunities": [{"sector": "materials", "title": "日内新增判断"}],
        },
        universe,
        benchmark,
        before,
        {},
        [],
    )
    notice = {
        "kind": "cninfo_announcement",
        "tag": "重大长协合同",
        "date": dates[8],
        "title": "金融企业签署重大合同",
        "url": "https://example.com/notice",
        "sector": "finance",
    }
    history.record_run(
        {
            **report,
            "run_id": "scan-2",
            "generated_at": f"{dates[8]}T17:00:00+08:00",
            "high_risk_warnings": report["high_risk_warnings"][:1],
        },
        universe,
        benchmark,
        before,
        {"by_sector": {"finance": {"hard_signals": [notice]}}},
        [lead],
    )
    # Full series uses benchmark sessions. A missing intermediate close remains a gap.
    series = {
        symbol: dict.fromkeys(dates, 100.0) for symbol in (*names, "sh000300") if ":" not in symbol
    }
    for symbol in series:
        series[symbol]["2026-11-20"] = 80.0  # Published after that day's close.
    for day in dates[5:]:
        series["semiconductor"][day] = 85.0
        series["digital"][day] = series["sh000300"][day] = 95.0
        series["energy"][day] = 110.0
        series["metals"][day] = 82.0
        series["finance"][day] = 95.0
        series["materials"][day] = 115.0
    series["semiconductor"][dates[2]] = 120.0
    series["semiconductor"][dates[4]] = 80.0
    del series["consumer"][dates[3]]

    def read(symbols, start, end):
        return {
            symbol: {
                day: close
                for day, close in series.get(symbol, {}).items()
                if start.isoformat() <= day <= end.isoformat()
            }
            for symbol in symbols
        }

    verified_at = f"{dates[14]}T09:00:00+08:00"
    proof = {
        "source": {"type": "twse_pulse", "url": "https://example.com/revenue"},
        "quote": "11 月营收同比中位数 35.5%",
        "run_id": "verified",
    }
    outcome = {
        **lead,
        "industry_status": "已兑现",
        "timeline": [
            {
                "type": "industry",
                "to": "已兑现",
                "at": verified_at,
                "run_id": "wrong-period",
                "detail": "其他报告期",
            },
            {
                "type": "industry",
                "to": "已兑现",
                "at": verified_at,
                "run_id": "verified",
                "detail": "11 月满足原指标",
            },
        ],
        "observations": [
            {"period": "2026-10", "evidence": [{**proof, "run_id": "wrong-period"}]},
            {"period": "2026-11", "evidence": [proof]},
        ],
    }
    as_of = datetime.fromisoformat(dates[65] + "T17:00:00").replace(tzinfo=TZ_SHANGHAI)
    payload = history.evaluate(read, [outcome], as_of=as_of, selected="2026-11-20")
    selected = payload["selected"]
    assert selected["run_id"] == "scan-1" and selected["published_at"] == published_at
    rows = {item["node"]: item for item in selected["items"]}
    semi = rows["semiconductor"]
    assert semi["visible_at_scan"] == {"date": "2026-11-19", "closes": {"semiconductor": 99.0}}
    assert semi["market"]["start"]["date"] == dates[0]
    assert semi["market"]["start"]["closes"]["semiconductor"] == 100.0
    for steps in (5, 20, 60):
        period = semi["market"]["windows"][str(steps)]
        assert period["status"] == "已锁定" and period["date"] == dates[steps]
        assert period["interpretation"] == "跑输大盘"
        assert (period["return_pct"], period["benchmark_return_pct"], period["excess_pp"]) == (
            -15.0,
            -5.0,
            -10.0,
        )
        assert period["max_gain_pct"] == 20.0
        assert period["max_drawdown_pct"] == pytest.approx(100 / 3, abs=0.001)
    assert semi["industry"][0]["status"] == "已兑现"
    assert semi["industry"][0]["expected_period"] == semi["industry"][0]["period"] == "2026-11"
    assert semi["industry"][0]["evidence"] == [proof]
    assert rows["digital"]["market"]["windows"]["20"]["interpretation"] == "与大盘同步下跌"
    assert rows["consumer"]["market"]["windows"]["5"]["status"] == "数据不足"
    assert rows["consumer"]["market"]["windows"]["5"]["date"] == dates[5]
    assert rows["lithium:salt"]["market"]["windows"]["20"]["status"] == "数据不足"
    misses = {item["node"]: item for item in selected["omissions"]}
    assert misses["energy"]["classification"] == "在观察未升级"
    assert misses["metals"]["classification"] == "没发现"
    assert misses["consumer"]["classification"] == "缺数据"
    assert misses["finance"]["reasons"] == ["重大公告复核"]
    assert misses["finance"]["announcements"][0]["url"] == notice["url"]
    assert not {"semiconductor", "materials"} & misses.keys()
    assert rows["materials"]["coverage"] == "没发现"
    assert rows["materials"]["market"]["windows"]["20"]["excess_pp"] == 20.0
    assert payload["summary"]["samples"] == 3  # New intraday sample; repeated event counted once.
    assert {item["node"] for item in selected["calls"]} == {"semiconductor", "digital", "materials"}

    series["semiconductor"][dates[20]] = 150.0
    repeated = history.evaluate(read, [], as_of=as_of, selected="2026-11-20")
    locked = next(item for item in repeated["selected"]["items"] if item["node"] == "semiconductor")
    assert locked["market"]["windows"]["20"] == semi["market"]["windows"]["20"]
    assert locked["industry"] == semi["industry"]
    # An updated cache can supply a missing bar, without overwriting prior locked results.
    series["consumer"][dates[3]] = 100
    recovered = history.evaluate(read, [], as_of=as_of, selected="2026-11-20")
    consumer = next(item for item in recovered["selected"]["items"] if item["node"] == "consumer")
    assert consumer["market"]["windows"]["5"]["status"] == "已锁定"
    (tmp_path / "review-payload.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_scan_keeps_legacy_history_bytes_and_saves_full_universe(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDUSTRYPULSE_AI_DIR", str(tmp_path))
    monkeypatch.setenv("INDUSTRYPULSE_NEWS_DIR", str(tmp_path / "news"))
    monkeypatch.setenv("INDUSTRYPULSE_MARKET_DATA", "0")
    monkeypatch.setenv("INDUSTRYPULSE_NEWS_AUTO_FETCH", "0")
    legacy = tmp_path / "history.json"
    original = json.dumps([{"id": str(i), "created_at": "2026-10-01"} for i in range(13)]).encode()
    legacy.write_bytes(original)
    with TestClient(create_app()) as client:
        scan = client.post(
            "/api/ai/butterfly/run", json={"use_ai": False, "refresh_signals": False}
        )
        assert scan.status_code == 200, scan.text
        response = client.get("/api/ai/review")
        assert response.status_code == 200, response.text
        items = response.json()["selected"]["items"]
        assert len(items) == 12 + 30 + 6
        assert (
            next(item for item in items if item["node"] == "lithium:salt")["coverage"] == "缺数据"
        )
        assert client.get("/api/ai/history").json()["count"] == 13
    assert legacy.read_bytes() == original
