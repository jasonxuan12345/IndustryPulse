from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from industrypulse.contracts import TZ_SHANGHAI
from industrypulse.services import ledger, research_loop
from industrypulse.services import research_mvp as mvp
from industrypulse.services.workbench import ROOT, Workbench

AT = datetime(2026, 10, 28, 19, tzinfo=TZ_SHANGHAI)
CASES = {"CN_NBS_C06", "CN_NBS_B07", "CN_NBS_C13", "CN_NBS_C35"}
QUOTE = "这是测试夹具中的原始披露文本，行业需求变化仍待后续核验。"
URL = "https://www.stats.gov.cn/test-fixture"


class Tools:
    def __init__(self, *, fail=0, future=False, missing_date=False, fabricated=False):
        self.fail, self.future = fail, future
        self.missing_date, self.fabricated = missing_date, fabricated
        self.searches = []

    def search(self, query):
        self.searches.append(query)
        if self.fail:
            self.fail -= 1
            raise ConnectionError("injected")
        return [
            {
                "url": URL,
                "title": "合成验收夹具，非真实投资证据",
                "date": None
                if self.missing_date
                else (
                    AT + timedelta(days=1) if self.future else AT - timedelta(days=1)
                ).isoformat(),
            }
        ]

    def read(self, url):
        return {"title": "合成验收夹具", "content": QUOTE}

    def ask(self, prompt):
        if '"query"' in prompt:
            return {"query": "支持需求检索" if "supports" in prompt else "需求下滑风险独立检索"}
        return {
            "facts": [{"url": URL, "quote": "编造的原文不存在" if self.fabricated else QUOTE}],
            "verdict": "weakened",
            "thesis": "需求变化尚未传导至可核验盈利",
            "gaps": ["估值未知"],
        }


@pytest.fixture
def config():
    return yaml.safe_load((ROOT / "config/research/mvp.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def bundles():
    service = Workbench()
    specs, policy = research_loop.universe(service)
    service.runner.close()
    service.pool.shutdown()
    result = []
    for spec in specs:
        news = []
        if spec["industry_id"] in CASES:
            news.append(
                {
                    "title": "合成回放线索 " + spec["name"],
                    "url": URL,
                    "published_at": (AT - timedelta(days=1)).isoformat(),
                }
            )
        result.append(
            {
                "spec": spec,
                "policy": {**policy, "lag_days": 5, "series_lag_days": {}, "trading_days": []},
                "vintages": [],
                "bars": [],
                "actions": [],
                "news_leads": news,
            }
        )
    return result


def execute(tmp_path, bundles, config, tools=None, run_id="replay"):
    service = SimpleNamespace(root=tmp_path)
    return mvp.run(
        service,
        as_of=AT,
        run_id=run_id,
        config=config,
        bundles=bundles,
        tools=tools or Tools(),
        output=tmp_path / "out",
        ledger_path=tmp_path / "ledger.jsonl",
    )


def test_twenty_sectors_four_cases_three_runs_idempotency_and_failure(tmp_path, bundles, config):
    assert len(bundles) == 20
    tools = Tools(fail=1)
    for index in range(3):
        result = execute(tmp_path, bundles, config, tools, f"run-{index}")
        directory = Path(result["directory"])
        queue = json.loads((directory / "opportunity_queue.json").read_text(encoding="utf-8"))
        assert queue["scanned_sectors"] == 20
        assert {o["sector"] for o in queue["opportunities"]} == CASES
        memos = json.loads((directory / "investment_memo.json").read_text(encoding="utf-8"))[
            "memos"
        ]
        assert len(memos) == 4
        assert all(
            m["recommendation_status"] == "WATCH" and not m["execution_allowed"] for m in memos
        )
        before = (tmp_path / "ledger.jsonl").read_bytes()
        assert execute(tmp_path, bundles, config, tools, f"run-{index}") == result
        assert (tmp_path / "ledger.jsonl").read_bytes() == before
    records = ledger.load_ledger(tmp_path / "ledger.jsonl")
    assert len(records) == 36
    assert {r["horizon_months"] for r in records} == {1, 3, 6}
    assert all(r["metrics"] is None and r["verification"] is None for r in records)
    assert any("风险" in q for q in tools.searches)
    assert any("支持" in q for q in tools.searches)
    logs = json.loads((tmp_path / "out/run-0/research_review.json").read_text(encoding="utf-8"))
    assert any(t.get("error") == "ConnectionError" for r in logs["reviews"] for t in r["tool_log"])


@pytest.mark.parametrize("change", ["future", "missing_date", "fabricated"])
def test_untrusted_evidence_never_verifies(tmp_path, bundles, config, change):
    tools = Tools(**{change: True})
    result = execute(tmp_path, bundles, config, tools)
    packet = json.loads(
        (Path(result["directory"]) / "evidence_packet.json").read_text(encoding="utf-8")
    )
    assert all(not p["facts"] and p["status"] == "unverified" for p in packet["packets"])


def test_budget_and_no_future_queue(tmp_path, bundles, config):
    config["max_calls_per_opportunity"] = 1
    result = execute(tmp_path, bundles, config)
    reviews = json.loads(
        (Path(result["directory"]) / "research_review.json").read_text(encoding="utf-8")
    )
    assert all(r["calls_used"] <= 1 for r in reviews["reviews"])
    future = copy.deepcopy(bundles)
    for b in future:
        for n in b["news_leads"]:
            n["published_at"] = (AT + timedelta(days=1)).isoformat()
    queue = mvp.scan(future, mvp.metadata("future", AT, "test"), AT, config)
    assert not queue["opportunities"]


def test_checkpoint_resume_and_frozen_tamper(tmp_path, bundles, config, monkeypatch):
    original = ledger.register

    def fail(**kwargs):
        raise OSError("injected ledger failure")

    monkeypatch.setattr(ledger, "register", fail)
    with pytest.raises(OSError):
        execute(tmp_path, bundles, config)
    monkeypatch.setattr(ledger, "register", original)

    class NoTools:
        def __getattr__(self, name):
            raise AssertionError("checkpoint must avoid repeating research")

    result = execute(tmp_path, bundles, config, NoTools())
    path = Path(result["directory"]) / "investment_memo.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="冻结产物"):
        execute(tmp_path, bundles, config)


def test_asof_and_config_bounds(tmp_path, bundles, config):
    execute(tmp_path, bundles, config)
    with pytest.raises(ValueError, match="as_of"):
        mvp.run(
            SimpleNamespace(root=tmp_path),
            as_of=AT + timedelta(days=1),
            run_id="replay",
            output=tmp_path / "out",
            bundles=bundles,
        )
    config["max_rounds"] = 4
    with pytest.raises(ValueError, match="最多3轮"):
        execute(tmp_path, bundles, config, run_id="bad")


def test_scheduler_registered_at_beijing_close(tmp_path):
    scheduler = mvp.schedule(SimpleNamespace(root=tmp_path))
    assert str(scheduler.timezone) == "Asia/Shanghai"
    job = scheduler.get_jobs()[0]
    assert "hour='18'" in str(job.trigger) and "minute='10'" in str(job.trigger)


def test_offline_archive_replays_without_network(tmp_path, bundles, config, monkeypatch):
    first = execute(tmp_path, bundles, config, run_id="capture")
    archive = Path(first["directory"]) / "tool_archive.json"

    def forbidden(*args, **kwargs):
        raise AssertionError("offline replay attempted network")

    monkeypatch.setattr(mvp.httpx, "Client", forbidden)
    second = execute(tmp_path, bundles, config, mvp.ToolArchive(archive), "offline")
    left = json.loads(
        (Path(first["directory"]) / "evidence_packet.json").read_text(encoding="utf-8")
    )
    right = json.loads(
        (Path(second["directory"]) / "evidence_packet.json").read_text(encoding="utf-8")
    )
    assert [[f["evidence_id"] for f in p["facts"]] for p in left["packets"]] == [
        [f["evidence_id"] for f in p["facts"]] for p in right["packets"]
    ]
    for packet in right["packets"]:
        for fact in packet["facts"]:
            mvp.validate_record(fact)
            raw = packet["documents"][fact["document_hash"]]["page"]
            assert ledger._hash(raw) == fact["document_hash"]
            location = fact["location"]
            assert raw["content"][location["start"] : location["end"]] == fact["fact"]


def test_missing_asset_is_rejected_without_numeric_scenario(tmp_path, bundles, config):
    for bundle in bundles:
        bundle["spec"].pop("asset_id", None)
    result = execute(tmp_path, bundles, config)
    memos = json.loads(
        (Path(result["directory"]) / "investment_memo.json").read_text(encoding="utf-8")
    )["memos"]
    assert all(
        m["asset_id"] == "UNMAPPED" and m["recommendation_status"] == "REJECT" for m in memos
    )
    assert all(
        s["expected_return"] is None for m in memos for s in m["scenario_assumptions"].values()
    )


def test_unknown_calendar_does_not_invoke_research(tmp_path, monkeypatch):
    monkeypatch.setattr(mvp, "run", lambda *a, **k: pytest.fail("unknown calendar ran research"))
    mvp.scheduled_tick(SimpleNamespace(root=tmp_path))
    status = json.loads(
        (tmp_path / "artifacts/research-mvp/schedule-status.json").read_text(encoding="utf-8")
    )
    assert status["status"] == "skipped_unknown_or_closed_calendar"


def test_relative_strength_uses_traceable_frozen_local_inputs(bundles, config):
    bundle = next(b for b in bundles if b["spec"]["industry_id"] == "CN_NBS_C06")
    bundle["news_leads"] = []
    days = [AT.date() - timedelta(days=i) for i in range(60, -1, -1)]
    days = [d for d in days if d.weekday() < 5]
    for aid, step in ((bundle["spec"]["asset_id"], 0.01), ("ETF_510300", 0)):
        for index, day in enumerate(days):
            price = 1 + index * step
            bundle["bars"].append(
                {
                    "asset_id": aid,
                    "trade_date": day.isoformat(),
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 2000000,
                    "adj_method": "none",
                }
            )
    queue = mvp.scan([bundle], mvp.metadata("rs", AT, "test"), AT, config)
    opportunity = queue["opportunities"][0]
    assert opportunity["trigger_type"] == "ETF_RELATIVE_STRENGTH"
    assert opportunity["trigger_value"] > config["relative_strength_threshold"]
    assert opportunity["source_ids"][0].startswith("urn:sha256:")
    assert opportunity["inputs"]["bars"] == bundle["bars"]
    assert opportunity["data_quality"]["gaps"]
