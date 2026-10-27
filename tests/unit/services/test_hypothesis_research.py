"""AI 假设研究（阶段 3）：结构化校验、缓存、预算、模型失败隔离。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from industrypulse.contracts import TZ_SHANGHAI
from industrypulse.services import hypothesis_research as hr

NOW = datetime(2026, 10, 25, 20, 0, tzinfo=TZ_SHANGHAI)


def _packet() -> dict[str, Any]:
    return {
        "prompt_version": hr.PROMPT_VERSION,
        "event": {"event_id": "lead-x", "chain": "有色金属"},
        "facts": [
            {
                "ref": "F1",
                "label": "沪铜主力",
                "value": 80000,
                "unit": "元/吨",
                "period": "2026-10-24",
            },
            {
                "ref": "F2",
                "label": "有色冶炼利润累计同比",
                "value": -3.2,
                "unit": "%",
                "period": "2026-08",
            },
        ],
        "boundary": ["只引用编号事实"],
    }


def _packet_hypothesis() -> dict:
    """引用 _packet() 静态事实表的合法假设（校验单测用）。"""
    return {
        "channel": "cost",
        "statement": "沪铜 80000 对应冶炼加工费承压，利润同比 -3.2 或继续回落",
        "transmission": "原料成本占冶炼成本主体，铜价高位挤压冶炼利润",
        "supporting": [
            {"fact_ref": "F1", "note": "铜价 80000 高位"},
            {"fact_ref": "F2", "note": "利润同比 -3.2"},
        ],
        "invalidation_conditions": ["利润累计同比回升到 0 以上"],
        "observation_months": 3,
        "next_verification": "2026-11-27 利润期公布",
    }


def _state_hypothesis() -> dict:
    """引用 build_packet 从 _state() 构建的事实编号（端到端用）：
    F1=PMI 新订单，F2=宏观状态，F3=行业利润累计同比，F4=环比变动，
    F5=营收，F6=成本，F7=价格检验。"""
    return {
        "channel": "demand",
        "statement": "PMI 新订单 50.6 回落背景下，有色冶炼利润累计同比 -3.2 或继续承压",
        "transmission": "订单需求走弱传导到量价，压制行业利润同比",
        "supporting": [
            {"fact_ref": "F1", "note": "新订单 50.6 回落"},
            {"fact_ref": "F3", "note": "利润同比 -3.2"},
        ],
        "invalidation_conditions": ["利润累计同比回升到 0 以上"],
        "observation_months": 3,
        "next_verification": "2026-11-27 利润期公布",
    }


def _packet_counter() -> dict:
    return {
        "counter_evidence": [
            {
                "fact_ref": "F2",
                "observation": "利润同比降幅收窄",
                "weight": "contradicts",
                "note": "收窄则假设削弱",
            }
        ],
        "verdict": "weakened",
    }


def _state_counter() -> dict:
    return {
        "counter_evidence": [
            {
                "fact_ref": "F4",
                "observation": "利润环比变动 +0.4 在改善",
                "weight": "contradicts",
                "note": "环比改善则假设削弱",
            }
        ],
        "verdict": "weakened",
    }


class TestParseAnswer:
    def test_plain_and_fenced_json(self) -> None:
        assert hr.parse_answer('{"a": 1}') == {"a": 1}
        assert hr.parse_answer('```json\n{"a": 1}\n```') == {"a": 1}
        assert hr.parse_answer('前言 {"a": 1} 后记') == {"a": 1}

    def test_rejects_non_json(self) -> None:
        with pytest.raises(ValueError):
            hr.parse_answer("模型没有输出 JSON")


class TestValidate:
    def test_valid_hypothesis_passes(self) -> None:
        result = hr.validate_hypothesis(_packet_hypothesis(), _packet_counter(), _packet())
        assert result["status"] == "validated", result["failed"]

    def test_missing_counter_evidence_is_rejected(self) -> None:
        result = hr.validate_hypothesis(
            _packet_hypothesis(), {"counter_evidence": [], "verdict": "reinforced"}, _packet()
        )
        assert result["status"] == "rejected" and "counter_nonempty" in result["failed"]

    def test_unknown_fact_ref_is_rejected(self) -> None:
        hypothesis = _packet_hypothesis()
        hypothesis["supporting"] = [{"fact_ref": "F9", "note": "不存在"}]
        result = hr.validate_hypothesis(hypothesis, _packet_counter(), _packet())
        assert "supporting_refs" in result["failed"]

    def test_stray_number_in_statement_is_rejected(self) -> None:
        hypothesis = _packet_hypothesis()
        hypothesis["statement"] = "沪铜 99999 对应利润率改善"
        result = hr.validate_hypothesis(hypothesis, _packet_counter(), _packet())
        assert "statement_numbers_from_facts" in result["failed"]

    def test_vague_invalidation_is_structurally_allowed_but_empty_is_not(self) -> None:
        hypothesis = _packet_hypothesis()
        hypothesis["invalidation_conditions"] = []
        result = hr.validate_hypothesis(hypothesis, _packet_counter(), _packet())
        assert "invalidation_conditions" in result["failed"]

    def test_channel_and_months_ranges(self) -> None:
        hypothesis = _packet_hypothesis()
        hypothesis["channel"] = "momentum"
        result = hr.validate_hypothesis(hypothesis, _packet_counter(), _packet())
        assert "channel" in result["failed"]
        hypothesis = _packet_hypothesis()
        hypothesis["observation_months"] = 24
        result = hr.validate_hypothesis(hypothesis, _packet_counter(), _packet())
        assert "observation_months" in result["failed"]


class _FakeConnection:
    enabled = True


class TestRun:
    def _events(self) -> list[dict]:
        return [
            {
                "event_id": "lead-x",
                "chain_name": "有色金属",
                "candidate_name": "沪铜主力连续",
                "what_changed": "沪铜主力连续 上升 500（相对 2026-10-17 的 79500）",
                "relative_to": "2026-10-17 的 79500",
                "release_at": None,
                "detection_lag_hours": None,
                "next_verification": "下次扫描",
                "license": "公开接口",
                "industries": [{"id": "CN_NBS_C32", "name": "有色金属冶炼和压延加工业"}],
            }
        ]

    def _state(self) -> dict:
        return {
            "macro": {
                "regime_label": "扩张",
                "period": "2026-09",
                "source_url": "https://example.test",
                "breadth": 0.5,
                "components": [
                    {
                        "id": "new_orders",
                        "name": "新订单",
                        "value": 50.6,
                        "change": 0.2,
                        "period": "2026-09",
                    }
                ],
            },
            "industries": [
                {
                    "name": "有色金属冶炼和压延加工业",
                    "period": "2026-08",
                    "profit_yoy": -3.2,
                    "profit_change": 0.4,
                    "revenue_yoy": 5.1,
                    "cost_yoy": 6.0,
                    "source_urls": ["https://example.test/nbs"],
                    "note": "",
                    "price_check": {
                        "name": "有色金属ETF",
                        "horizons": [
                            {
                                "months": 3,
                                "status": "TESTED",
                                "difference": 0.01,
                                "q_value": 0.85,
                                "mean_on": 0.02,
                                "mean_off": 0.01,
                            }
                        ],
                    },
                }
            ],
        }

    def test_full_run_appends_validated_record_and_spends_budget(self, tmp_path: Path) -> None:
        calls: list[str] = []

        def fake_complete(connection, prompt, *, system=""):  # noqa: ANN001
            calls.append(system[:6])
            if "反证" in system:
                return json.dumps(_state_counter(), ensure_ascii=False)
            return json.dumps(_state_hypothesis(), ensure_ascii=False)

        payload = hr.run_hypothesis_research(
            fake_complete,
            _FakeConnection(),
            self._state(),
            events=self._events(),
            model_id="test-model",
            now=NOW,
            cache_file=tmp_path / "cache.json",
            budget_file=tmp_path / "budget.json",
            hypotheses_path=tmp_path / "hyp.jsonl",
            journal_path=tmp_path / "journal.jsonl",
        )
        assert payload["appended"] == 1 and payload["processed"] == 1
        assert len(calls) == 2  # 两步两次调用
        assert payload["budget"]["used"] == 2
        record = payload["records"][0]
        assert record["status"] == "RESEARCH_CANDIDATE"
        assert record["observation_until"] == "2027-01-23"
        assert record["hypothesis_id"].startswith("hyp-")
        lines = (tmp_path / "hyp.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1

    def test_same_packet_hits_cache_and_spends_nothing(self, tmp_path: Path) -> None:
        counter = 0

        def fake_complete(connection, prompt, *, system=""):  # noqa: ANN001
            nonlocal counter
            counter += 1
            if "反证" in system:
                return json.dumps(_state_counter(), ensure_ascii=False)
            return json.dumps(_state_hypothesis(), ensure_ascii=False)

        kwargs = dict(
            model_id="test-model",
            now=NOW,
            cache_file=tmp_path / "cache.json",
            budget_file=tmp_path / "budget.json",
            hypotheses_path=tmp_path / "hyp.jsonl",
            journal_path=tmp_path / "journal.jsonl",
        )
        first = hr.run_hypothesis_research(
            fake_complete, _FakeConnection(), self._state(), events=self._events(), **kwargs
        )
        assert counter == 2 and first["budget"]["used"] == 2
        second = hr.run_hypothesis_research(
            fake_complete,
            _FakeConnection(),
            self._state(),
            events=self._events(),  # 同一事件 → 同一数据包 → 缓存命中
            **kwargs,
        )
        assert counter == 2  # 没有新的模型调用
        assert second["cache_hits"] == 1
        (tmp_path / "budget.json").write_text(
            json.dumps({"date": NOW.date().isoformat(), "used": 12, "limit": 12}), encoding="utf-8"
        )
        third = hr.run_hypothesis_research(
            fake_complete,
            _FakeConnection(),
            self._state(),
            events=self._events(),
            cached_only=True,
            **kwargs,
        )
        assert counter == 2 and third["cache_hits"] == 1 and third["budget"]["used"] == 12
        assert third["appended"] == 0
        assert second["budget"]["used"] == 2  # 缓存命中不消耗预算

    def test_model_failure_is_isolated(self, tmp_path: Path) -> None:
        def broken_complete(connection, prompt, *, system=""):  # noqa: ANN001
            raise ValueError("无法连接模型服务")

        payload = hr.run_hypothesis_research(
            broken_complete,
            _FakeConnection(),
            self._state(),
            events=self._events(),
            model_id="test-model",
            now=NOW,
            cache_file=tmp_path / "cache.json",
            budget_file=tmp_path / "budget.json",
            hypotheses_path=tmp_path / "hyp.jsonl",
            journal_path=tmp_path / "journal.jsonl",
        )
        assert payload["appended"] == 0 and payload["processed"] == 0
        assert payload["failures"][0]["status"] == "failed"
        assert not (tmp_path / "hyp.jsonl").exists()

    def test_budget_exhaustion_defers_events(self, tmp_path: Path) -> None:
        (tmp_path / "budget.json").write_text(
            json.dumps({"date": NOW.date().isoformat(), "used": 12, "limit": 12}),
            encoding="utf-8",
        )

        def fake_complete(connection, prompt, *, system=""):  # noqa: ANN001
            raise AssertionError("预算耗尽时不应调用模型")

        payload = hr.run_hypothesis_research(
            fake_complete,
            _FakeConnection(),
            self._state(),
            events=self._events(),
            model_id="test-model",
            now=NOW,
            cache_file=tmp_path / "cache.json",
            budget_file=tmp_path / "budget.json",
            hypotheses_path=tmp_path / "hyp.jsonl",
            journal_path=tmp_path / "journal.jsonl",
        )
        assert payload["processed"] == 0
        assert payload["failures"][0]["status"] == "deferred"

    def test_invalid_model_output_is_rejected_not_stored(self, tmp_path: Path) -> None:
        def lying_complete(connection, prompt, *, system=""):  # noqa: ANN001
            hypothesis = _state_hypothesis()
            hypothesis["supporting"] = [{"fact_ref": "F9", "note": "编造的引用"}]
            return json.dumps(hypothesis, ensure_ascii=False)

        payload = hr.run_hypothesis_research(
            lying_complete,
            _FakeConnection(),
            self._state(),
            events=self._events(),
            model_id="test-model",
            now=NOW,
            cache_file=tmp_path / "cache.json",
            budget_file=tmp_path / "budget.json",
            hypotheses_path=tmp_path / "hyp.jsonl",
            journal_path=tmp_path / "journal.jsonl",
        )
        assert payload["appended"] == 0
        assert payload["failures"][0]["status"] == "rejected"
