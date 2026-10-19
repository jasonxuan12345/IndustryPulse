"""分红页解析：每10份折算每份，冲突即拒绝。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from industrypulse.datahub.fund_dividend import (
    DividendParseError,
    parse_dividend_table,
    to_corporate_actions,
)

PAGE = """<html><body><table>
<tr><td>2025年</td><td>2025-12-23</td><td>2025-12-24</td><td>每10份派现金0.6000元</td><td>2025-12-29</td></tr>
<tr><td>2024年</td><td>2024-12-23</td><td>2024-12-24</td><td>每10份派现金0.6000元</td><td>2024-12-30</td></tr>
</table></body></html>"""


def test_parses_and_converts_per_ten_to_per_unit() -> None:
    records = parse_dividend_table(PAGE)
    assert [r.ex_date for r in records] == [date(2024, 12, 24), date(2025, 12, 24)]
    assert records[0].per_unit == Decimal("0.0600")


def test_duplicates_are_deduplicated() -> None:
    doubled = PAGE + PAGE
    records = parse_dividend_table(doubled)
    assert len(records) == 2


def test_conflicting_amounts_are_rejected() -> None:
    conflicting = """<html><body><table>
<tr><td>2024年</td><td>2024-12-23</td><td>2024-12-24</td><td>每10份派现金0.6000元</td><td>2024-12-30</td></tr>
<tr><td>2024年</td><td>2024-12-23</td><td>2024-12-24</td><td>每10份派现金0.9000元</td><td>2024-12-30</td></tr>
</table></body></html>"""
    with pytest.raises(DividendParseError, match="冲突"):
        parse_dividend_table(conflicting)


def test_empty_page_returns_empty_list() -> None:
    assert parse_dividend_table("<html><body>暂无分红</body></html>") == []


def test_to_corporate_actions_shape() -> None:
    actions = to_corporate_actions(
        parse_dividend_table(PAGE), asset_id="ETF_515210", source_note="test"
    )
    assert len(actions) == 2
    assert actions[0].action_type == "cash_dividend"
    assert actions[0].distribution_per_unit == Decimal("0.0600")
