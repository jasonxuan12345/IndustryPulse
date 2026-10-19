"""上期所日行情 JSON：单合约口径、小计行剔除、发布时间与报告日校验。"""

from __future__ import annotations

import json
from datetime import date

import pytest

from industrypulse.contracts.sources import TimestampPrecision
from industrypulse.datahub.adapters import parse_shfe_daily

TRADING_DAY = date(2026, 10, 19)


def _payload(**overrides: object) -> str:
    base: dict[str, object] = {
        "report_date": "20261019",
        "update_date": "20261019 15:56:16",
        "o_curinstrument": [
            {
                "PRODUCTID": "rb_f",
                "DELIVERYMONTH": "2701",
                "SETTLEMENTPRICE": 3080,
                "VOLUME": 793143,
                "OPENINTEREST": 1743434,
            },
            {
                "PRODUCTID": "rb_f",
                "DELIVERYMONTH": "2702",
                "SETTLEMENTPRICE": 3066,
                "VOLUME": 100,
                "OPENINTEREST": 1472,
            },
            {  # 小计行：无结算价，必须被跳过
                "PRODUCTID": "rb_f",
                "DELIVERYMONTH": "小计",
                "SETTLEMENTPRICE": "",
                "VOLUME": 907258,
                "OPENINTEREST": 2361691,
            },
            {  # 非 RB/HC 品种：由 parse_shfe 过滤
                "PRODUCTID": "cu_f",
                "DELIVERYMONTH": "2611",
                "SETTLEMENTPRICE": 109450,
                "VOLUME": 107082,
                "OPENINTEREST": 176863,
            },
            {
                "PRODUCTID": "hc_f",
                "DELIVERYMONTH": "2701",
                "SETTLEMENTPRICE": 3250,
                "VOLUME": 476885,
                "OPENINTEREST": 1510323,
            },
        ],
    }
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


def test_parses_explicit_contract_only() -> None:
    parsed = parse_shfe_daily(_payload(), trading_date=TRADING_DAY, contract="RB2701")
    assert parsed.series_id == "cn.shfe.rb2701.settlement"
    assert parsed.value is not None and str(parsed.value) == "3080"
    assert parsed.unit == "元/吨"
    assert parsed.is_ytd is False
    # 日频序列必须按真实交易日做所属期，不能退化成自然月
    assert parsed.observation_start == TRADING_DAY
    assert parsed.observation_end == TRADING_DAY
    assert parsed.locator == "kx[2026-10-19]/RB2701/SETTLEMENTPRICE"


def test_publication_time_is_second_precision() -> None:
    parsed = parse_shfe_daily(_payload(), trading_date=TRADING_DAY, contract="HC2701")
    assert parsed.timestamp_precision == TimestampPrecision.SECOND
    assert parsed.publication_at is not None
    assert parsed.publication_at.isoformat() == "2026-10-19T15:56:16+08:00"


def test_missing_update_date_downgrades_to_unknown() -> None:
    parsed = parse_shfe_daily(
        _payload(update_date=None), trading_date=TRADING_DAY, contract="RB2701"
    )
    assert parsed.publication_at is None
    assert parsed.timestamp_precision == TimestampPrecision.UNKNOWN


def test_unknown_contract_is_rejected_with_candidates() -> None:
    with pytest.raises(ValueError, match="没有合约 CU2701"):
        parse_shfe_daily(_payload(), trading_date=TRADING_DAY, contract="CU2701")


def test_report_date_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="不一致"):
        parse_shfe_daily(
            _payload(report_date="20261016"), trading_date=TRADING_DAY, contract="RB2701"
        )


def test_malformed_payload_is_rejected() -> None:
    with pytest.raises(ValueError, match="不是合法 JSON"):
        parse_shfe_daily("{not json", trading_date=TRADING_DAY, contract="RB2701")
    with pytest.raises(ValueError, match="o_curinstrument"):
        parse_shfe_daily(
            json.dumps({"report_date": "20261019"}), trading_date=TRADING_DAY, contract="RB2701"
        )
