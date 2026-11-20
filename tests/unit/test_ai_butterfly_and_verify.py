from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx

from industrypulse.ai import gateway
from industrypulse.ai.butterfly import load_latest_report, run_butterfly_analysis
from industrypulse.ai.history import JudgmentHistory
from industrypulse.ai.verify import verify_lead
from industrypulse.core import TZ_SHANGHAI


def _sample_radars() -> list[dict]:
    return [
        {
            "id": "energy",
            "name": "新能源",
            "available": True,
            "zone": "LURKING",
            "zone_label": "早期潜伏区",
            "position_score": 25.0,
            "catalyst_score": 68.0,
            "price_percentile_60d": 18.0,
            "turnover_percentile_60d": 22.0,
            "return_5d": 0.5,
            "return_20d": -2.0,
            "relative_20d": -1.0,
            "risk_flags": [],
        },
        {
            "id": "ai",
            "name": "人工智能",
            "available": True,
            "zone": "HIGH_RISK",
            "zone_label": "高危离场警示",
            "position_score": 91.0,
            "catalyst_score": 45.0,
            "price_percentile_60d": 95.0,
            "turnover_percentile_60d": 92.0,
            "return_5d": -0.2,
            "return_20d": 28.0,
            "relative_20d": 25.0,
            "risk_flags": ["高位放量滞涨"],
        },
    ]


def _sample_stories() -> list[dict]:
    return [
        {
            "id": "story-e1",
            "sector": "energy",
            "title": "欧洲高压海缆招标超预期",
            "summary": "多国电网并网计划加速，海缆与变压器交期延长",
            "source": "公开资讯",
            "url": "https://example.com/e1",
            "published_at": "2026-11-18T10:00:00+08:00",
        },
        {
            "id": "story-a1",
            "sector": "ai",
            "title": "热门算力题材换手率创新高",
            "summary": "短期融资盘持续拥挤",
            "source": "公开资讯",
            "url": "https://example.com/a1",
            "published_at": "2026-11-18T11:00:00+08:00",
        },
    ]


def test_butterfly_rule_fallback_and_persistence(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDUSTRYPULSE_AI_DIR", str(tmp_path / "ai"))
    monkeypatch.setenv("INDUSTRYPULSE_MODEL_CONFIG", str(tmp_path / "missing-model.json"))

    report = run_butterfly_analysis(
        radars=_sample_radars(),
        stories=_sample_stories(),
        macro_items=[],
        commodities=[],
        smart_money_summary={},
        use_ai=True,
    )
    assert report["mode"] == "rule_fallback"
    assert report["early_opportunities"][0]["sector"] == "energy"
    assert report["high_risk_warnings"][0]["sector"] == "ai"
    assert load_latest_report()["run_id"] == report["run_id"]
    snapshot = tmp_path / "ai" / "runs" / report["run_id"] / "snapshot.json"
    saved = json.loads(snapshot.read_text(encoding="utf-8"))
    assert saved["report"] == report
    assert saved["fallback_reason"]
    assert saved["model_calls"] == []


def test_butterfly_ai_parallel_and_verify_and_history(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDUSTRYPULSE_AI_DIR", str(tmp_path / "ai"))
    conn = gateway.ModelConnection(
        base_url="https://mock.example/v1",
        model="test-reasoner",
        api_key="secret",
        enabled=True,
    )
    captured = {}

    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sys_prompt = body["messages"][0]["content"]
        user_prompt = body["messages"][1]["content"]
        if "弱信号初筛助手" in sys_prompt:
            if '"sector": "energy"' in user_prompt:
                content = json.dumps(
                    {
                        "signals": [
                            {
                                "headline": "欧洲电网招标加速",
                                "chain": "海外招标->变压器与海缆订单",
                                "strength": "high",
                                "story_ids": ["story-e1", "fabricated-id"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            else:
                content = "无法解析的模型响应"
        elif "交叉核验员" in sys_prompt:
            content = json.dumps(
                {
                    "verdict": "EARLY_ALPHA",
                    "verdict_label": "早期认知差",
                    "reasoning": "本地近30日相关讨论尚少，且板块仍处于低位 18% 分位。",
                    "supporting_signals": ["海外订单周期延长"],
                    "counter_evidence": ["若国内电网投资低于预期则逻辑弱化"],
                    "action_hint": "持续跟踪后续中标公告",
                },
                ensure_ascii=False,
            )
        else:
            captured.update(json.loads(user_prompt))
            content = json.dumps(
                {
                    "macro_regime": {
                        "summary": "外需电网与制造升级出现局部先行信号",
                        "favored_styles": ["低位出口链"],
                        "key_risks": ["高拥挤算力回撤"],
                    },
                    "early_opportunities": [
                        {
                            "sector": "energy",
                            "sector_name": "新能源",
                            "title": "电网出海出现订单传导迹象",
                            "butterfly_chain": "海外招标加速 -> 国内设备出口订单增量",
                            "quant_alignment": "位置分 25，处于低位",
                            "smart_money_note": "暂无拥挤",
                            "disproof_signal": "连续两月出口额同比转负",
                            "evidence_story_ids": ["story-e1", "fake-id"],
                        }
                    ],
                    "high_risk_warnings": [
                        {
                            "sector": "ai",
                            "sector_name": "人工智能",
                            "title": "交易拥挤与高位滞涨",
                            "reason": "60日价格 95% 分位叠加放量滞涨",
                            "disproof_signal": "缩量企稳并伴随新业绩超预期",
                            "evidence_story_ids": ["story-a1"],
                        }
                    ],
                    "watch_list": [],
                },
                ensure_ascii=False,
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    orig = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: orig(**kw, transport=httpx.MockTransport(reply))
    )

    report = run_butterfly_analysis(
        radars=_sample_radars(),
        stories=_sample_stories(),
        macro_items=[
            {"name": "PMI", "value": 50.3, "unit": "%", "period": "2026-10"},
            {"id": "northbound_net", "name": "北向资金累计净买入", "value": 99.0},
        ],
        commodities=[],
        smart_money_summary={},
        connection=conn,
        use_ai=True,
        data_status={
            "macro_unavailable": [
                {
                    "id": "northbound_net",
                    "name": "北向资金净买入",
                    "status": "retired",
                    "last_valid_date": "2024-08-16",
                }
            ]
        },
    )
    assert report["mode"] == "ai"
    early = report["early_opportunities"][0]
    assert early["evidence_story_ids"] == ["story-e1"]  # 伪造 id 被过滤
    assert early["evidence_stories"][0]["url"] == "https://example.com/e1"
    snapshot = tmp_path / "ai" / "runs" / report["run_id"] / "snapshot.json"
    raw_snapshot = snapshot.read_text(encoding="utf-8")
    saved = json.loads(raw_snapshot)
    assert saved["report"] == report
    assert saved["model"]["name"] == "test-reasoner"
    assert conn.api_key.get_secret_value() not in raw_snapshot
    assert saved["sector_status"]["energy"]["screening"] == "ok"
    assert saved["sector_status"]["ai"]["screening"] == "failed"
    assert any(
        call["sector"] == "ai" and call["status"] == "failed" for call in saved["model_calls"]
    )
    assert [call["stage"] for call in saved["model_calls"]].count("screening") == 2
    synthesis = saved["model_calls"][-1]
    assert synthesis["input"] == captured
    assert len(synthesis["prompt_version"]) == 64
    assert [item["name"] for item in captured["macro_indicators"]] == ["PMI"]
    assert captured["macro_data_gaps"][0]["last_valid_date"] == "2024-08-16"

    # 测试随手投喂核验
    vr = verify_lead(
        text="看到欧洲某电网公司大幅上调未来五年海缆资本开支",
        sector="energy",
        radars=_sample_radars(),
        stories=_sample_stories(),
        smart_money_summary={},
        connection=conn,
    )
    assert vr["verdict"] == "EARLY_ALPHA"

    # 测试历史判断记录与真实涨跌回看
    hist = JudgmentHistory()
    recorded = hist.record_run(
        report,
        {r["id"]: r for r in _sample_radars()},
        {"energy": (100.0, "2026-11-10"), "ai": (200.0, "2026-11-10")},
    )
    assert len(recorded) == 2

    # 模拟 5 天前发出的快照，检验后续真实回报计算
    saved = hist._load()
    past = (datetime.now(TZ_SHANGHAI) - timedelta(days=6)).isoformat()
    for row in saved:
        row["created_at"] = past
    hist._save(saved)

    outcomes = hist.list_with_outcomes(
        {"energy": (106.0, "2026-11-16"), "ai": (188.0, "2026-11-16")}
    )
    by_sector = {o["sector"]: o for o in outcomes}
    assert by_sector["energy"]["actual_return_pct"] == 6.0
    assert "命中" in by_sector["energy"]["outcome_label"]
    assert by_sector["ai"]["actual_return_pct"] == -6.0
    assert "有效" in by_sector["ai"]["outcome_label"]

    summary = JudgmentHistory.summarize(outcomes)
    assert summary["evaluated_calls"] == 2
    assert summary["lurking_hits"] == 1
    assert summary["high_risk_hits"] == 1
    assert summary["win_rate_pct"] == 100.0
