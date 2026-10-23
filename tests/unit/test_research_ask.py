from industrypulse.services.research_ask import guard_answer, question_prompt, research_packet


def _state() -> dict:
    return {
        "as_of": "2026-10-23T17:00:00+08:00",
        "snapshot_id": "all-industries-demo",
        "disclaimer": "研究状态，不是投资信号。",
        "macro": {
            "regime": "EXPANSION",
            "regime_label": "扩张",
            "level": 50.1,
            "period": "2026-09-01",
        },
        "hypotheses": [
            {
                "industry_id": "CN_NBS_C36",
                "theme": "汽车制造业利润累计同比改善",
                "status": "RESEARCH_CANDIDATE",
            }
        ],
        "industries": [
            {
                "name": "汽车制造业",
                "industry_id": "CN_NBS_C36",
                "period": "2026-08-01",
                "profit_yoy": 1.23456,
                "profit_change": 0.4,
                "revenue_yoy": 2.0,
                "cost_yoy": 1.0,
                "cyclical_label": "改善",
                "note": "累计同比",
                "price_check": {
                    "name": "汽车ETF国泰",
                    "fit": "对口",
                    "status": "NOT_VALIDATED",
                    "horizons": [
                        {
                            "months": 6,
                            "status": "TESTED",
                            "n_on": 15,
                            "n_off": 26,
                            "mean_on": -0.031,
                            "mean_off": 0.005,
                            "q_value": 0.92,
                        }
                    ],
                },
            }
        ],
        "gaps": [{"name": "半导体", "reason": "官方工业利润表没有半导体单列"}],
        "price_check": {
            "status": "NOT_VALIDATED",
            "tests": 1,
            "method": "循环平移",
            "disclaimer": "不是投资建议。",
        },
    }


def test_packet_keeps_computed_results_and_the_semiconductor_gap() -> None:
    brief = {
        "sector": "auto",
        "day": "2026-10-23",
        "clusters": [{}, {}],
        "events": [
            {
                "subject": "某公司",
                "action": "投产",
                "quantity": None,
                "unit": None,
                "quote": "原文",
                "sources": [{"name": "来源"}],
            }
        ],
    }
    packet = research_packet(_state(), brief)
    assert packet["price_check"]["status"] == "NOT_VALIDATED"
    assert packet["gaps"][0]["name"] == "半导体"
    assert packet["industries"][0]["profit_yoy"] == 1.2346
    assert packet["events"]["cluster_count"] == 2
    assert packet["events"]["extracted"][0]["subject"] == "某公司"
    assert "建议买入" not in question_prompt("利润为什么上升", packet)


def test_trading_advice_is_withheld() -> None:
    assert guard_answer("汽车利润改善，建议买入对应 ETF。")["withheld"] is True
    kept = guard_answer("汽车的 6 个月检验看不出差异，状态仍是 NOT_VALIDATED。")
    assert kept["withheld"] is False
    assert "NOT_VALIDATED" in kept["answer"]
