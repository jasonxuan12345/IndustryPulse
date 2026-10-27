"""Persistent new-data -> forecasts -> independent AI review -> ledger workflow."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import threading
from datetime import datetime, time, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml

from industrypulse.contracts import TZ_SHANGHAI
from industrypulse.contracts.pit import select_vintages_as_of
from industrypulse.repositories import DuckDBMarketRepository
from industrypulse.services import hypothesis_research as ai
from industrypulse.services import ledger, opportunity_risk, research_forecast, trusted_baseline
from industrypulse.services.chain_demo import MODE, EvidenceReplay
from industrypulse.services.workbench import write_json

if TYPE_CHECKING:
    from industrypulse.services.workbench import Workbench


def read(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def settings(service: Workbench) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        read(
            service.root / "data/research-loop/settings.json",
            {
                "enabled": False,
                "ai_enabled": True,
                "capture_enabled": True,
                "interval_minutes": 15,
                "max_ai_events_per_run": 2,
            },
        ),
    )


def configure(service: Workbench, enabled: bool, *, ai_enabled: bool = True) -> dict[str, Any]:
    options = {
        **settings(service),
        "enabled": enabled,
        "ai_enabled": ai_enabled,
        "requires": (
            "应用持续运行；每15分钟查新；统计局发布时段每小时捕获、收盘后全映射报价每日同步"
        ),
    }
    write_json(service.root / "data/research-loop/settings.json", options)
    return options


def universe(service: Workbench) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    mapping = yaml.safe_load(
        (service.root / "config/market/industry_price_lines.yaml").read_text(encoding="utf-8")
    )
    policy = yaml.safe_load(
        (service.root / "config/research/opportunity-policy.yaml").read_text(encoding="utf-8")
    )
    result = {}
    for path in sorted((service.root / "config/industries").glob("*.yaml")):
        registry = service.taxonomy(path.stem)
        profit = next(
            (s.series_id for s in registry.series if s.series_id.endswith("profit_ytd_yoy")), None
        )
        if registry.synthetic or not profit:
            continue
        iid = registry.nodes[0].industry_id
        revenue, cost = (
            profit.replace("profit_ytd_yoy", k + "_ytd_yoy") for k in ("revenue", "cost")
        )
        iva = next(
            (
                s.series_id
                for s in registry.series
                if re.fullmatch(r"cn\.nbs\.industry\.[a-z]\d+", s.series_id)
            ),
            None,
        )
        # The steel legacy registry uses 'cn.nbs.industry.c31'.
        shared = ["cn.nbs.pmi.production"]
        price_spec = mapping["industries"].get(iid, {})
        result[iid] = {
            "industry_id": iid,
            "config_id": path.stem,
            "name": registry.nodes[0].names.get("zh", iid),
            "profit_id": profit,
            "revenue_id": revenue,
            "cost_id": cost,
            "iva_id": iva,
            "series": [profit, revenue, cost, *([iva] if iva else []), *shared],
            "asset_name": price_spec.get("name"),
            **{key: value for key, value in price_spec.items() if key != "name"},
        }
    return list(result.values()), policy


def freeze(
    service: Workbench, spec: dict[str, Any], policy: dict[str, Any], now: datetime
) -> dict[str, Any]:
    from industrypulse.services.research_news import leads

    repo = service.repository()
    market = DuckDBMarketRepository(service.path)
    rows = repo.all_vintages(spec["series"])
    from industrypulse.contracts.observation import ObservationVintage

    eligible: list[ObservationVintage] = []
    for sid in spec["series"]:
        selection = select_vintages_as_of(
            [r for r in rows if r.series_id == sid],
            as_of=now,
            mode=MODE,
            trading_days=repo.trading_days,
            conservative_lag_days=repo.series_lag_days.get(sid, repo.conservative_lag_days),
        )
        excluded = {(r.observation_start, r.vintage_id) for r in selection.skipped}
        eligible.extend(
            r
            for r in rows
            if r.series_id == sid and (r.observation_start, r.vintage_id) not in excluded
        )
    end = now.date() if now.time() >= time(15) else now.date() - timedelta(days=1)
    aids = sorted({policy["benchmark_id"], *([spec["asset_id"]] if spec.get("asset_id") else [])})
    return {
        "spec": spec,
        "news_leads": leads(service, spec["industry_id"], now),
        "holdouts": [
            value
            for path in sorted((service.output / "holdouts").glob("*.json"))
            if (value := read(path)).get("industry") == spec["config_id"]
        ],
        "policy": {
            **policy,
            "lag_days": repo.conservative_lag_days,
            "series_lag_days": dict(repo.series_lag_days),
            "trading_days": [d.isoformat() for d in repo.trading_days],
        },
        "vintages": [r.model_dump(mode="json") for r in eligible],
        "bars": [b.model_dump(mode="json") for aid in aids for b in market.bars(aid, end=end)],
        "actions": [
            a.model_dump(mode="json")
            for aid in aids
            for a in market.actions(aid)
            if a.ex_date <= end
        ],
        "runtime_versions": {
            "python": platform.python_version(),
            **{name: version(name) for name in ("numpy", "scikit-learn", "pydantic", "duckdb")},
        },
        "code_sha256": {
            name: hashlib.sha256((service.root / name).read_bytes()).hexdigest()
            for name in (
                "src/industrypulse/services/research_forecast.py",
                "src/industrypulse/services/opportunity_risk.py",
                "src/industrypulse/services/research_loop.py",
                "src/industrypulse/services/hypothesis_research.py",
                "src/industrypulse/forecast/models.py",
                "src/industrypulse/forecast/registry.py",
                "src/industrypulse/services/research_run.py",
                "src/industrypulse/services/chain_demo.py",
                "src/industrypulse/services/etf_audit.py",
                "src/industrypulse/contracts/pit.py",
            )
        },
    }


def replay(service: Workbench, event_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"data-[0-9a-f]{24}", event_id):
        raise ValueError("事件ID格式错误")
    directory = service.root / "data/research-loop"
    event = read(directory / "events" / f"{event_id}.json")
    if event is None:
        raise ValueError("事件不存在")
    bundle = read(directory / "inputs" / f"{event['input_sha256']}.json")
    if bundle is None or ledger._hash(bundle) != event["input_sha256"]:
        raise ValueError("冻结输入缺失或被改写")
    for path, sha in bundle["code_sha256"].items():
        if path in {
            "src/industrypulse/services/research_loop.py",
            "src/industrypulse/services/hypothesis_research.py",
            "src/industrypulse/services/opportunity_risk.py",
        }:
            continue  # AI validation and admission do not execute inside forecast replay.
        if hashlib.sha256((service.root / path).read_bytes()).hexdigest() != sha:
            raise ValueError(f"运行代码与冻结版本不同：{path}")
    for name, expected in bundle["runtime_versions"].items():
        actual = platform.python_version() if name == "python" else version(name)
        if actual != expected:
            raise ValueError(f"依赖版本不同：{name}")
    result = research_forecast.forecast(
        bundle, bundle["spec"], datetime.fromisoformat(event["detected_at"])
    )
    return {
        "event_id": event_id,
        "identical": ledger._hash(result) == ledger._hash(event["forecast"]),
        "original_sha256": ledger._hash(event["forecast"]),
        "replay_sha256": ledger._hash(result),
        "forecast": result,
    }


def ai_event(event: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    packet_event = {
        "event_id": event["event_id"],
        "chain_name": spec["name"],
        "candidate_name": "行业已发布数据与价格变化",
        "industries": [{"id": spec["industry_id"], "name": spec["name"]}],
        "what_changed": json.dumps(
            event.get("changed_facts") or event["event_type"], ensure_ascii=False
        ),
        "release_at": event["source_at"],
        "next_verification": "下一次官方PMI及行业利润、收入与成本公告；具体时间以官方公布为准",
        "model_context": event["forecast"],
        "previous_hypothesis": event["previous_hypothesis"],
        "research_facts": [
            {
                "label": f["series_id"],
                "value": f["value"],
                "period": f["period"],
                "source_url": f["source_url"],
                "unit": f.get("unit", ""),
                "note": (
                    f"可用时点{f['available_at']}；版本{f['vintage_id']}；"
                    f"数值证据{f['value_evidence']}"
                    + f"；统计区间{f['period']}至{f.get('period_end', f['period'])}；"
                    "发布时点与统计期不同"
                ),
            }
            for f in event["facts"]
        ],
    }
    packet_event["research_facts"].extend(
        {
            "label": "未核定新闻线索：" + lead["title"],
            "value": lead["summary"],
            "period": lead["published_at"],
            "source_url": lead["url"],
            "note": lead["evidence"] + "；只作定性解释，数字需另行核定，禁止代入模型或收益预测",
        }
        for lead in event.get("news_leads", [])
    )
    return packet_event


def revalidate(service: Workbench, event_id: str) -> dict[str, Any]:
    """Recheck identical cached answers; never call the provider or edit the event."""
    from industrypulse.ai_research.model_gateway import load_connection

    if not re.fullmatch(r"data-[0-9a-f]{24}", event_id):
        raise ValueError("事件ID格式错误")
    directory = service.root / "data/research-loop"
    with ledger._lock(directory / "runtime"):
        event = read(directory / "events" / f"{event_id}.json")
        if event is None:
            raise ValueError("事件不存在")
        bundle = read(directory / "inputs" / f"{event['input_sha256']}.json")
        if not bundle or ledger._hash(bundle) != event["input_sha256"]:
            raise ValueError("冻结输入缺失或被改写")
        connection = load_connection()
        if connection is None:
            raise ValueError("原模型配置不存在")
        now = ledger._moment()
        outcome = ai.run_hypothesis_research(
            None,
            connection,
            {},
            events=[ai_event(event, bundle["spec"])],
            model_id=connection.model,
            now=now,
            max_events=1,
            cached_only=True,
            cache_file=service.root / "data/research/ai-cache.json",
            budget_file=service.root / "data/research/ai-budget.json",
            hypotheses_path=service.root / "data/research/hypotheses.jsonl",
            journal_path=service.root / "data/research/tool-journal.jsonl",
        )
        write_json(
            directory / "revalidations" / (event_id + "-" + ledger._hash(outcome)[:16] + ".json"),
            outcome,
        )
        if outcome["records"]:
            hypothesis = outcome["records"][0]
            state = read(directory / "state.json")
            previous = state["industries"].get(event["industry_id"], {})
            if previous.get("event_id") == event_id:
                previous.update(hypothesis=hypothesis, ai_status="completed", ai_failures=[])
                write_json(directory / "state.json", state)
            report = read(directory / "latest.json", {})
            for item in report.get("items", []):
                if item["event_id"] != event_id:
                    continue
                item.update(ai_hypothesis=hypothesis, ai_review="completed", ai_failures=[])
                # Revalidation never grants model admission or a positive weight.
                item["checks"] = [c for c in item["checks"] if c["kind"] != "ai"]
                item["checks"].append(
                    {
                        "check": "独立AI解释及反证",
                        "passed": hypothesis["counter_verdict"] == "reinforced",
                        "observed": hypothesis["counter_verdict"],
                        "required": "独立审查reinforced",
                        "kind": "ai",
                    }
                )
                item["blocking_reasons"] = [r for r in item["blocking_reasons"] if "AI" not in r]
                if hypothesis["counter_verdict"] != "reinforced":
                    item["blocking_reasons"].append("AI独立反证削弱或否定传导假设")
                if (
                    item["economic_signal"]
                    and item["data_risk_passed"]
                    and hypothesis["counter_verdict"] in {"reinforced", "weakened"}
                ):
                    cohort = ledger._hash(
                        {
                            "facts": item["facts"],
                            "policy": report["policy"],
                            "asset": item["asset_id"],
                        }
                    )
                    for months in (3, 6):
                        ledger.register(
                            source_kind="conditional_watch",
                            source_id=cohort,
                            category="asset_candidate",
                            subject=item["name"],
                            asset=item["asset_id"],
                            horizon_months=months,
                            statement="零权重前瞻研究候选；经济及数据门槛通过，等待模型投资有效性验证",
                            evidence=sorted(
                                {f["source_url"] for f in item["facts"] if f.get("source_url")}
                            ),
                            invalidation_conditions=item["invalidation_conditions"],
                            source_snapshot=item,
                            now=now,
                            path=service.root / "data/ledger/recommendations.jsonl",
                        )
            report["ai_pending"] = sum(
                i["ai_review"] != "completed" for i in report.get("items", [])
            )
            report["revalidated_at"] = now.isoformat()
            write_json(directory / "latest.json", report)
            ledger.seed(
                now=now,
                path=service.root / "data/ledger/recommendations.jsonl",
                hypotheses_path=service.root / "data/research/hypotheses.jsonl",
                events_path=service.root / "data/leading/events.jsonl",
            )
            ledger.update(
                DuckDBMarketRepository(service.path),
                now=now,
                path=service.root / "data/ledger/recommendations.jsonl",
            )
        return outcome


def _save_frozen(path: Path, content: dict[str, Any]) -> None:
    if path.exists():
        if ledger._hash(read(path)) != ledger._hash(content):
            raise ValueError("冻结事件或输入被改写，拒绝覆盖")
    else:
        write_json(path, content)


def capture_sources(
    service: Workbench, now: datetime, state: dict[str, Any], *, force: bool = False
) -> dict[str, Any]:
    """One bounded attempt per hour/day, with failures visible and retained."""
    from industrypulse.services.industry_expand import expand_industries
    from industrypulse.services.workbench_ops import backfill

    result = dict(state.get("source_health", {}))
    news_service = getattr(service, "news_service", None)
    if news_service is not None and news_service.settings.mode != "demo":
        result["news_refresh_started"] = news_service.refresh()
    hour = now.strftime("%Y-%m-%dT%H")
    if force or (9 <= now.hour <= 20 and result.get("macro_attempt_hour") != hour):
        result["macro_attempt_hour"] = hour
        try:
            captured = backfill(service, {"max_pages": 3})
            result["macro"] = captured
            if captured.get("report"):
                result["expand"] = expand_industries(service, {})
            result.pop("macro_error", None)
        except Exception as exc:
            result["macro_error"] = str(exc)[:300]
    if force or (now.hour >= 18 and result.get("price_attempt_day") != now.date().isoformat()):
        result["price_attempt_day"] = now.date().isoformat()
        try:
            from industrypulse.services.ledger_runtime import refresh_prices

            result["prices"] = refresh_prices(
                service, DuckDBMarketRepository(service.path), all_mapped=True
            )
            result["baseline"] = trusted_baseline.run(service, now=now, refresh=True)
            result.pop("price_error", None)
        except Exception as exc:
            result["price_error"] = str(exc)[:300]
    return result


def run(
    service: Workbench,
    *,
    now: datetime | None = None,
    refresh: bool = False,
    use_ai: bool = True,
    complete: Any = None,
    connection: Any = None,
    scheduled: bool = False,
) -> dict[str, Any]:
    moment = ledger._moment(now)
    directory = service.root / "data/research-loop"
    with ledger._lock(directory / "runtime"):
        return _run(service, directory, moment, refresh, use_ai, complete, connection, scheduled)


def _run(
    service: Workbench,
    directory: Path,
    now: datetime,
    refresh: bool,
    use_ai: bool,
    complete: Any,
    connection: Any,
    scheduled: bool = False,
) -> dict[str, Any]:
    from industrypulse.ai_research.model_gateway import complete as model_complete
    from industrypulse.ai_research.model_gateway import load_connection, public_connection

    state = read(directory / "state.json", {"industries": {}})
    result: dict[str, Any] = {
        "started_at": now.isoformat(),
        "errors": [],
        "events_created": 0,
        "items": [],
        "ai": [],
        "settings": settings(service),
    }
    if refresh:
        state["source_health"] = capture_sources(service, now, state, force=not scheduled)
    result["source_health"] = state.get("source_health", {})
    specs, policy = universe(service)
    connection = connection or load_connection()
    complete = complete or model_complete
    model_identity = public_connection(connection)
    # No API key is ever included in snapshots, reports, prompts, or hashes.
    model_hash = ledger._hash(
        {k: model_identity.get(k) for k in ("base_url", "model", "protocol", "enabled")}
    )
    pending = []
    for spec in specs:
        iid = spec["industry_id"]
        try:
            bundle = freeze(service, spec, policy, now)
            identity = ledger._hash(bundle)
            input_path = directory / "inputs" / f"{identity}.json"
            _save_frozen(input_path, bundle)
            previous = state["industries"].get(iid, {})
            cached = previous.get("input_sha256") == identity
            if cached:
                event = read(directory / "events" / f"{previous['event_id']}.json")
                model = event["forecast"]
            else:
                model = research_forecast.forecast(bundle, spec, now)
                event_id = "data-" + identity[:24]
                selected = EvidenceReplay(bundle).select(now)
                latest = [row for rows in selected.values() for row in rows[-2:]]
                facts = [
                    {**f, "period_end": r.observation_end.isoformat(), "unit": r.unit}
                    for r in latest
                    if (f := EvidenceReplay(bundle).fact(r)) is not None
                ]
                prior_hyp = previous.get("hypothesis")
                prior_event = read(
                    directory / "events" / f"{previous.get('event_id', '')}.json", {}
                )
                old_facts = {(f["series_id"], f["period"]): f for f in prior_event.get("facts", [])}
                changed = [
                    {
                        "series_id": f["series_id"],
                        "before": old_facts.get((f["series_id"], f["period"])),
                        "after": f,
                    }
                    for f in facts
                    if f != old_facts.get((f["series_id"], f["period"]))
                ]
                event = {
                    "event_id": event_id,
                    "detected_at": now.isoformat(),
                    "input_sha256": identity,
                    "industry_id": iid,
                    "name": spec["name"],
                    "facts": facts,
                    "forecast": model,
                    "previous_event_id": previous.get("event_id"),
                    "previous_hypothesis": prior_hyp,
                    "changed_facts": changed,
                    "news_leads": bundle.get("news_leads", []),
                    "event_type": "initial_capture" if not previous else "data_or_policy_revision",
                    "source_at": max((f["available_at"] for f in facts), default=None),
                }
                _save_frozen(directory / "events" / f"{event_id}.json", event)
                previous = {
                    "event_id": event_id,
                    "input_sha256": identity,
                    "hypothesis": prior_hyp,
                    "first_pending_at": previous.get("first_pending_at", now.isoformat()),
                }
                state["industries"][iid] = previous
                result["events_created"] += 1
            dividend_ok, reason = trusted_baseline.asset_ready(
                service, spec.get("asset_id", ""), now
            )
            benchmark_ok, benchmark_reason = trusted_baseline.asset_ready(
                service, policy["benchmark_id"], now
            )
            item = opportunity_risk.screen(
                bundle,
                spec,
                now,
                model,
                dividend_ok and benchmark_ok,
                reason + "；基准：" + benchmark_reason,
            )
            item.update(
                event_id=event["event_id"],
                input_sha256=identity,
                forecast=model,
                ai_review=previous.get("ai_status", "pending"),
                ai_failures=previous.get("ai_failures", []),
                previous_ai_hypothesis=previous.get("hypothesis")
                if previous.get("ai_status") != "completed"
                else None,
                ai_hypothesis=previous.get("hypothesis")
                if previous.get("ai_status") == "completed"
                else None,
            )
            result["items"].append(item)
            screen_identity = ledger._hash(
                {
                    "day": now.date().isoformat(),
                    "input": identity,
                    "checks": item["checks"],
                    "policy": policy,
                }
            )
            ledger.register(
                source_kind="opportunity_screen",
                source_id=screen_identity,
                category="screening_observation",
                subject=spec["name"],
                statement="全行业机会筛选：" + item["tier"],
                source_snapshot=item,
                evidence=sorted({f["source_url"] for f in item["facts"] if f.get("source_url")}),
                horizon_months=0,
                now=now,
                path=service.root / "data/ledger/recommendations.jsonl",
            )
            if previous.get("ai_input") != model_hash or previous.get("ai_status") not in {
                "completed",
                "rejected",
            }:
                retry = previous.get("retry_after")
                if (
                    not retry
                    or now >= datetime.fromisoformat(retry)
                    or previous.get("ai_input") != model_hash
                ):
                    pending.append((item, spec, event, previous))
        except Exception as exc:
            result["errors"].append(
                {"industry": iid, "stage": "model_or_screen", "error": str(exc)[:300]}
            )
    # New official releases rank before price-only changes; the evidence score
    # decides the research queue, never an expected profit or trade instruction.
    pending.sort(
        key=lambda p: (
            not bool(p[2].get("changed_facts")),
            p[3].get("first_pending_at", now.isoformat()),
            -p[0]["evidence_score"],
            p[0]["industry_id"],
        )
    )
    count = 0
    for item, spec, event, previous in pending:
        if (
            not use_ai
            or not result["settings"].get("ai_enabled")
            or connection is None
            or not connection.enabled
        ):
            item["ai_review"] = "awaiting_model" if use_ai else "not_requested"
            continue
        if count >= result["settings"]["max_ai_events_per_run"]:
            break
        if ai.budget_remaining(service.root / "data/research/ai-budget.json", now=now) < 2:
            item["ai_review"] = "budget_deferred"
            continue
        count += 1
        packet_event = ai_event(event, spec)
        # A prior hypothesis is frozen into this event's input, so retries do not
        # change their own prompts after another industry's review completes.
        outcome = ai.run_hypothesis_research(
            complete,
            connection,
            {},
            events=[packet_event],
            model_id=str(model_identity.get("model") or "configured"),
            max_events=1,
            now=now,
            cache_file=service.root / "data/research/ai-cache.json",
            budget_file=service.root / "data/research/ai-budget.json",
            hypotheses_path=service.root / "data/research/hypotheses.jsonl",
            journal_path=service.root / "data/research/tool-journal.jsonl",
        )
        _save_frozen(
            directory
            / "ai-runs"
            / (event["event_id"] + "-" + ledger._hash(outcome)[:16] + ".json"),
            outcome,
        )
        result["ai"].append(outcome)
        previous["ai_failures"] = outcome["failures"]
        item["ai_failures"] = outcome["failures"]
        previous.update(ai_input=model_hash, retry_after=(now + timedelta(hours=1)).isoformat())
        if outcome["records"]:
            hypothesis = outcome["records"][0]
            previous.update(
                ai_status="completed", hypothesis=hypothesis, first_pending_at=now.isoformat()
            )
            item.update(ai_review="completed", ai_hypothesis=hypothesis)
        else:
            rejected = any(f["status"] == "rejected" for f in outcome["failures"])
            previous["ai_status"] = "rejected" if rejected else "failed"
            item["ai_review"] = previous["ai_status"]
    for item in result["items"]:
        verdict = (item.get("ai_hypothesis") or {}).get("counter_verdict")
        passed = item.get("ai_review") == "completed" and verdict == "reinforced"
        item["checks"].append(
            {
                "check": "独立AI解释及反证",
                "passed": passed,
                "observed": verdict or item.get("ai_review"),
                "required": "结构与引用通过，独立审查reinforced",
                "kind": "ai",
            }
        )
        if not passed:
            item["admitted"] = False
            item["blocking_reasons"].append(
                "AI独立反证削弱或否定传导假设"
                if verdict in {"rejected", "weakened"}
                else "等待独立AI研究通过"
            )
        item["tier"] = (
            "CONDITIONAL_CANDIDATE"
            if item["admitted"]
            else "RESEARCH_CANDIDATE"
            if item["economic_signal"]
            else "OBSERVE"
        )
    market = DuckDBMarketRepository(service.path)
    result["ledger_update"] = ledger.update(
        market, now=now, path=service.root / "data/ledger/recommendations.jsonl"
    )
    result["risk_review"] = opportunity_risk.review_registered(
        service, result["items"], now, policy
    )
    stops = state.setdefault("risk_stops", {})
    for action in result["risk_review"]["actions"]:
        if action["action"] == "withdrawn":
            stops[action["asset"]] = (
                now + timedelta(days=policy["risk_cooldown_days"])
            ).isoformat()
    for item in result["items"]:
        until = stops.get(item["asset_id"])
        if until and now < datetime.fromisoformat(until):
            item.update(admitted=False, risk_cooldown_until=until)
            item["blocking_reasons"].append("风险撤销后的冷静期，禁止立即重新增加敞口")
            item["checks"].append(
                {
                    "check": "撤销后冷静期",
                    "passed": False,
                    "observed": until,
                    "required": f"撤销后{policy['risk_cooldown_days']}天",
                    "kind": "risk",
                }
            )
            item["tier"] = "RESEARCH_CANDIDATE" if item["economic_signal"] else "OBSERVE"
    state["shadow"] = opportunity_risk.shadow_account(market, state.get("shadow"), now, policy)
    result["shadow_account"] = state["shadow"]
    result["allocation"] = opportunity_risk.allocate(
        result["items"],
        policy,
        state.get("weights", {}),
        state["shadow"]["paused"] or bool(state["shadow"].get("error")),
    )
    if result["allocation"]["weights"] != state.get("weights", {}):
        state["shadow"]["pending"] = {
            "signal_day": now.date().isoformat(),
            "weights": result["allocation"]["weights"],
        }
    state["weights"] = result["allocation"]["weights"]
    # Persist a checkpoint before ledger writes: crash retries reuse the same
    # event and cached AI answers; source IDs make ledger registration idempotent.
    write_json(directory / "state.json", state)
    result["ledger"] = ledger.seed(
        now=now,
        path=service.root / "data/ledger/recommendations.jsonl",
        hypotheses_path=service.root / "data/research/hypotheses.jsonl",
        events_path=service.root / "data/leading/events.jsonl",
    )
    for item in result["items"]:
        decision_id = ledger._hash(
            {
                "input": item["input_sha256"],
                "checks": item["checks"],
                "hypothesis": (item.get("ai_hypothesis") or {}).get("hypothesis_id"),
                "weight": item["target_weight"],
            }
        )
        ledger.register(
            source_kind="opportunity_decision",
            source_id=decision_id,
            category="screening_observation",
            subject=item["name"],
            statement="完整研究决策：" + item["tier"],
            horizon_months=0,
            source_snapshot=item,
            now=now,
            path=service.root / "data/ledger/recommendations.jsonl",
        )
        # Zero-weight forward research cohorts make eventual validation possible
        # even while the investment model is not admitted. Their definition and
        # original horizon remain frozen, including after later revocation.
        observed = (
            item["economic_signal"]
            and item["data_risk_passed"]
            and item.get("ai_review") == "completed"
            and (item.get("ai_hypothesis") or {}).get("counter_verdict")
            in {"reinforced", "weakened"}
            and not item.get("risk_cooldown_until")
        )
        if not item["admitted"] and not observed:
            continue
        cohort = ledger._hash({"facts": item["facts"], "policy": policy, "asset": item["asset_id"]})
        for months in (3, 6):
            ledger.register(
                source_kind="conditional_opportunity" if item["admitted"] else "conditional_watch",
                source_id=cohort,
                category="asset_candidate",
                subject=item["name"],
                statement="有条件研究候选；按冻结准入及撤销规则跟踪"
                if item["admitted"]
                else "零权重前瞻研究候选；经济及数据门槛通过，等待模型投资有效性验证",
                asset=item["asset_id"],
                horizon_months=months,
                industries=[{"id": item["industry_id"], "name": item["name"]}],
                evidence=sorted({f["source_url"] for f in item["facts"] if f.get("source_url")}),
                invalidation_conditions=item["invalidation_conditions"],
                source_snapshot=item,
                now=now,
                path=service.root / "data/ledger/recommendations.jsonl",
            )
    result["new_cohort_update"] = ledger.update(
        market, now=now, path=service.root / "data/ledger/recommendations.jsonl"
    )
    result["items"].sort(key=lambda i: (-i["evidence_score"], i["industry_id"]))
    result.update(
        policy=policy,
        universe_size=len(specs),
        checked=len(result["items"]),
        status="degraded" if result["errors"] else "ok",
        finished_at=datetime.now(TZ_SHANGHAI).isoformat(),
        ai_pending=sum(i.get("ai_review") != "completed" for i in result["items"]),
        missing_inputs=["行业估值历史", "一致预期及预期差", "可回放的历史资金流"],
        model_boundary="方向概率未校准、尚无已验收模型优势；候选分值是证据排序，不能换算收益率",
    )
    write_json(directory / "latest.json", result)
    return result


class ResearchMonitor:
    def __init__(self, service: Workbench) -> None:
        self.service = service
        self.closed = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, name="event-research", daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        recent = read(self.service.root / "data/research-loop/latest.json", {})
        if recent.get("finished_at"):
            elapsed = (
                datetime.now(TZ_SHANGHAI) - datetime.fromisoformat(recent["finished_at"])
            ).total_seconds()
            if 0 <= elapsed < 900:
                self.closed.wait(900 - elapsed)
        while not self.closed.is_set():
            try:
                options = settings(self.service)
                if options.get("enabled"):
                    run(self.service, refresh=bool(options.get("capture_enabled")), scheduled=True)
            except Exception as exc:
                write_json(
                    self.service.root / "data/research-loop/monitor-error.json",
                    {"at": datetime.now(TZ_SHANGHAI).isoformat(), "error": str(exc)[:300]},
                )
            self.closed.wait(900)

    def stop(self) -> None:
        self.closed.set()
