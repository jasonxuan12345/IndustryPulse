from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from industrypulse.ai import butterfly
from industrypulse.ai.gateway import ModelConnection
from industrypulse.core import TZ_SHANGHAI
from industrypulse.signals.anomalies import SignalService
from industrypulse.signals.industry_chains import assess_chains

ROOT = Path(__file__).resolve().parents[2]
AS_OF = datetime(2026, 11, 20, 9, 45, tzinfo=TZ_SHANGHAI)


def _event(title: str, summary: str, category: str, *, row_id: str = "quote") -> dict[str, object]:
    return {
        "id": row_id,
        "title": title,
        "summary": summary,
        "industry_ids": [category],
        "published_at": AS_OF.isoformat(),
        "sector": "consumer" if category == "supply-chain" else "energy",
        "url": f"https://example.com/{row_id}",
    }


def test_lithium_run_has_distinct_impacts_and_freezes_baskets(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDUSTRYPULSE_AI_DIR", str(tmp_path))

    class Clock:
        @staticmethod
        def now(_tz):
            return AS_OF

    monkeypatch.setattr(butterfly, "datetime", Clock)
    article = _event(
        "碳酸锂期货下跌4.2%",
        "动力电池装机量同比下降8%。预计电池售价同比下降3%。",
        "batteries",
    )
    market = {
        "id": "lc",
        "symbol": "LC0",
        "name": "碳酸锂",
        "change_pct": -4.2,
        "price": 75000,
        "unit": "元/吨",
        "date": AS_OF.date().isoformat(),
        "sectors": ["energy", "metals"],
    }
    report = butterfly.run_butterfly_analysis(
        radars=[],
        stories=[article],
        macro_items=[],
        commodities=[market],
        smart_money_summary={},
        use_ai=False,
    )
    chain = report["chain_assessments"][0]
    assert chain["symbol"] == "LC0"
    nodes = {node["id"]: node for node in chain["nodes"]}
    mine, battery = nodes["miners"], nodes["battery"]
    assert mine["facts"][0] == battery["facts"][0]
    assert "售价" in mine["inference"] and "毛利可能承压" in mine["inference"]
    assert "成本可能下降" in battery["inference"]
    assert "下游需求走弱" in battery["inference"]
    assert "锂矿实现售价跟随期货方向变化" in mine["conditions"]
    assert "电池售价与装机量未同步走弱" in battery["conditions"]
    assert battery["observations"]["动力电池装机量"] == "下降8%"
    assert "电池售价" in battery["gaps"] and "电池售价" not in battery["observations"]
    assert battery["profit_status"] == "待验证"
    saved = json.loads(
        (tmp_path / "runs" / report["run_id"] / "snapshot.json").read_text(encoding="utf-8")
    )
    assert saved["report"] == report
    basket = next(row for row in saved["chain_baskets"] if row["node"] == "battery")
    assert basket["effective_from"] == "2026-11-20"
    assert basket["members"] == battery["basket"]["members"]
    assert {member["symbol"]: member["weight"] for member in basket["members"]} == {
        "sz300750": 0.5,
        "sz300014": 0.5,
    }
    # The same published event can drive the trial without a futures quote.
    news_only = assess_chains(ROOT, [], [article], as_of=AS_OF)
    assert news_only[0]["shock"]["evidence"]["type"] == "news_event"
    assert news_only[0]["shock"]["quote"] == "碳酸锂期货下跌4.2%"

    inputs = {}

    def mock_model(_conn, prompt, **_kwargs):
        inputs.update(json.loads(prompt))
        return {"chain_assessments": [{"id": "cannot_replace_the_observation"}]}

    monkeypatch.setattr(butterfly, "complete_json", mock_model)
    modeled = butterfly.run_butterfly_analysis(
        radars=[],
        stories=[article],
        macro_items=[],
        commodities=[market],
        smart_money_summary={},
        connection=ModelConnection(base_url="https://mock.example/v1", model="test"),
    )
    assert modeled["mode"] == "ai"
    assert inputs["pilot_chains"] == modeled["chain_assessments"] == [chain]
    assert chain["shock"]["evidence"] in inputs["evidence_catalog"]


def test_freight_checks_who_pays_and_clears_legacy_generic_scoring(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDUSTRYPULSE_SIGNALS_DIR", str(tmp_path))
    svc = SignalService()
    market = {
        "id": "ec",
        "symbol": "EC0",
        "name": "集运欧线",
        "change_pct": -4.3,
        "date": AS_OF.date().isoformat(),
        "sectors": ["consumer", "macro"],
    }
    other = {"symbol": "CU0", "name": "沪铜", "change_pct": -5, "sectors": ["metals"]}
    surges, warnings = svc.extract_commodity_anomalies(
        [
            market,
            {**market, "change_pct": 4.3},
            {"id": "lc", "change_pct": 6, "sectors": ["metals"]},
            other,
        ]
    )
    assert not surges
    assert list(warnings) == ["metals"] and "沪铜" in warnings["metals"][0]["title"]
    legacy_warning = {"kind": "commodity_crash", "title": "集运欧线(EC0) -4.30%"}
    legacy_boost = {"kind": "commodity_surge", "title": "碳酸锂(LC0) +5.00%"}
    svc._save_cache(
        {
            "by_sector": {
                "consumer": {"commodity_warnings": [legacy_warning]},
                "energy": {"hard_signals": [legacy_boost], "hard_signal_count": 1},
            },
            "highlights": [legacy_boost],
            "channels": {"commodities": {"status": "ok", "count": 1, "warning_count": 1}},
        }
    )
    filtered = svc.load_cached()
    assert filtered["by_sector"]["consumer"]["commodity_warnings"] == []
    assert filtered["by_sector"]["energy"]["hard_signal_count"] == 0
    assert filtered["highlights"] == []
    assert filtered["channels"]["commodities"]["count"] == 0
    assert filtered["channels"]["commodities"]["warning_count"] == 0

    unresolved = assess_chains(ROOT, [market], [], as_of=AS_OF)[0]["nodes"]
    assert "单位收入可能下降" in unresolved[0]["inference"]
    assert "成本可能下降" in unresolved[1]["inference"]
    assert unresolved[1]["profit_status"] == "待验证"
    assert "运费承担方" in unresolved[1]["gaps"] and unresolved[1]["basket"] is None
    article = _event("欧线出口运费安排", "对欧出口合同的海运费由买方承担。", "supply-chain")
    resolved = assess_chains(ROOT, [market], [article], as_of=AS_OF)[0]["nodes"][1]
    assert resolved["observations"]["运费承担方"] == "买方"
    assert "出口方的直接海运费未必随之变化" in resolved["inference"]
    assert "运费承担方" not in resolved["gaps"]
