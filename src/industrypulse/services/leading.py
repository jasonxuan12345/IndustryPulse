"""领先候选指标的采集、变化检测与事件账本（阶段 2 · 更早感知）。

目标：在月度利润公布（约次月 27 日）之前，捕捉需求、供给、价格与订单的
变化。对每个新进入系统的观测回答四件事：什么变了、相对哪一期、影响哪个
行业、下一次怎样验证；并记录采集与处理时延。

纪律：
- 三条链（有色、汽车、电力及电气设备）的候选只是待验证，不是信号；
- 事件账本 append-only（data/leading/events.jsonl）：稳定事件 ID 由
  候选+所属期+变化类型+方向决定，重复扫描不产生重复事件，首次发现时间
  以账本里第一次出现为准，绝不改写；
- 研究库候选带官方发布时点，可以进研究；新浪期货、东财资金流是公开
  接口，未在研究目录登记，只做参考，不进研究计算；
- 变化检测先于 AI：只有越过幅度阈值或发生方向翻转的事件才标记为值得
  调用 AI，其余记录跳过原因（AI 本身的调用属于阶段 3）。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import yaml

from industrypulse.contracts import TZ_SHANGHAI
from industrypulse.contracts.context import ResearchMode

DIRECTION_LABELS = {"up": "上升", "down": "下降", "flat": "持平"}

ROOT = Path(__file__).resolve().parents[3]
CHAINS_CONFIG = ROOT / "config/leading/chains.yaml"
LEADING_DIR = ROOT / "data/leading"
EVENTS_PATH = LEADING_DIR / "events.jsonl"
STATE_PATH = LEADING_DIR / "scan-state.json"

#: 市场类候选动量/强弱比较需要的历史扫描条数。
MOMENTUM_WINDOW = 5
#: 账本里每个候选保留的扫描历史长度。
HISTORY_KEEP = 60
#: 统计局行业利润的推断发布规则：次月 27 日 09:30（B 级，以上游为准）。
PROFIT_RELEASE_DAY = 27

LICENSE_LABELS = {
    "research": "研究库已登记来源（统计局），带发布时点，可进研究",
    "sina_futures": "公开接口（新浪财经），未在研究目录登记，仅参考",
    "etf_relative": "研究库市场日线（新浪采集），仅相对强弱参考",
    "board_flow": "公开接口（东方财富），未在研究目录登记，仅参考",
}


def load_chains(path: Path = CHAINS_CONFIG) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))


def next_profit_release(now: datetime | None = None) -> datetime:
    """下一个统计局利润发布时点（次月 27 日 09:30，B 级推断）。"""
    moment = now or datetime.now(TZ_SHANGHAI)
    local = moment.astimezone(TZ_SHANGHAI)
    year, month = local.year, local.month + 1
    if month == 13:
        year, month = year + 1, 1
    return datetime(year, month, PROFIT_RELEASE_DAY, 9, 30, tzinfo=TZ_SHANGHAI)


def event_id(chain: str, candidate: str, period_key: str, change_kind: str, direction: str) -> str:
    """稳定事件 ID：链+候选+所属期+变化类型+方向决定身份，重复扫描不产生新事件。"""
    digest = hashlib.sha1(f"{chain}|{candidate}|{period_key}|{change_kind}|{direction}".encode())
    return f"lead-{digest.hexdigest()[:16]}"


def _direction(value: float | None) -> str:
    if value is None:
        return "flat"
    return "up" if value > 0 else ("down" if value < 0 else "flat")


# ------------------------------------------------------------------ collectors


def collect_research(
    repository: Any,
    series_id: str,
    *,
    snapshot_id: str | None,
    mode: ResearchMode = ResearchMode.RECONSTRUCTED_PIT,
) -> dict[str, Any]:
    """研究库序列的当期与上期可见值；release_at 用官方发布时点。"""
    clock = SimpleNamespace(
        as_of=datetime.now(TZ_SHANGHAI), research_mode=mode, dataset_snapshot_id=snapshot_id
    )
    selection = repository.select([series_id], clock)
    rows = sorted(selection.selected, key=lambda row: row.observation_start)
    if not rows:
        return {"status": "no_data", "series_id": series_id}
    latest, previous = rows[-1], (rows[-2] if len(rows) > 1 else None)
    return {
        "status": "ok",
        "series_id": series_id,
        "period": latest.observation_start.isoformat(),
        "value": None if latest.value is None else float(latest.value),
        "previous_period": None if previous is None else previous.observation_start.isoformat(),
        "previous_value": (
            None if previous is None or previous.value is None else float(previous.value)
        ),
        "release_at": (
            latest.publication_at.astimezone(TZ_SHANGHAI).isoformat()
            if latest.publication_at
            else None
        ),
        "source_url": str(latest.source_url) if latest.source_url else None,
        "history_periods": len(
            {row.observation_start for row in repository.all_vintages([series_id])}
        ),
    }


def collect_futures(symbols: Sequence[str]) -> dict[str, Any]:
    """新浪期货主力连续批量报价；失败返回错误，由调用方按候选降级。"""
    from industrypulse.market.providers import sina_public

    start = time.perf_counter()
    quotes = sina_public.fetch_futures(list(symbols))
    return {
        "status": "ok",
        "elapsed_ms": int((time.perf_counter() - start) * 1000),
        "quotes": quotes,
    }


def collect_etf_relative(
    market: Any,
    asset_id: str,
    benchmark_id: str = "ETF_510300",
    *,
    trading_days: int = 20,
) -> dict[str, Any]:
    """ETF 相对沪深300 的 20 日总收益差（分红加回口径，同 price_check）。"""
    from industrypulse.economy.price_check import PriceLine

    def line(asset: str) -> PriceLine | None:
        bars = market.bars(asset)
        actions = [a for a in market.actions(asset) if a.action_type == "cash_dividend"]
        if not bars:
            return None
        return PriceLine(
            tuple(bar.trade_date for bar in bars),
            tuple(float(bar.close) for bar in bars),
            tuple((a.ex_date, float(a.distribution_per_unit)) for a in actions),
        )

    asset_line, benchmark_line = line(asset_id), line(benchmark_id)
    if asset_line is None or benchmark_line is None:
        return {"status": "no_bars", "asset_id": asset_id}
    common = sorted(set(asset_line.days) & set(benchmark_line.days))
    if len(common) <= trading_days:
        return {"status": "insufficient_history", "asset_id": asset_id, "common_days": len(common)}
    end = common[-1]
    start = common[-1 - trading_days]
    relative = asset_line.total_return(start, end) - benchmark_line.total_return(start, end)
    return {
        "status": "ok",
        "asset_id": asset_id,
        "day": end.isoformat(),
        "window_start": start.isoformat(),
        "relative_pct": round(relative * 100, 2),
    }


def collect_board_flow(board: str, sina_flow: str | None = None) -> dict[str, Any]:
    """东财板块当日主力净流入（元转亿元）；东财断连时用新浪行业/概念兜底。"""
    from industrypulse.market.providers import eastmoney, sina_public

    start = time.perf_counter()
    try:
        flows = eastmoney.fetch_industry_flows()
    except Exception:  # noqa: BLE001 - 与首页同一条新浪兜底路径
        if not sina_flow:
            raise
        rows = sina_public.fetch_board_flows()
        part = rows.get(sina_flow)
        if not part:
            return {"status": "no_data", "board": board, "sina_flow": sina_flow}
        return {
            "status": "ok",
            "board": board,
            "day": date.today().isoformat(),
            "flow_yi": round(float(part["netamount"]) / 1e8, 2),
            "source": f"新浪行业资金流（{part['name']}）",
            "elapsed_ms": int((time.perf_counter() - start) * 1000),
        }
    if board not in flows:
        return {"status": "no_data", "board": board}
    return {
        "status": "ok",
        "board": board,
        "day": date.today().isoformat(),
        "flow_yi": round(flows[board] / 1e8, 2),
        "source": "东财板块资金流",
        "elapsed_ms": int((time.perf_counter() - start) * 1000),
    }


# ------------------------------------------------------------------ detection


def detect_value_change(
    candidate_id: str,
    observation: dict[str, Any],
    threshold: float,
) -> dict[str, Any] | None:
    """研究库候选：与上一公布期比较。返回事件描述或 None。"""
    value, previous = observation.get("value"), observation.get("previous_value")
    if value is None or previous is None:
        return None
    change = value - previous
    return {
        "candidate": candidate_id,
        "period_key": str(observation.get("period")),
        "change_kind": "value_change",
        "direction": _direction(change),
        "magnitude": round(change, 2),
        "value": value,
        "relative_to": f"{observation.get('previous_period')} 的 {previous}",
        "release_at": observation.get("release_at"),
        "material": abs(change) >= threshold,
    }


def detect_momentum(
    candidate_id: str,
    price: float,
    period_key: str,
    history: list[dict[str, Any]],
    *,
    window: int = MOMENTUM_WINDOW,
) -> dict[str, Any] | None:
    """价格候选：5 个扫描日的动量翻转才成事件；历史不足只记录观测。"""
    if len(history) < window:
        return None
    reference = history[-window]
    if reference.get("price") is None:
        return None
    momentum = price - float(reference["price"])
    previous_momentum = reference.get("momentum")
    flipped = (
        previous_momentum is not None
        and _direction(momentum) != "flat"
        and _direction(momentum) != _direction(previous_momentum)
    )
    if not flipped:
        return None
    return {
        "candidate": candidate_id,
        "period_key": period_key,
        "change_kind": "momentum_flip",
        "direction": _direction(momentum),
        "magnitude": round(momentum, 2),
        "value": price,
        "relative_to": f"{reference.get('at', '')} 的 {reference.get('price')}",
        "release_at": None,
        "material": True,
    }


def detect_sign_flip(
    candidate_id: str,
    value: float,
    period_key: str,
    history: list[dict[str, Any]],
    *,
    change_kind: str,
    release_at: str | None = None,
    material: bool = True,
) -> dict[str, Any] | None:
    """相对强弱 / 资金流候选：符号翻转成事件；首轮无历史不成事件。"""
    if not history:
        return None
    previous_sign = history[-1].get("sign")
    current = _direction(value)
    if previous_sign is None or current == "flat" or current == previous_sign:
        return None
    return {
        "candidate": candidate_id,
        "period_key": period_key,
        "change_kind": change_kind,
        "direction": current,
        "magnitude": round(value, 2),
        "value": value,
        "relative_to": f"{history[-1].get('at', '')} 的 {history[-1].get('value')}",
        "release_at": release_at,
        "material": material,
    }


# ------------------------------------------------------------------ ledger


def load_event_ids(path: Path | None = None) -> set[str]:
    path = path or EVENTS_PATH
    if not path.exists():
        return set()
    return {
        record["event_id"]
        for record in (
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line
        )
    }


def append_events(events: Sequence[dict[str, Any]], path: Path | None = None) -> int:
    """追加新事件；已存在的 ID 跳过（首次发现时间以账本为准），批量内也去重。"""
    path = path or EVENTS_PATH
    if not events:
        return 0
    known = load_event_ids(path)
    fresh: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        identifier = event["event_id"]
        if identifier in known or identifier in seen:
            continue
        seen.add(identifier)
        fresh.append(event)
    if not fresh:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for event in fresh:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
    return len(fresh)


def _load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return cast(dict[str, Any], json.loads(STATE_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return {}
    return {}


def _save_state(state: dict[str, Any]) -> None:
    LEADING_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _remember(state: dict[str, Any], candidate_id: str, entry: dict[str, Any]) -> None:
    slot = state.setdefault(candidate_id, {"history": []})
    slot["history"] = (slot["history"] + [entry])[-HISTORY_KEEP:]


# ------------------------------------------------------------------ scan


def scan(
    repository: Any,
    market: Any,
    *,
    snapshot_id: str | None = None,
    now: datetime | None = None,
    chains_path: Path = CHAINS_CONFIG,
) -> dict[str, Any]:
    """采集全部候选 → 变化检测 → 写账本 → 输出报告与时延统计。"""
    moment = (now or datetime.now(TZ_SHANGHAI)).astimezone(TZ_SHANGHAI)
    chains = load_chains(chains_path)
    state = _load_state()
    known_events = load_event_ids()
    pending: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    futures_symbols = sorted(
        {
            str(item["symbol"])
            for config in chains["chains"].values()
            for item in config["candidates"]
            if item["kind"] == "sina_futures"
        }
    )
    futures_quotes: dict[str, Any] = {}
    futures_ms: int | None = None
    if futures_symbols:
        try:
            fetched = collect_futures(futures_symbols)
            futures_quotes = fetched["quotes"]
            futures_ms = fetched["elapsed_ms"]
        except Exception:  # noqa: BLE001 - 单源失败逐候选降级
            futures_quotes = {}

    for chain_id, config in chains["chains"].items():
        industries = [{"id": item["id"], "name": item["name"]} for item in config["industries"]]
        for candidate in config["candidates"]:
            kind = candidate["kind"]
            threshold = float(candidate.get("material") or 0.0)
            observation: dict[str, Any] = {
                "chain": chain_id,
                "candidate": candidate["id"],
                "kind": kind,
            }
            event: dict[str, Any] | None = None
            if kind == "research":
                started = time.perf_counter()
                try:
                    result = collect_research(
                        repository, str(candidate["series"]), snapshot_id=snapshot_id
                    )
                except Exception as exc:  # noqa: BLE001
                    result = {"status": "fail", "error": str(exc)[:120]}
                observation["source_ms"] = int((time.perf_counter() - started) * 1000)
                observation |= result
                if result.get("status") == "ok":
                    event = detect_value_change(str(candidate["id"]), result, threshold)
                    _remember(
                        state,
                        str(candidate["id"]),
                        {"at": moment.date().isoformat(), "value": result.get("value")},
                    )
            elif kind == "sina_futures":
                quote = futures_quotes.get(str(candidate["symbol"]))
                if not quote:
                    observation["status"] = "fail"
                    observation["error"] = "新浪期货报价不可用"
                else:
                    observation["status"] = "ok"
                    observation["source_ms"] = futures_ms
                    observation["price"] = quote["price"]
                    observation["day"] = quote["date"]
                    history = state.get(str(candidate["id"]), {}).get("history", [])
                    event = detect_momentum(
                        str(candidate["id"]),
                        float(quote["price"]),
                        str(quote["date"]),
                        history,
                    )
                    price = float(quote["price"])
                    reference = (
                        history[-MOMENTUM_WINDOW].get("price")
                        if len(history) >= MOMENTUM_WINDOW
                        else None
                    )
                    _remember(
                        state,
                        str(candidate["id"]),
                        {
                            "at": moment.date().isoformat(),
                            "price": price,
                            "momentum": None
                            if reference is None
                            else round(price - float(reference), 2),
                        },
                    )
            elif kind == "etf_relative":
                started = time.perf_counter()
                try:
                    result = collect_etf_relative(market, str(candidate["asset_id"]))
                except Exception as exc:  # noqa: BLE001
                    result = {"status": "fail", "error": str(exc)[:120]}
                observation["source_ms"] = int((time.perf_counter() - started) * 1000)
                observation |= result
                if result.get("status") == "ok":
                    history = state.get(str(candidate["id"]), {}).get("history", [])
                    relative = float(result["relative_pct"])
                    event = detect_sign_flip(
                        str(candidate["id"]),
                        relative,
                        str(result["day"]),
                        history,
                        change_kind="relative_flip",
                        release_at=f"{result['day']}T15:00:00+08:00",
                    )
                    _remember(
                        state,
                        str(candidate["id"]),
                        {
                            "at": moment.date().isoformat(),
                            "value": relative,
                            "sign": _direction(relative),
                        },
                    )
            elif kind == "board_flow":
                started = time.perf_counter()
                try:
                    result = collect_board_flow(
                        str(candidate["board"]), str(candidate.get("sina_flow") or "") or None
                    )
                except Exception as exc:  # noqa: BLE001
                    result = {"status": "fail", "error": str(exc)[:120]}
                observation["source_ms"] = int((time.perf_counter() - started) * 1000)
                observation |= result
                if result.get("status") == "ok":
                    history = state.get(str(candidate["id"]), {}).get("history", [])
                    flow = float(result["flow_yi"])
                    event = detect_sign_flip(
                        str(candidate["id"]),
                        flow,
                        str(result["day"]),
                        history,
                        change_kind="flow_sign_flip",
                    )
                    _remember(
                        state,
                        str(candidate["id"]),
                        {"at": moment.date().isoformat(), "value": flow, "sign": _direction(flow)},
                    )
            else:
                observation["status"] = "unknown_kind"
            observations.append(observation)

            if event is None:
                continue
            identifier = event_id(
                chain_id,
                event["candidate"],
                event["period_key"],
                event["change_kind"],
                event["direction"],
            )
            release = event.get("release_at")
            lag_hours = None
            if release:
                try:
                    lag_hours = round(
                        (moment - datetime.fromisoformat(release)).total_seconds() / 3600, 1
                    )
                except ValueError:
                    lag_hours = None
            pending.append(
                {
                    "event_id": identifier,
                    "first_seen_at": moment.isoformat(),
                    "is_new": identifier not in known_events,
                    "chain": chain_id,
                    "chain_name": str(config["name"]),
                    "candidate": event["candidate"],
                    "candidate_name": str(candidate["name"]),
                    "kind": event["change_kind"],
                    "industries": industries,
                    "what_changed": (
                        f"{candidate['name']} {DIRECTION_LABELS[event['direction']]} "
                        f"{abs(float(event['magnitude']))}（相对 {event['relative_to']}）"
                    ),
                    "direction": event["direction"],
                    "magnitude": event["magnitude"],
                    "relative_to": event["relative_to"],
                    "period_key": event["period_key"],
                    "release_at": release,
                    "detection_lag_hours": lag_hours,
                    "ai_status": "queued" if event["material"] else "skipped",
                    "ai_reason": (
                        "越过幅度阈值或方向翻转"
                        if event["material"]
                        else f"变化幅度低于阈值 {threshold}"
                    ),
                    "next_verification": str(candidate["verify"]).format(
                        industry="、".join(item["name"] for item in industries),
                        next_profit=next_profit_release(moment).strftime("%Y-%m-%d %H:%M"),
                    ),
                    "license": LICENSE_LABELS[kind],
                }
            )
    _save_state(state)
    appended = append_events(pending)
    source_ms = [item["source_ms"] for item in observations if item.get("source_ms") is not None]
    lags = [
        item["detection_lag_hours"]
        for item in pending
        if item.get("detection_lag_hours") is not None
    ]
    payload: dict[str, Any] = {
        "scanned_at": moment.isoformat(),
        "chains": len(chains["chains"]),
        "candidates": len(observations),
        "observations": observations,
        "new_events": appended,
        "queued_for_ai": sum(1 for item in pending if item["ai_status"] == "queued"),
        "events": pending,
        "latency": {
            "source_ms_median": _median(source_ms),
            "source_ms_max": max(source_ms) if source_ms else None,
            "detection_lag_hours_median": _median(lags),
            "detection_lag_hours_max": max(lags) if lags else None,
        },
        "known_events_total": len(known_events) + appended,
    }
    return payload


def _median(values: Sequence[float]) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def format_scan(payload: dict[str, Any]) -> str:
    """渲染扫描报告：什么变了、相对哪期、影响哪个行业、下次怎样验证、时延。"""
    lines = [
        f"领先指标扫描 {payload['scanned_at']} · 链 {payload['chains']} · "
        f"候选 {payload['candidates']} · 新事件 {payload['new_events']}"
        f"（其中 AI 门槛通过 {payload['queued_for_ai']}）",
        "",
    ]
    for event in payload["events"]:
        names = "、".join(item["name"] for item in event["industries"])
        marker = "新" if event.get("is_new") else "已知"
        lines.append(
            f"[{event['chain_name']}·{marker}] {event['what_changed']}"
            f"  （事件 {event['event_id']}，{event['kind']}）"
        )
        lines.append(f"    影响行业：{names}")
        lines.append(f"    下次验证：{event['next_verification']}")
        lag = event.get("detection_lag_hours")
        lines.append(
            f"    AI 门槛：{event['ai_status']}（{event['ai_reason']}）"
            + ("" if lag is None else f" · 发现延迟 {lag} 小时")
        )
        lines.append("")
    latency = payload["latency"]
    lines.append(
        f"时延：采集中位数 {latency['source_ms_median']}ms / 最大 {latency['source_ms_max']}ms；"
        f"发现延迟中位数 {latency['detection_lag_hours_median']}h / 最大 "
        f"{latency['detection_lag_hours_max']}h"
    )
    failed = [item for item in payload["observations"] if item.get("status") not in ("ok", None)]
    if failed:
        lines.append(
            f"采集失败 {len(failed)} 项：" + "、".join(str(item["candidate"]) for item in failed)
        )
    return "\n".join(lines)


def verify(
    repository: Any,
    market: Any,
    *,
    snapshot_id: str | None = None,
    chains_path: Path = CHAINS_CONFIG,
) -> dict[str, Any]:
    """逐候选核实：发布时间、许可、历史深度、采集能力（现场抓一次）。"""
    chains = load_chains(chains_path)
    rows: list[dict[str, Any]] = []
    futures_symbols = sorted(
        {
            str(item["symbol"])
            for config in chains["chains"].values()
            for item in config["candidates"]
            if item["kind"] == "sina_futures"
        }
    )
    try:
        futures = collect_futures(futures_symbols)
    except Exception as exc:  # noqa: BLE001
        futures = {"status": "fail", "error": str(exc)[:120], "quotes": {}}
    for chain_id, config in chains["chains"].items():
        for candidate in config["candidates"]:
            kind = str(candidate["kind"])
            row: dict[str, Any] = {
                "chain": chain_id,
                "candidate": candidate["id"],
                "name": candidate["name"],
                "kind": kind,
                "release": candidate["release"],
                "license": LICENSE_LABELS[kind],
            }
            if kind == "research":
                try:
                    result = collect_research(
                        repository, str(candidate["series"]), snapshot_id=snapshot_id
                    )
                    row["collect_status"] = result.get("status")
                    row["latest_period"] = result.get("period")
                    row["latest_value"] = result.get("value")
                    row["history_periods"] = result.get("history_periods")
                    row["source_ms"] = None  # 本地查询，不计网络时延
                except Exception as exc:  # noqa: BLE001
                    row["collect_status"] = "fail"
                    row["error"] = str(exc)[:120]
            elif kind == "sina_futures":
                quote = futures.get("quotes", {}).get(str(candidate["symbol"]))
                row["collect_status"] = "ok" if quote else "fail"
                row["latest_period"] = quote.get("date") if quote else None
                row["latest_value"] = quote.get("price") if quote else None
                row["source_ms"] = futures.get("elapsed_ms") if quote else None
                row["history_periods"] = None  # 连续报价无历史，账本自首次扫描起积累
            elif kind == "etf_relative":
                try:
                    result = collect_etf_relative(market, str(candidate["asset_id"]))
                    row["collect_status"] = result.get("status")
                    row["latest_period"] = result.get("day")
                    row["latest_value"] = result.get("relative_pct")
                    bars = market.bars(str(candidate["asset_id"]))
                    row["history_periods"] = len(bars)
                except Exception as exc:  # noqa: BLE001
                    row["collect_status"] = "fail"
                    row["error"] = str(exc)[:120]
            elif kind == "board_flow":
                try:
                    result = collect_board_flow(
                        str(candidate["board"]), str(candidate.get("sina_flow") or "") or None
                    )
                    row["collect_status"] = result.get("status")
                    row["latest_period"] = result.get("day")
                    row["latest_value"] = result.get("flow_yi")
                    row["source_ms"] = result.get("elapsed_ms")
                    row["history_periods"] = None  # 账本自首次扫描起积累
                except Exception as exc:  # noqa: BLE001
                    row["collect_status"] = "fail"
                    row["error"] = str(exc)[:120]
            rows.append(row)
    ok = sum(1 for row in rows if row.get("collect_status") == "ok")
    return {
        "verified_at": datetime.now(TZ_SHANGHAI).isoformat(),
        "candidates": len(rows),
        "collect_ok": ok,
        "rows": rows,
        "note": (
            "history_periods 为当前可得深度：研究库候选按已入库期数计，"
            "市场类候选从账本首次扫描起积累。采集失败不掩盖，逐项给原因。"
        ),
    }


def format_verify(payload: dict[str, Any]) -> str:
    lines = [
        f"领先候选核实 {payload['verified_at']} · "
        f"采集成功 {payload['collect_ok']}/{payload['candidates']}",
        "",
        f"{'链':<8}{'候选':<26}{'状态':<6}{'最近期':<13}{'深度':>6}{'时延':>8}",
        "-" * 74,
    ]
    for row in payload["rows"]:
        period = str(row.get("latest_period") or "—")
        depth = row.get("history_periods")
        elapsed = row.get("source_ms")
        lines.append(
            f"{row['chain']:<8}{row['candidate']:<26}{str(row.get('collect_status')):<6}"
            f"{period:<13}{depth if depth is not None else '—':>6}"
            f"{str(elapsed) + 'ms' if elapsed is not None else '—':>8}"
        )
        if row.get("error"):
            lines.append(f"    错误：{row['error']}")
    lines += ["", payload["note"]]
    return "\n".join(lines)


__all__ = [
    "append_events",
    "collect_board_flow",
    "collect_etf_relative",
    "collect_futures",
    "collect_research",
    "detect_momentum",
    "detect_sign_flip",
    "detect_value_change",
    "event_id",
    "format_scan",
    "format_verify",
    "load_chains",
    "load_event_ids",
    "next_profit_release",
    "scan",
    "verify",
]
