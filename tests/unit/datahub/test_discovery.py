"""公告发现：标题正则、相对链接补全、年度公告折算、区间过滤。"""

from __future__ import annotations

import pytest

from industrypulse.datahub.discovery import discover, in_range

BASE = "https://www.stats.gov.cn/sj/zxfb/"


def _listing(*rows: tuple[str, str]) -> str:
    anchors = "".join(f'<li><a href="{href}">{title}</a></li>' for href, title in rows)
    return f"<html><body><ul>{anchors}</ul></body></html>"


def test_recognizes_three_kinds_and_absolutizes_urls() -> None:
    html = _listing(
        ("./202508/t1.html", "2025年1—7月份全国规模以上工业企业利润下降1.7%"),
        ("./202609/t2.html", "2026年9月中国采购经理指数运行情况"),
        ("./202610/t3.html", "2026年9月份规模以上工业增加值增长5.5%"),
        ("./202601/t4.html", "无关的新闻：统计局召开新闻发布会"),
    )
    found = discover(html, base_url=BASE)
    assert [(item.kind, item.period) for item in found] == [
        ("profit", "2025-07"),
        ("pmi", "2026-09"),
        ("iva", "2026-09"),
    ]
    assert found[0].url == BASE + "202508/t1.html"


def test_annual_profit_maps_to_december() -> None:
    html = _listing(("./202601/t9.html", "2025年全国规模以上工业企业利润增长0.6%"))
    found = discover(html, base_url=BASE, kinds=("profit",))
    assert [(item.kind, item.period) for item in found] == [("profit", "2025-12")]


def test_combined_january_february_maps_to_february() -> None:
    html = _listing(("./202603/t5.html", "2026年1—2月份规模以上工业增加值增长6.3%"))
    found = discover(html, base_url=BASE, kinds=("iva",))
    assert found[0].period == "2026-02"


def test_duplicates_keep_first_occurrence() -> None:
    html = _listing(
        ("./a.html", "2025年7月中国采购经理指数运行情况"),
        ("./b.html", "2025年7月中国采购经理指数运行情况"),
    )
    found = discover(html, base_url=BASE, kinds=("pmi",))
    assert len(found) == 1 and found[0].url == BASE + "a.html"


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="未支持的公告类别"):
        discover(_listing(), base_url=BASE, kinds=("gdp",))


def test_in_range_filters_and_validates() -> None:
    html = _listing(
        ("./a.html", "2025年6月中国采购经理指数运行情况"),
        ("./b.html", "2025年7月中国采购经理指数运行情况"),
        ("./c.html", "2025年8月中国采购经理指数运行情况"),
    )
    found = discover(html, base_url=BASE, kinds=("pmi",))
    assert [item.period for item in in_range(found, "2025-07", "2025-08")] == [
        "2025-07",
        "2025-08",
    ]
    with pytest.raises(ValueError, match="YYYY-MM"):
        in_range(found, "2025-7", "2025-08")
    with pytest.raises(ValueError, match="起始期"):
        in_range(found, "2025-09", "2025-08")
