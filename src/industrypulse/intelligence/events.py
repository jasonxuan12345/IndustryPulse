"""把多篇报道收成一条事件。数字和主体必须能在引用句里逐字找到。"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from industrypulse.core import TZ_SHANGHAI
from industrypulse.intelligence.feeds import canonical_url
from industrypulse.intelligence.service import write_json

_PUNCTUATION = re.compile(r"[\s\W_]+", re.UNICODE)
_PREFIX = re.compile(r"^(?:【[^】]{1,12}】|快讯[：:]|独家[｜|]|突发[：:]|刚刚[｜|])+")
_NUMBER = re.compile(r"(?<![\d.])[+-]?\d+(?:,\d{3})*(?:\.\d+)?(?![\d.])")
_STAGES = (
    (
        "planned",
        re.compile(
            r"(?<!模)拟(?:签|与|向|收购|终止|取消|筹)|计划(?:签|与|收购|终止|取消)|筹划|意向"
        ),
    ),
    ("terminated", re.compile(r"终止|取消|撤回|解除协议")),
    ("completed", re.compile(r"正式|已签|落地|完成|签订|签署")),
)
_INDEX_LOCK = Lock()
_WINDOW_SECONDS = 3 * 24 * 60 * 60


def normalize_title(title: str) -> str:
    text = _PREFIX.sub("", title.casefold())
    return _PUNCTUATION.sub("", text)


def _bigrams(text: str) -> set[str]:
    if len(text) < 2:
        return {text} if text else set()
    return {text[index : index + 2] for index in range(len(text) - 1)}


@lru_cache(maxsize=16384)
def _stage(title: str) -> str:
    return next((name for name, pattern in _STAGES if pattern.search(title)), "")


def _stage_conflicts(left: str, right: str) -> bool:
    a, b = _stage(left), _stage(right)
    return bool(a and b and a != b)


def same_report(left: str, right: str) -> bool:
    """标题归一化后，判断两篇报道是不是同一件事的转载。"""
    if not left or not right or _stage_conflicts(left, right):
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


@lru_cache(maxsize=16384)
def _event_url(value: str) -> str:
    url = urlsplit(canonical_url(value))
    query = [(key, val) for key, val in parse_qsl(url.query) if (key, val) != ("f", "rss")]
    return urlunsplit((url.scheme, url.netloc.removeprefix("www."), url.path, urlencode(query), ""))


def _moment(value: str) -> float | None:
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=TZ_SHANGHAI)).timestamp()


def _nearby(left: float | None, right: float | None) -> bool:
    return left is None or right is None or abs(left - right) <= _WINDOW_SECONDS


class _Candidates:
    """Find possible matches without comparing every pair in the whole feed."""

    def __init__(self, titles: Sequence[str]) -> None:
        self.prefixes = {title[:8] for title in titles if len(title) >= 8}
        self.lookup: dict[str, set[int]] = defaultdict(set)

    def keys(self, title: str, urls: set[str]) -> set[str]:
        # same_report uses a four-character prefix for fuzzy matches. For containment
        # the shorter title's eight-character prefix appears inside the longer title.
        return (
            {f"url:{url}" for url in urls}
            | {f"prefix:{title[:4]}"}
            | {
                f"anchor:{part}"
                for pos in range(len(title) - 7)
                if (part := title[pos : pos + 8]) in self.prefixes
            }
        )

    def find(self, keys: set[str]) -> list[int]:
        return sorted({index for key in keys for index in self.lookup.get(key, ())})

    def add(self, index: int, keys: set[str]) -> None:
        for key in keys:
            self.lookup[key].add(index)


def cluster_stories(stories: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    ordered = sorted(stories, key=lambda item: (item.get("published_at", ""), item.get("url", "")))
    titles = [normalize_title(str(item.get("title") or "")) for item in ordered]
    candidates = _Candidates(titles)
    groups: list[dict[str, Any]] = []
    for story, title in zip(ordered, titles, strict=True):
        url = _event_url(str(story.get("url") or ""))
        urls = {url} if url else set()
        keys = candidates.keys(title, urls)
        when = _moment(str(story.get("published_at") or ""))
        for index in candidates.find(keys):
            group = groups[index]
            if not _nearby(when, group["time"]) or _stage_conflicts(title, group["stage_title"]):
                continue
            if (url and url in group["urls"]) or same_report(title, group["seed"]):
                group["items"].append(story)
                if url:
                    group["urls"].add(url)
                if _stage(title):
                    group["stage_title"] = title
                if len(title) > len(group["seed"]):
                    group["seed"] = title
                candidates.add(index, keys)
                break
        else:
            candidates.add(len(groups), keys)
            groups.append(
                {
                    "seed": title,
                    "stage_title": title,
                    "items": [story],
                    "time": when,
                    "urls": urls,
                }
            )
    groups.sort(
        key=lambda group: max(item.get("published_at", "") for item in group["items"]), reverse=True
    )
    return [group["items"] for group in groups]


def cluster_id(members: Sequence[dict[str, Any]]) -> str:
    urls = "|".join(sorted(str(item.get("url", "")) for item in members))
    return hashlib.sha256(urls.encode()).hexdigest()[:24]


def identified_clusters(
    stories: Sequence[dict[str, Any]], path: Path
) -> list[tuple[str, list[dict[str, Any]]]]:
    """保留首次见到的编号，后续用来源别名和标题匹配同一时间段的事件。"""
    groups = cluster_stories(stories)
    if not groups:
        return []
    titles_by_group = [
        [normalize_title(str(member.get("title") or "")) for member in members]
        for members in groups
    ]
    with _INDEX_LOCK:
        index = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        registry_ids = list(index)
        registry_slots = {key: slot for slot, key in enumerate(registry_ids)}
        anchors = [item["title"] for item in index.values()]
        candidates = _Candidates(anchors + [max(titles, key=len) for titles in titles_by_group])
        for slot, item in enumerate(index.values()):
            candidates.add(slot, candidates.keys(item["title"], set(item["urls"])))
        result: dict[str, list[dict[str, Any]]] = {}
        changed = False
        for members, titles in zip(groups, titles_by_group, strict=True):
            first = members[0]
            published = str(first.get("published_at") or "")
            moment = _moment(published)
            title = max(titles, key=len)
            stage_title = next((value for value in titles if _stage(value)), title)
            urls = {url for member in members if (url := _event_url(str(member.get("url") or "")))}
            keys = candidates.keys(title, urls)
            matches = [
                (key, item)
                for slot in candidates.find(keys)
                for key in [registry_ids[slot]]
                for item in [index[key]]
                if _nearby(moment, item["time"])
                and not _stage_conflicts(stage_title, item.get("stage_title", item["title"]))
            ]
            key = next((key for key, item in matches if urls.intersection(item["urls"])), None)
            if key is None:
                key = next(
                    (key for key, item in matches if same_report(title, item["title"])), None
                )
            if key is None:
                key = cluster_id(members)
                if key in index:
                    key = hashlib.sha256(f"{key}:{title}:{published}".encode()).hexdigest()[:24]
                index[key] = {
                    "title": title,
                    "stage_title": stage_title,
                    "published_at": published,
                    "time": moment,
                    "urls": [],
                }
                registry_slots[key] = len(registry_ids)
                registry_ids.append(key)
                candidates.add(registry_slots[key], keys)
                changed = True
            item = index[key]
            all_urls = sorted(set(item["urls"]) | urls)
            if all_urls != item["urls"]:
                candidates.add(registry_slots[key], candidates.keys(item["title"], urls))
                item["urls"] = all_urls
                changed = True
            if not _stage(item["stage_title"]) and _stage(stage_title):
                item["stage_title"] = stage_title
                changed = True
            if moment is not None and (item["time"] is None or moment < item["time"]):
                item["published_at"], item["time"] = published, moment
                changed = True
            result.setdefault(key, []).extend(members)
        if changed:
            write_json(path, index)
        return [
            (
                key,
                [
                    {**member, "event_published_at": index[key]["published_at"]}
                    for member in members
                ],
            )
            for key, members in result.items()
        ]


def radar_events(stories: Sequence[dict[str, Any]], path: Path) -> list[dict[str, Any]]:
    events = []
    for event_id, members in identified_clusters(stories, path):
        sectors = dict.fromkeys(str(item["sector"]) for item in members if item.get("sector"))
        primary = next((item for item in members if item.get("summary")), members[0])
        sources = _sources(members)
        for sector in sectors:
            events.append(
                {
                    **primary,
                    "sector": sector,
                    "id": event_id,
                    "cluster_id": event_id,
                    "published_at": members[0]["event_published_at"],
                    "sources": sources,
                }
            )
    return events


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
    *,
    index_path: Path | None = None,
) -> dict[str, Any]:
    chosen, selected = stories_for_day(stories, sector, day)
    clusters = []
    saved = cached or {}
    groups = (
        identified_clusters(selected, index_path)
        if index_path
        else [(cluster_id(group), group) for group in cluster_stories(selected)]
    )
    for event_id, members in groups:
        view = describe_cluster(members)
        record = saved.get(event_id) or saved.get(view["cluster_id"])
        if (extracted := recover_extraction(members, record)) is not None:
            view.update(extracted)
        elif record and record.get("fingerprint") == view["fingerprint"] and record.get("event"):
            view.update(record["event"])
            present_event(view)
        view["cluster_id"] = event_id
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


def recover_extraction(
    members: Sequence[dict[str, Any]], record: dict[str, Any] | None
) -> dict[str, Any] | None:
    event = (record or {}).get("event") or {}
    if event.get("status") != "EXTRACTED":
        return None
    checked = validate_extraction(members, event)
    return checked if checked["status"] == "EXTRACTED" else None


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
