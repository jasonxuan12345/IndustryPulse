"""PMI 真实版式：单位为横幅行、行为月份、列为指标。"""

from __future__ import annotations

import pytest

from industrypulse.core.html_tables import header_span, tables
from industrypulse.datahub.adapters import PMI_MATRIX_COLUMNS, parse_pmi_matrix


def _page() -> str:
    """复刻国家统计局 PMI 月表：首行整行 "单位：%" 横幅，随后两行表头，再是月份行。"""
    return """<html><head><meta name="PubDate" content="2026/09/30 09:30"></head><body>
<table>
<tr><td>单位：%</td><td>单位：%</td><td>单位：%</td><td>单位：%</td></tr>
<tr><td></td><td>PMI</td><td></td><td></td></tr>
<tr><td></td><td>PMI</td><td>生产</td><td>新订单</td></tr>
<tr><td>2026年8月</td><td>49.8</td><td>50.4</td><td>50.6</td></tr>
<tr><td>2026年9月</td><td>50.1</td><td>51.7</td><td>50.5</td></tr>
</table></body></html>"""


def test_banner_row_is_skipped_when_locating_headers() -> None:
    table = tables(_page())[0]
    start, end = header_span(table)
    # 整行 "单位：%" 的横幅不计入表头；真正的表头是随后首列为空的两行
    assert start == 1 and end == 3


def test_parses_each_registered_column() -> None:
    for indicator, suffix in PMI_MATRIX_COLUMNS.items():
        if indicator not in ("PMI", "生产", "新订单"):
            continue
        parsed = parse_pmi_matrix(
            _page(),
            period="2026-09",
            month_label="2026年9月",
            indicator=indicator,
            table_index=1,
        )
        assert parsed.series_id == f"cn.nbs.pmi.{suffix}"
        assert parsed.unit == "%"
        assert parsed.value is not None
        assert parsed.period == "2026-09"
        assert parsed.locator.startswith("table[1]/tr[5]")


def test_unknown_column_is_rejected() -> None:
    with pytest.raises(ValueError, match="未登记的 PMI 列"):
        parse_pmi_matrix(
            _page(),
            period="2026-09",
            month_label="2026年9月",
            indicator="不存在的指标",
            table_index=1,
        )


def test_missing_month_row_is_rejected() -> None:
    with pytest.raises(ValueError, match="expected one exact row/column"):
        parse_pmi_matrix(
            _page(),
            period="2025-01",
            month_label="2025年1月",
            indicator="PMI",
            table_index=1,
        )
