"""把多篇报道收成一条事件。数字和主体必须能在引用句里逐字找到。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from industrypulse.contracts import TZ_SHANGHAI

_PUNCTUATION = re.compile(r"[\s\W_]+", re.UNICODE)
_PREFIX = re.compile(r"^(?:【[^】]{1,12}】|快讯[：:]|独家[｜|]|突发[：:]|刚刚[｜|])+")
_NUMBER = re.compile(r"(?<![\d.])[+-]?\d+(?:,\d{3})*(?:\.\d+)?(?![\d.])")


def normalize_title(title: str) -> str:
    text = _PREFIX.sub("", title.casefold())
    return _PUNCTUATION.sub("", text)


def _bigrams(text: str) -> set[str]:
    if len(text) < 2:
        return {text} if text else set()
    return {text[index : index + 2] for index in range(len(text) - 1)}


def same_report(left: str, right: str) -> bool:
    """标题归一化后，判断两篇报道是不是同一件事的转载。"""
    if not left or not right:
        return False
    if left == right:
        return True
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    if len(short) >= 8 and (
        long.startswith(short) or (short in long and len(short) / len(long) >= 0.72)
    ):
        return True
    if min(len(left), len(right)) < 8 or left[:4] != right[:4]:
        return False
    first, second = _bigrams(left), _bigrams(right)
    return len(first & second) / len(first | second) >= 0.72


def cluster_stories(stories: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    ordered = sorted(stories, key=lambda item: (item.get("published_at", ""), item.get("url", "")))
    groups: list[dict[str, Any]] = []
    for story in ordered:
        title = normalize_title(str(story.get("title", "")))
        placed = False
        for group in groups:
            if same_report(title, group["seed"]):
                group["items"].append(story)
                if len(title) > len(group["seed"]):
                    group["seed"] = title
                placed = True
                break
        if not placed:
            groups.append({"seed": title, "items": [story]})
    groups.sort(
        key=lambda group: max(item.get("published_at", "") for item in group["items"]), reverse=True
    )
    return [group["items"] for group in groups]


def cluster_id(members: Sequence[dict[str, Any]]) -> str:
    urls = "|".join(sorted(str(item.get("url", "")) for item in members))
    return hashlib.sha256(urls.encode()).hexdigest()[:24]


def fingerprint(members: Sequence[dict[str, Any]]) -> str:
    body = "\n".join(
        sorted(f"{item.get('title', '')}\n{item.get('summary', '')}" for item in members)
    )
    return hashlib.sha256(body.encode()).hexdigest()[:24]


def decimals_in(text: str) -> set[Decimal]:
    found = set()
    for token in _NUMBER.findall(text):
        try:
            found.add(Decimal(token.replace(",", "")))
        except InvalidOperation:
            continue
    return found


def _blank(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value.replace(",", "").strip())
        except InvalidOperation:
            return None
    return None


def validate_extraction(
    members: Sequence[dict[str, Any]], payload: dict[str, Any]
) -> dict[str, Any]:
    """引用必须来自某一篇原文。对不上的主体、动作和数字改回空值。"""
    quote = _blank(payload.get("quote")) or ""
    texts = [f"{item.get('title', '')} {item.get('summary', '')}" for item in members]
    base = describe_cluster(members)
    if not quote or not any(quote in text for text in texts):
        base.update(
            {
                "status": "REJECTED",
                "note": "模型给出的引用不在原文里，这条事件没有采用",
            }
        )
        return base
    missing: list[str] = []
    subject = _blank(payload.get("subject"))
    action = _blank(payload.get("action"))
    if subject is None:
        missing.append("引用里没有写出主体")
    elif subject not in quote:
        subject = None
        missing.append("主体用词不在引用句里")
    if action is None:
        missing.append("引用里没有单独标出动作")
    elif action not in quote:
        action = None
        missing.append("动作不是引用里的原文")
    number = _decimal(payload.get("quantity"))
    if payload.get("quantity") in (None, ""):
        number = None
        missing.append("引用里没有可核对的数字")
    elif number is None or number not in decimals_in(quote):
        number = None
        missing.append("这个数字没有出现在引用里")
    unit = _blank(payload.get("unit"))
    if number is None:
        unit = None
    elif unit is None:
        missing.append("引用里没有写出单位")
    elif unit not in quote:
        unit = None
        missing.append("单位没有出现在引用里")
    base.update(
        {
            "status": "EXTRACTED",
            "subject": subject,
            "action": action,
            "quantity": None if number is None else float(number),
            "unit": unit,
            "quote": quote,
            "missing": missing,
            "note": "；".join(missing),
        }
    )
    return present_event(base)


def _sources(members: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    sources = []
    for item in members:
        url = str(item.get("url", ""))
        if not url or url in seen:
            continue
        seen.add(url)
        sources.append(
            {
                "name": str(item.get("source") or "原文"),
                "url": url,
                "story_id": str(item.get("id", "")),
            }
        )
    return sources


def describe_cluster(members: Sequence[dict[str, Any]]) -> dict[str, Any]:
    published = [str(item.get("published_at", "")) for item in members if item.get("published_at")]
    return {
        "cluster_id": cluster_id(members),
        "fingerprint": fingerprint(members),
        "titles": [str(item.get("title", "")) for item in members],
        "sources": _sources(members),
        "published_at": max(published) if published else "",
        "status": "UNEXTRACTED",
        "subject": None,
        "action": None,
        "quantity": None,
        "unit": None,
        "quote": None,
        "missing": ["尚未用已配置模型抽取"],
        "note": "同一件事已合并，事实字段仍空着",
    }


def present_event(event: dict[str, Any]) -> dict[str, Any]:
    """年份写在原文里也不当成这件事的数量。"""
    quantity = event.get("quantity")
    if (
        event.get("unit") == "年"
        and isinstance(quantity, int | float)
        and 1900 <= float(quantity) <= 2100
    ):
        event["quantity"] = None
        event["unit"] = None
        missing = [item for item in event.get("missing") or [] if item != "年份不当作事件数量"]
        missing.append("年份不当作事件数量")
        event["missing"] = missing
        event["note"] = "；".join(str(item) for item in missing)
    return event


def parse_model_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        raise ValueError("模型没有返回 JSON 对象")
    payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("模型返回的不是一个 JSON 对象")
    return payload


def extraction_prompt(members: Sequence[dict[str, Any]]) -> str:
    blocks = []
    for index, item in enumerate(members, start=1):
        title = item.get("title", "")
        summary = item.get("summary", "")
        url = item.get("url", "")
        blocks.append(f"{index}. 标题：{title}\n摘要：{summary}\n链接：{url}")
    return (
        "这些报道讲的是同一件事。只输出一个 JSON 对象，不要 Markdown。\n"
        "quote 必须从某一篇标题或摘要里原样复制。subject、action、quantity、unit "
        "只能使用 quote 里已经出现的词和数字；原文没有就填 null。不要换算，不要估算。\n"
        "字段：quote, subject, action, quantity, unit\n\n" + "\n\n".join(blocks)
    )


def shanghai_day(value: str) -> date | None:
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=TZ_SHANGHAI)
    return moment.astimezone(TZ_SHANGHAI).date()


def stories_for_day(
    stories: Sequence[dict[str, Any]], sector: str, day: date | None
) -> tuple[date | None, list[dict[str, Any]]]:
    grouped: dict[date, list[dict[str, Any]]] = {}
    for story in stories:
        if story.get("sector") != sector:
            continue
        published = shanghai_day(str(story.get("published_at", "")))
        if published is None:
            continue
        grouped.setdefault(published, []).append(story)
    if not grouped:
        return day, []
    chosen = day if day in grouped else max(grouped)
    return chosen, grouped[chosen]


def build_brief(
    stories: Sequence[dict[str, Any]],
    sector: str,
    day: date | None,
    cached: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    chosen, selected = stories_for_day(stories, sector, day)
    clusters = []
    saved = cached or {}
    for members in cluster_stories(selected):
        view = describe_cluster(members)
        record = saved.get(view["cluster_id"])
        if record and record.get("fingerprint") == view["fingerprint"] and record.get("event"):
            view.update(record["event"])
            present_event(view)
        clusters.append(view)
    events = [item for item in clusters if item["status"] == "EXTRACTED"]
    label = chosen.isoformat() if chosen else ""
    return {
        "day": label,
        "requested_day": None if day is None else day.isoformat(),
        "sector": sector,
        "timezone": "Asia/Shanghai",
        "disclaimer": (
            "简报只转述已在原文引用里核对过的字段。"
            "对不上的主体、动作和数字保持空白。这不是投资建议。"
        ),
        "clusters": clusters,
        "events": events,
    }


def apply_extraction(
    members: Sequence[dict[str, Any]], complete: Callable[[str], str]
) -> dict[str, Any]:
    try:
        payload = parse_model_json(complete(extraction_prompt(members)))
    except (ValueError, json.JSONDecodeError) as exc:
        view = describe_cluster(members)
        view.update({"status": "REJECTED", "note": str(exc)})
        return view
    return validate_extraction(members, payload)
