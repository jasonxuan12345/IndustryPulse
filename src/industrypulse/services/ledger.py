"""前瞻账本：追加版本、冻结判断和评估口径，研究假设不冒充交易建议。"""

from __future__ import annotations

import calendar
import hashlib
import json
import math
import os
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from industrypulse.contracts import TZ_SHANGHAI
from industrypulse.services.etf_audit import MAX_DIVIDEND_YIELD, find_barriers

ROOT = Path(__file__).resolve().parents[3]
LEDGER_PATH = ROOT / "data/ledger/recommendations.jsonl"
TOOL_JOURNAL = ROOT / "data/research/tool-journal.jsonl"
DEFAULT_POLICY: dict[str, Any] = {
    "version": "forward-ledger-v2",
    "commission_bps": 3.0,
    "slippage_bps": 5.0,
    "benchmark_id": "ETF_510300",
    "max_entry_gap_days": 10,
    "entry": "登记日之后首个共同交易日开盘",
    "exit": "入场日起满日历月后的首个共同交易日收盘",
    "dividends": "现金分红加回不再投资；入场除息日不享受分红",
    "evaluation": "成熟资产窗口费后超额收益<=0为误报；缺行情、未成熟不计分",
}
IMMUTABLE = (
    "recommendation_id",
    "created_at",
    "source_at",
    "source",
    "category",
    "subject",
    "statement",
    "industries",
    "asset",
    "horizon_months",
    "policy",
    "evidence",
    "invalidation_conditions",
    "next_verification",
    "source_snapshot",
    "model_status",
)
TERMINAL = {"evaluated", "closed_withdrawn", "confirmed", "refuted", "expired_unverified"}


class LedgerIntegrityError(ValueError):
    """账本损坏时停止写入，不静默跳行或重建历史。"""


def _moment(now: datetime | None = None) -> datetime:
    value = now or datetime.now(TZ_SHANGHAI)
    if value.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return value.astimezone(TZ_SHANGHAI)


def add_months(day: date, months: int) -> date:
    year, month_zero = divmod(day.year * 12 + day.month - 1 + months, 12)
    month = month_zero + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(path) + ".lease.sqlite3", timeout=30)) as lease:
        lease.execute("CREATE TABLE IF NOT EXISTS writer_lease(id INTEGER PRIMARY KEY)")
        lease.execute("BEGIN IMMEDIATE")
        try:
            yield
        finally:
            lease.rollback()


def _read(path: Path) -> list[dict[str, Any]]:
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    if content and not content.endswith("\n"):
        raise LedgerIntegrityError("账本末行不完整，停止读写")
    records: list[dict[str, Any]] = []
    latest: dict[str, dict[str, Any]] = {}
    previous = "0" * 64
    for index, line in enumerate(content.splitlines(), 1):
        try:
            record = json.loads(line)
            actual = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record["schema_version"] != 2
                or record["sequence"] != index
                or record["previous_hash"] != previous
                or _hash(actual) != record["record_hash"]
            ):
                raise ValueError("hash / sequence mismatch")
            old = latest.get(record["recommendation_id"])
            if record["version"] != (old["version"] + 1 if old else 1):
                raise ValueError("version mismatch")
            if old and any(record[key] != old[key] for key in IMMUTABLE):
                raise ValueError("frozen definition changed")
            if old and datetime.fromisoformat(record["at"]) < datetime.fromisoformat(old["at"]):
                raise ValueError("time moved backwards")
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise LedgerIntegrityError(f"账本第{index}行损坏或为不支持的旧格式：{exc}") from exc
        records.append(record)
        latest[record["recommendation_id"]] = record
        previous = record["record_hash"]
    head = path.with_suffix(".head.json")
    if head.exists():
        try:
            if json.loads(head.read_text(encoding="utf-8")) != {
                "count": len(records),
                "hash": previous,
            }:
                raise ValueError("head mismatch")
        except (ValueError, TypeError) as exc:
            raise LedgerIntegrityError("账本与持久链头不一致，可能被截断；停止写入") from exc
    elif records:
        raise LedgerIntegrityError("账本缺少持久链头，停止写入")
    return records


def _append(path: Path, history: list[dict[str, Any]], drafts: list[dict[str, Any]]) -> int:
    if not drafts:
        return 0
    latest = {record["recommendation_id"]: record for record in history}
    previous = history[-1]["record_hash"] if history else "0" * 64
    completed: list[dict[str, Any]] = []
    for draft in drafts:
        record = _copy(draft)
        # 旧版本的链信息不得进入新版本的指纹计算。
        record.pop("record_hash", None)
        old = latest.get(record["recommendation_id"])
        if old and any(record[key] != old[key] for key in IMMUTABLE):
            raise ValueError("禁止改写原始判断、证据或评估口径")
        if old and datetime.fromisoformat(record["at"]) < datetime.fromisoformat(old["at"]):
            raise ValueError("不允许回填或倒退账本时间")
        record.update(
            schema_version=2,
            sequence=len(history) + len(completed) + 1,
            version=old["version"] + 1 if old else 1,
            previous_hash=previous,
        )
        record["record_id"] = f"{record['recommendation_id']}-v{record['version']}"
        record["record_hash"] = _hash(record)
        previous = record["record_hash"]
        completed.append(record)
        latest[record["recommendation_id"]] = record
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(
            "".join(
                json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
                for record in completed
            )
        )
        stream.flush()
        os.fsync(stream.fileno())
    head = path.with_suffix(".head.json")
    temporary = head.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump({"count": len(history) + len(completed), "hash": previous}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(head)
    return len(completed)


def load_ledger(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or LEDGER_PATH
    with _lock(target):
        return _read(target)


def latest_versions(path: Path | None = None) -> dict[str, dict[str, Any]]:
    return {record["recommendation_id"]: record for record in load_ledger(path)}


def recommendation_id(source_kind: str, source_id: str, asset: str = "", months: int = 0) -> str:
    return "rec-" + _hash([source_kind, source_id, asset, months])[:20]


def register(
    *,
    source_kind: str,
    source_id: str,
    category: str,
    subject: str,
    statement: str,
    industries: list[dict[str, Any]] | None = None,
    asset: str = "",
    horizon_months: int = 3,
    evidence: list[str] | None = None,
    invalidation_conditions: list[str] | None = None,
    source_at: str | None = None,
    source_snapshot: dict[str, Any] | None = None,
    next_verification: str = "",
    policy: dict[str, Any] | None = None,
    now: datetime | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    moment = _moment(now)
    if category not in {
        "asset_candidate",
        "economic_hypothesis",
        "leading_event",
        "screening_observation",
    }:
        raise ValueError("未知账本类别")
    if not source_id or not statement.strip():
        raise ValueError("来源ID和原始判断必填")
    if category == "asset_candidate" and (
        not asset or horizon_months not in (3, 6) or not evidence or not invalidation_conditions
    ):
        raise ValueError("资产候选必须有资产、3/6个月窗口、证据和撤销条件")
    if not 0 <= horizon_months <= 12:
        raise ValueError("观察期必须在0至12个月")
    if source_at and _moment(datetime.fromisoformat(source_at)) > moment:
        raise ValueError("未来来源不得登记")
    frozen_policy = _copy(policy or DEFAULT_POLICY)
    if any(not str(item).strip() for item in (evidence or []) + (invalidation_conditions or [])):
        raise ValueError("证据和撤销条件不能是空白")
    if asset and asset == frozen_policy["benchmark_id"]:
        raise ValueError("资产候选不得与比较基准相同")
    for key in ("commission_bps", "slippage_bps"):
        if (
            not math.isfinite(float(frozen_policy[key]))
            or not 0 <= float(frozen_policy[key]) < 10000
        ):
            raise ValueError("费用必须是有效的非负bps且小于10000")
    identifier = recommendation_id(source_kind, source_id, asset, horizon_months)
    target = path or LEDGER_PATH
    with _lock(target):
        history = _read(target)
        old = next(
            (record for record in reversed(history) if record["recommendation_id"] == identifier),
            None,
        )
        if old:
            if old["statement"] != statement or old["category"] != category:
                raise ValueError("来源ID已登记不同判断，请使用新来源ID保留修订")
            return {"created": False, "record": old}
        record = {
            "recommendation_id": identifier,
            "created_at": moment.isoformat(),
            "at": moment.isoformat(),
            "source_at": source_at,
            "source": {"kind": source_kind, "id": source_id},
            "category": category,
            "subject": subject,
            "statement": statement,
            "industries": industries or [],
            "asset": asset,
            "horizon_months": horizon_months,
            "policy": frozen_policy,
            "evidence": evidence or [],
            "invalidation_conditions": invalidation_conditions or [],
            "next_verification": next_verification,
            "source_snapshot": source_snapshot or {},
            "model_status": "NOT_VALIDATED",
            "action": "registered",
            "status": "watching",
            "review_due": add_months(moment.date(), horizon_months).isoformat()
            if horizon_months
            else None,
            "metrics": None,
            "verification": None,
        }
        _append(target, history, [record])
        return {"created": True, "record": _read(target)[-1]}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        if path.exists()
        else []
    )


def seed(
    *,
    now: datetime | None = None,
    path: Path | None = None,
    hypotheses_path: Path | None = None,
    events_path: Path | None = None,
) -> dict[str, Any]:
    """历史材料今天补记；不回填创建时间，不把假设或queued事件当买入推荐。"""
    moment = _moment(now)
    created = 0
    skipped = []
    for category, file in (
        ("economic_hypothesis", hypotheses_path or ROOT / "data/research/hypotheses.jsonl"),
        ("leading_event", events_path or ROOT / "data/leading/events.jsonl"),
    ):
        for source in _jsonl(file):
            if category == "leading_event" and source.get("ai_status") != "queued":
                continue
            source_id = source.get("hypothesis_id") or source.get("event_id")
            source_at = source.get("created_at") or source.get("first_seen_at")
            if not source_id or (source_at and _moment(datetime.fromisoformat(source_at)) > moment):
                skipped.append(source_id)
                continue
            result = register(
                source_kind="ai_hypothesis"
                if category == "economic_hypothesis"
                else "leading_event",
                source_id=str(source_id),
                category=category,
                subject=str(source.get("chain_name") or source.get("chain") or ""),
                statement=str(source.get("statement") or source.get("what_changed") or ""),
                industries=source.get("industries") or [],
                horizon_months=int(source.get("observation_months") or 3)
                if category == "economic_hypothesis"
                else 0,
                source_at=source_at,
                source_snapshot=source,
                evidence=sorted(
                    {str(f["source_url"]) for f in source.get("facts", []) if f.get("source_url")}
                ),
                invalidation_conditions=source.get("invalidation_conditions") or [],
                next_verification=str(source.get("next_verification") or ""),
                now=moment,
                path=path,
            )
            created += result["created"]
    return {"seeded": created, "total_known": len(latest_versions(path)), "skipped": skipped}


def recommendation_metrics(
    market: Any,
    asset_id: str,
    benchmark_id: str,
    *,
    from_day: date,
    to_day: date,
    horizon_months: int = 3,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """费用、现金分红和峰谷回撤；成熟后截断原定终点，不随之后行情漂移。"""
    rules = policy or DEFAULT_POLICY
    by_asset = {}
    for aid in (asset_id, benchmark_id):
        ordered = sorted(
            (b for b in market.bars(aid) if b.trade_date <= to_day), key=lambda b: b.trade_date
        )
        by_asset[aid] = {bar.trade_date: bar for bar in ordered}
    asset_bars, benchmark_bars = by_asset[asset_id], by_asset[benchmark_id]
    common = sorted(set(asset_bars) & set(benchmark_bars))
    entries = [day for day in common if day > from_day]
    if not entries:
        return {
            "status": "pending_entry",
            "mature": False,
            "reason": "等待登记日之后的共同交易日行情",
        }
    entry = entries[0]
    if (entry - from_day).days > rules["max_entry_gap_days"]:
        return {
            "status": "blocked",
            "mature": False,
            "reason": "入场数据缺口超过上限，不用迟到行情伪造入场",
        }
    due = add_months(entry, horizon_months)
    exits = sorted(day for day in benchmark_bars if day >= due)
    exit_day = exits[0] if exits else None
    final = exit_day or max(benchmark_bars)
    expected = sorted(day for day in benchmark_bars if entry <= day <= final)
    if any(day not in asset_bars for day in expected):
        return {
            "status": "blocked",
            "mature": False,
            "reason": "持有期资产行情相对基准缺报",
            "review_due": due.isoformat(),
        }
    fingerprints = []
    for aid, seq in ((asset_id, asset_bars), (benchmark_id, benchmark_bars)):
        window = [seq[day] for day in expected]
        if any(
            getattr(bar, "adj_method", "none") != "none" or float(getattr(bar, "volume", 1)) <= 0
            for bar in window
        ):
            return {"status": "blocked", "mature": False, "reason": f"{aid}存在复权或零成交量行情"}
        actions = [a for a in market.actions(aid) if entry < a.ex_date <= final]
        if any(a.action_type != "cash_dividend" for a in actions) or find_barriers(window, actions):
            return {
                "status": "blocked",
                "mature": False,
                "reason": f"{aid}存在未核实拆并或不支持的公司行动",
            }
        for action in actions:
            previous = [day for day in seq if day < action.ex_date]
            if (
                previous
                and float(action.distribution_per_unit) / float(seq[max(previous)].close)
                > MAX_DIVIDEND_YIELD
            ):
                return {"status": "blocked", "mature": False, "reason": f"{aid}现金分红金额异常"}
        fingerprints.append(
            {
                "asset": aid,
                "bars": [[str(b.trade_date), str(b.open), str(b.close)] for b in window],
                "dividends": [[str(a.ex_date), str(a.distribution_per_unit)] for a in actions],
            }
        )
    c, s = float(rules["commission_bps"]) / 10000, float(rules["slippage_bps"]) / 10000

    def curve(aid: str, seq: dict[date, Any]) -> list[float]:
        cash = 0.0
        actions = [a for a in market.actions(aid) if a.action_type == "cash_dividend"]
        values = []
        entry_cost = float(seq[entry].open) * (1 + s) * (1 + c)
        for day in expected:
            cash += sum(
                float(a.distribution_per_unit) for a in actions if a.ex_date == day and day > entry
            )
            values.append((float(seq[day].close) * (1 - s) * (1 - c) + cash) / entry_cost)
        return values

    asset_values, benchmark_values = (
        curve(asset_id, asset_bars),
        curve(benchmark_id, benchmark_bars),
    )
    peak, drawdown = 1.0, 0.0
    for value in asset_values:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    excess = [a - b for a, b in zip(asset_values, benchmark_values, strict=True)]
    return {
        "status": "mature" if exit_day else "observing",
        "mature": exit_day is not None,
        "entry_day": entry.isoformat(),
        "review_due": due.isoformat(),
        "as_of": final.isoformat(),
        "exit_day": exit_day.isoformat() if exit_day else None,
        "net_return_pct": (asset_values[-1] - 1) * 100,
        "benchmark_return_pct": (benchmark_values[-1] - 1) * 100,
        "excess_pct": excess[-1] * 100,
        "max_drawdown_pct": drawdown * 100,
        "mae_pct": min(0.0, min(asset_values) - 1) * 100,
        "excess_mae_pct": min(0.0, min(excess)) * 100,
        "days_held": (final - entry).days,
        "data_sha256": _hash(fingerprints),
        "return_convention": "开盘入场、估值日收盘假设平仓，双边费后；现金分红加回，现金利率0",
    }


def update(market: Any, *, now: datetime | None = None, path: Path | None = None) -> dict[str, Any]:
    moment = _moment(now)
    price_day = moment.date() if moment.time() >= time(15) else moment.date() - timedelta(days=1)
    target = path or LEDGER_PATH
    with _lock(target):
        history = _read(target)
        drafts = []
        for current in {r["recommendation_id"]: r for r in history}.values():
            if current["status"] in TERMINAL or current["category"] in {
                "leading_event",
                "screening_observation",
            }:
                continue
            if datetime.fromisoformat(current["created_at"]) > moment:
                continue
            record = _copy(current)
            if current["category"] == "asset_candidate":
                metrics = recommendation_metrics(
                    market,
                    current["asset"],
                    current["policy"]["benchmark_id"],
                    from_day=datetime.fromisoformat(current["created_at"]).date(),
                    to_day=price_day,
                    horizon_months=current["horizon_months"],
                    policy=current["policy"],
                )
                record["metrics"] = metrics
                record["review_due"] = metrics.get("review_due", current["review_due"])
                if metrics["mature"]:
                    record["status"] = (
                        "closed_withdrawn" if current["status"] == "withdrawn" else "evaluated"
                    )
                    record["evaluation"] = (
                        "false_positive" if metrics["excess_pct"] <= 0 else "positive_excess"
                    )
                elif current["status"] != "withdrawn":
                    record["status"] = (
                        "awaiting_data"
                        if metrics["status"] in {"blocked", "pending_entry"}
                        or moment.date() >= date.fromisoformat(record["review_due"])
                        else "watching"
                    )
            elif current["review_due"] and moment.date() >= date.fromisoformat(
                current["review_due"]
            ):
                if current["status"] == "withdrawn":
                    record["status"] = "closed_withdrawn"
                elif (current.get("verification") or {}).get("outcome") == "confirmed":
                    record["status"] = "confirmed"
                else:
                    record["status"] = "expired_unverified"
            if (
                record["metrics"] == current["metrics"]
                and record["status"] == current["status"]
                and current["at"][:10] == moment.date().isoformat()
            ):
                continue
            record.update(
                at=moment.isoformat(),
                action="evaluation" if record["status"] in TERMINAL else "snapshot",
            )
            drafts.append(record)
        appended = _append(target, history, drafts)
    return {
        "appended": appended,
        "evaluated": sum(d["status"] in {"evaluated", "closed_withdrawn"} for d in drafts),
    }


def mark(
    identifier: str,
    action: str,
    *,
    reason: str,
    evidence: str = "",
    event_at: str = "",
    now: datetime | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    if action not in {"withdrawn", "confirmed", "refuted"} or not reason.strip():
        raise ValueError("只能撤销、确认或证伪，且必须记录理由")
    moment = _moment(now)
    target = path or LEDGER_PATH
    with _lock(target):
        history = _read(target)
        current = next((r for r in reversed(history) if r["recommendation_id"] == identifier), None)
        if current is None or current["status"] in TERMINAL:
            raise ValueError("记录不存在或已完成，不能事后改写结果")
        if current["status"] == "withdrawn":
            raise ValueError("记录已撤销，原定资产窗口仍会继续评估")
        if current["category"] not in {"economic_hypothesis", "asset_candidate"}:
            raise ValueError("领先事件和筛选快照不是可确认的推荐")
        record = _copy(current)
        if action in {"confirmed", "refuted"}:
            if current["category"] != "economic_hypothesis" or not evidence or not event_at:
                raise ValueError("确认/证伪只适用于经济假设，必须提供验证事件时间及证据")
            if moment.date() > date.fromisoformat(current["review_due"]):
                raise ValueError("经济观察窗口已过，不允许事后确认")
            released = _moment(datetime.fromisoformat(event_at))
            if not datetime.fromisoformat(
                current["created_at"]
            ) < released <= moment or released.date() > date.fromisoformat(current["review_due"]):
                raise ValueError("验证事件必须发生在真实登记之后、观察窗口内且不是未来")
            record["verification"] = {
                "event_at": released.isoformat(),
                "evidence": evidence,
                "outcome": action,
                "lead_hours": (
                    released - datetime.fromisoformat(current["created_at"])
                ).total_seconds()
                / 3600,
            }
        record.update(at=moment.isoformat(), action=action, status=action, reason=reason)
        if action == "confirmed" and moment.date() < date.fromisoformat(current["review_due"]):
            # 单次事件通过也不能提前完成原来的3/6月判断，继续观察到原定窗口。
            record["status"] = "watching"
        if action == "withdrawn":
            record["withdrawn_at"] = moment.isoformat()
        _append(target, history, [record])
        return _read(target)[-1]


def daily_report(*, now: datetime | None = None, path: Path | None = None) -> dict[str, Any]:
    moment = _moment(now)
    records = [r for r in load_ledger(path) if datetime.fromisoformat(r["at"]) <= moment]
    latest = list({r["recommendation_id"]: r for r in records}.values())
    assets = [r for r in latest if r["category"] == "asset_candidate"]
    matured = [r for r in assets if (r.get("metrics") or {}).get("mature")]
    false = [r["recommendation_id"] for r in matured if r.get("evaluation") == "false_positive"]
    return {
        "as_of": moment.isoformat(),
        "day": moment.date().isoformat(),
        "integrity": "OK",
        "record_count": len(records),
        "head_hash": records[-1]["record_hash"] if records else "0" * 64,
        "records_today": sum(r["at"][:10] == moment.date().isoformat() for r in records),
        "counts": dict(Counter(r["category"] for r in latest)),
        "items": latest,
        "asset_evaluation": {
            "total": len(assets),
            "mature": len(matured),
            "observing": len(assets) - len(matured),
            "false_positives": false,
            "false_positive_rate": len(false) / len(matured) if matured else None,
        },
        "note": "领先事件和经济假设不算买入推荐；未成熟、缺行情和未验证记录不算误报。",
    }


def weekly_report(
    market: Any = None,
    state: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    path: Path | None = None,
    tool_journal: Path | None = None,
) -> dict[str, Any]:
    moment = _moment(now)
    report = daily_report(now=moment, path=path)
    start = moment - timedelta(days=7)
    records = [r for r in load_ledger(path) if start <= datetime.fromisoformat(r["at"]) <= moment]
    sources = {r["source"]["id"] for r in report["items"]}
    gaps = [
        h.get("hypothesis_id")
        for h in (state or {}).get("hypotheses", [])
        if h.get("hypothesis_id") not in sources
    ]
    durations = [
        float(r.get("duration_ms") or 0)
        for r in _jsonl(tool_journal or TOOL_JOURNAL)
        if r.get("at") and start <= datetime.fromisoformat(r["at"]) <= moment
    ]
    report.update(
        week_start=start.isoformat(),
        records_this_week=len(records),
        reversals=[r["record_id"] for r in records if r["action"] == "withdrawn"],
        coverage_gaps=gaps if state is not None else None,
        research_minutes=round(sum(durations) / 60000, 2),
        missed_opportunities=None,
    )
    return report


def format_daily(payload: dict[str, Any]) -> str:
    evaluation = payload["asset_evaluation"]
    lines = [
        f"前瞻账本 {payload['day']} · 完整性 {payload['integrity']} · "
        f"{payload['record_count']}个版本",
        f"资产候选 {evaluation['total']} · 成熟 {evaluation['mature']} · "
        f"观察中/等待数据 {evaluation['observing']}",
        f"成熟窗口误报数 {len(evaluation['false_positives'])}；未成熟不计分。",
    ]
    for item in payload["items"]:
        metrics = item.get("metrics") or {}
        numbers = (
            f"费后{metrics['net_return_pct']:.2f}% / 超额{metrics['excess_pct']:.2f}% / "
            f"最大回撤{metrics['max_drawdown_pct']:.2f}%"
            if "net_return_pct" in metrics
            else metrics.get("reason", "仅记录事实与后续验证，不计算资产收益")
        )
        lines.append(
            f"[{item['category']} / {item['status']}] {item['subject']} · "
            f"{item['recommendation_id']} · {numbers}"
        )
    return "\n".join([*lines, payload["note"]])


def format_weekly(payload: dict[str, Any]) -> str:
    coverage = len(payload["coverage_gaps"]) if payload["coverage_gaps"] is not None else "未核对"
    return (
        format_daily(payload) + f"\n本周撤销 {len(payload['reversals'])}；记录覆盖缺口 {coverage}；"
        f"工具耗时 {payload['research_minutes']}分钟（不等同人工用时）；"
        "机会漏报无真值集合，不统计。"
    )
