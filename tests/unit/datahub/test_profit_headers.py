"""真实官方多级表头：确定性列解析 + 拒绝模糊列。"""

from __future__ import annotations

import pytest

from industrypulse.datahub.adapters import parse_profit, resolve_table_column

ROW = "黑色金属冶炼和压延加工业"


def _page(unit_amount: str, unit_growth: str = "(%)") -> str:
    """复刻国家统计局利润公告表结构：3 行表头 + 数据行（含表头同形数据行）。"""
    return f"""<html><head><meta name="PubDate" content="2025/08/27 09:30"></head><body>
<table>
<tr><th>行 业</th><th>营业收入</th><th>营业收入</th><th>利润总额</th><th>利润总额</th></tr>
<tr><th>行 业</th><th>金额</th><th>同比增长</th><th>金额</th><th>同比增长</th></tr>
<tr><th>行 业</th><th>{unit_amount}</th><th>(%)</th>
<th>{unit_amount}</th><th>{unit_growth}</th></tr>
<tr><td>总计</td><td>780703.3</td><td>2.3</td><td>40203.5</td><td>-1.7</td></tr>
<tr><td>{ROW}</td><td>49288.6</td><td>-1.1</td><td>294.3</td><td>-62.4</td></tr>
</table></body></html>"""


@pytest.mark.parametrize("unit", ["(亿元)", "（亿元）"])
def test_resolves_amount_column_across_paren_styles(unit: str) -> None:
    column = resolve_table_column(_page(unit), table_index=1, sub="金额")
    assert column.unit == "亿元"
    parsed = parse_profit(
        _page(unit),
        period="2025-07",
        row=ROW,
        column=column.label,
        unit=column.unit,
        table_index=1,
    )
    assert parsed.value is not None and parsed.series_id.endswith("profit_ytd")


def test_resolves_growth_column_separately() -> None:
    column = resolve_table_column(_page("(亿元)"), table_index=1, sub="同比增长")
    assert column.unit == "%"
    parsed = parse_profit(
        _page("(亿元)"),
        period="2025-07",
        row=ROW,
        column=column.label,
        unit=column.unit,
        table_index=1,
    )
    assert parsed.series_id.endswith("_yoy")


def test_ambiguous_metric_is_rejected() -> None:
    # 只给"利润总额"会同时命中金额列与同比列，必须拒绝而不是取第一个
    with pytest.raises(ValueError, match="found 2|expected one exact"):
        parse_profit(
            _page("(亿元)"),
            period="2025-07",
            row=ROW,
            column="利润总额",
            unit="亿元",
            table_index=1,
        )


def test_out_of_range_table_is_rejected() -> None:
    with pytest.raises(ValueError, match="越界"):
        resolve_table_column(_page("(亿元)"), table_index=9)


def test_unknown_unit_is_rejected() -> None:
    with pytest.raises(ValueError, match="未登记的表头单位"):
        resolve_table_column(_page("(吨)"), table_index=1, sub="金额")


def test_metric_switches_series_family() -> None:
    page = _page("(亿元)")
    column = resolve_table_column(page, table_index=1, metric="营业收入", sub="金额")
    parsed = parse_profit(
        page,
        period="2025-07",
        row=ROW,
        column=column.label,
        unit=column.unit,
        table_index=1,
        metric="营业收入",
    )
    assert parsed.series_id == "cn.nbs.industry_c31.revenue_ytd"


def test_metric_must_appear_in_column_name() -> None:
    page = _page("(亿元)")
    with pytest.raises(ValueError, match="must name the metric"):
        parse_profit(
            page,
            period="2025-07",
            row=ROW,
            column="利润总额 / 金额 / (亿元)",
            unit="亿元",
            table_index=1,
            metric="营业收入",
        )


def test_unknown_metric_is_rejected() -> None:
    with pytest.raises(ValueError, match="未登记的财务指标"):
        parse_profit(
            _page("(亿元)"),
            period="2025-07",
            row=ROW,
            column="利润总额 / 金额 / (亿元)",
            unit="亿元",
            table_index=1,
            metric="毛利",
        )
