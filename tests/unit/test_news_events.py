from datetime import date

from industrypulse.intelligence.event_book import EventBook
from industrypulse.intelligence.events import (
    apply_extraction,
    build_brief,
    cluster_stories,
    same_report,
    validate_extraction,
)


def story(title: str, summary: str, url: str, published: str = "2026-10-23T02:00:00+00:00") -> dict:
    return {
        "id": url[-6:],
        "sector": "semiconductor",
        "title": title,
        "summary": summary,
        "url": url,
        "source": "来源",
        "published_at": published,
    }


def test_reprints_merge_and_different_subjects_stay_apart() -> None:
    first = "宁德时代签订储能大单"
    second = "【财联社】宁德时代签订储能大单，金额超百亿"
    other = "欧洲央行维持利率不变"
    assert same_report(first, "宁德时代签订储能大单")
    groups = cluster_stories(
        [
            story(first, "公司公告", "https://a.example/1"),
            story(second, "公司公告称金额超过一百亿元", "https://b.example/2"),
            story(other, "政策维持", "https://c.example/3"),
        ]
    )
    assert len(groups) == 2
    assert {len(group) for group in groups} == {1, 2}


def test_unsupported_number_and_paraphrase_are_cleared() -> None:
    members = [
        story("宁德时代签约", "宁德时代签订储能订单，金额为 16.5 亿元", "https://a.example/1")
    ]
    kept = validate_extraction(
        members,
        {
            "quote": "金额为 16.5 亿元",
            "subject": "宁德时代",
            "action": "金额为",
            "quantity": 16.5,
            "unit": "亿元",
        },
    )
    assert kept["status"] == "EXTRACTED"
    assert kept["quantity"] == 16.5
    invented = validate_extraction(
        members,
        {
            "quote": "宁德时代签订储能订单",
            "subject": "比亚迪",
            "action": "大幅扩张",
            "quantity": 20,
            "unit": "亿美元",
        },
    )
    assert invented["quantity"] is None
    assert invented["subject"] is None
    assert invented["action"] is None
    assert invented["unit"] is None
    year = validate_extraction(
        [story("行业展望", "到 2029 年之前产能不太可能全面过剩", "https://a.example/9")],
        {
            "quote": "到 2029 年之前产能不太可能全面过剩",
            "subject": None,
            "action": "产能不太可能全面过剩",
            "quantity": 2029,
            "unit": "年",
        },
    )
    assert year["quantity"] is None and year["unit"] is None
    rejected = validate_extraction(members, {"quote": "这句不在原文里", "quantity": 16.5})
    assert rejected["status"] == "REJECTED"
    assert rejected["quantity"] is None


def test_brief_uses_only_validated_events_and_keeps_sources() -> None:
    stories = [
        story("宁德时代签订储能大单", "金额为 16.5 亿元", "https://a.example/1"),
        story("【快讯】宁德时代签订储能大单", "转载", "https://b.example/2"),
    ]
    members = cluster_stories(stories)[0]

    def complete(prompt: str) -> str:
        assert "16.5" in prompt
        return (
            '{"quote":"金额为 16.5 亿元","subject":"宁德时代",'
            '"action":"金额为","quantity":16.5,"unit":"亿元"}'
        )

    event = apply_extraction(members, complete)
    book = EventBook.__new__(EventBook)
    book.records = {}
    book.records[event["cluster_id"]] = {"fingerprint": event["fingerprint"], "event": event}
    brief = build_brief(stories, "semiconductor", date(2026, 10, 23), book.records)
    assert brief["day"] == "2026-10-23"
    assert len(brief["events"]) == 1
    assert {item["url"] for item in brief["events"][0]["sources"]} == {
        "https://a.example/1",
        "https://b.example/2",
    }
    assert "16.5" not in brief["disclaimer"]
