"""本地账本采集、报价续接、报告及独立监控；不发送通知，不调用模型。"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx
import yaml

from industrypulse.contracts import TZ_SHANGHAI
from industrypulse.repositories import DuckDBMarketRepository
from industrypulse.services import ledger
from industrypulse.services.workbench import write_json

if TYPE_CHECKING:
    from industrypulse.services.workbench import Workbench


def settings(service: Workbench) -> dict[str, Any]:
    path = service.root / "data/ledger/settings.json"
    return (
        json.loads(path.read_text(encoding="utf-8"))
        if path.exists()
        else {"enabled": False, "refresh_prices": True}
    )


def configure(service: Workbench, enabled: bool) -> dict[str, Any]:
    result = {
        **settings(service),
        "enabled": enabled,
        "timezone": "Asia/Shanghai",
        "interval_minutes": 15,
        "requires": "本地应用持续运行；重启自动恢复，离线期间不伪造记录",
    }
    write_json(service.root / "data/ledger/settings.json", result)
    return result


def refresh_prices(
    service: Workbench, market: DuckDBMarketRepository, *, all_mapped: bool = False
) -> list[dict[str, Any]]:
    from industrypulse.contracts.domain import Exchange
    from industrypulse.datahub.fund_dividend import parse_dividend_table, to_corporate_actions
    from industrypulse.datahub.sina_etf import fetch_sina_kline
    from industrypulse.services.etf_ingest import EtfIngestService

    config = yaml.safe_load(
        (service.root / "config/market/industry_price_lines.yaml").read_text(encoding="utf-8")
    )
    specs = {
        spec["asset_id"]: spec for spec in [config["benchmark"], *config["industries"].values()]
    }
    active = {
        r["asset"]
        for r in ledger.latest_versions(service.root / "data/ledger/recommendations.jsonl").values()
        if r["asset"] and r["status"] not in ledger.TERMINAL
    }
    # 示范链下一次筛选需要当日对口价格；不额外全市场回填。
    active.update(
        spec["asset_id"]
        for iid, spec in config["industries"].items()
        if iid in {"CN_NBS_D44", "CN_NBS_C38"} and spec["fit"] == "对口"
    )
    active.add(config["benchmark"]["asset_id"])
    if all_mapped:
        active.update(specs)
    outcomes = []
    pipeline = EtfIngestService(market, bronze_root=service.path.parent / "bronze-etf")
    with httpx.Client(timeout=15, follow_redirects=True) as client:
        for aid in sorted(active):
            spec = specs.get(aid)
            if spec is None:
                outcomes.append({"asset": aid, "error": "未在统一价格映射登记，不猜测代码"})
                continue
            item: dict[str, Any] = {"asset": aid}
            try:
                report = pipeline.ingest_symbol(
                    symbol=spec["symbol"],
                    asset_id=aid,
                    name_zh=spec["name"],
                    exchange=Exchange.SSE if spec["symbol"].startswith("sh") else Exchange.SZSE,
                    fetch=lambda code: fetch_sina_kline(code, client=client, interval=1.5),
                )
                item.update(inserted=report.inserted, last_day=report.last_day)
            except (ValueError, OSError, httpx.HTTPError) as exc:
                item["error"] = str(exc)[:240]
            try:
                url = f"https://fund.eastmoney.com/f10/FHSP_{spec['symbol'][2:]}.html"
                response = client.get(url)
                response.raise_for_status()
                content = response.content
                raw_hash = hashlib.sha256(content).hexdigest()
                archive = service.root / "data/ledger/dividend-raw" / f"{raw_hash}.html"
                if not archive.exists():
                    archive.parent.mkdir(parents=True, exist_ok=True)
                    archive.write_bytes(content)
                elif hashlib.sha256(archive.read_bytes()).hexdigest() != raw_hash:
                    raise ValueError("分红原件归档的字节指纹不一致，停止复用")
                from industrypulse.services.trusted_baseline import decode, parse_eastmoney

                decoded = decode(response)
                parse_eastmoney(decoded, spec["symbol"][2:])
                actions = to_corporate_actions(
                    parse_dividend_table(decoded),
                    asset_id=aid,
                    source_note=f"{url}; ledger capture {datetime.now(TZ_SHANGHAI).isoformat()}",
                )
                for action in actions:
                    previous = next(
                        (
                            a
                            for a in market.actions(aid)
                            if a.ex_date == action.ex_date and a.action_type == action.action_type
                        ),
                        None,
                    )
                    if previous is not None:
                        if (
                            previous.distribution_per_unit != action.distribution_per_unit
                            or previous.currency != action.currency
                        ):
                            raise ValueError(
                                f"{aid} {action.ex_date} 分红金额与冻结记录冲突，保留原记录待核验"
                            )
                        # 重新抓取的时间/来源备注不能改变不可变行动，数值相同则幂等复用。
                        continue
                    market.register_action(action)
                item.update(dividends=len(actions), dividend_raw=str(archive))
            except (ValueError, OSError, httpx.HTTPError) as exc:
                item["dividend_error"] = str(exc)[:240]
            outcomes.append(item)
    return outcomes


def capture_screen(
    service: Workbench, market: DuckDBMarketRepository, now: datetime
) -> dict[str, Any]:
    from industrypulse.contracts.domain import AssetMaster, MarketBar
    from industrypulse.services.chain_demo import EvidenceReplay, decision, freeze_inputs

    repository = service.repository()
    bundle = freeze_inputs(
        repository,
        market,
        root=service.root,
        snapshot_id=service.snapshot(repository),
        as_of=now,
        start=now.date(),
    )
    # 持续前瞻查询全目录的当前可见版本，不能停留在旧的最大历史快照。
    series_ids = sorted({row["series_id"] for row in bundle["vintages"]})
    rows = repository.all_vintages(series_ids)
    bundle["vintages"] = [row.model_dump(mode="json") for row in rows]
    bundle["snapshot_id"] = "live-catalog:" + ledger._hash(bundle["vintages"])
    assets = {
        a.asset_id: a for a in (AssetMaster.model_validate(item) for item in bundle["assets"])
    }
    bars: dict[str, list[MarketBar]] = {aid: [] for aid in assets}
    for item in bundle["bars"]:
        bar = MarketBar.model_validate(item)
        bars[bar.asset_id].append(bar)
    screen = decision(EvidenceReplay(bundle), bundle, now.date(), assets, bars)
    path = service.root / "data/ledger/recommendations.jsonl"
    # 筛选快照每天最多一份/同数据状态，保留未入选理由；不回填历史买点。
    identity = ledger._hash({"day": screen["day"], "candidates": screen["candidates"]})
    ledger.register(
        source_kind="chain_screen",
        source_id=identity,
        category="screening_observation",
        subject=bundle["config"]["name"],
        statement="本次产业链筛选："
        + ("有研究候选" if screen["weights"] else "无符合条件的资产候选"),
        horizon_months=0,
        source_snapshot=screen,
        now=now,
        path=path,
    )
    created = 0
    for item in screen["candidates"]:
        # The complete opportunity admission/risk service owns new asset windows.
        # Retain this historical demo as an observation, not a parallel bypass.
        if (service.root / "data/research-loop/settings.json").exists():
            continue
        if not item["selected"]:
            continue
        identity = ledger._hash([bundle["config"], item["industry_id"], item["facts"]])
        for months in (3, 6):
            result = ledger.register(
                source_kind="chain_candidate",
                source_id=identity,
                category="asset_candidate",
                subject=item["name"],
                statement="固定示范规则全部通过；待验证的资产研究候选",
                industries=[{"id": item["industry_id"], "name": item["name"]}],
                asset=item["asset_id"],
                horizon_months=months,
                evidence=sorted({f["source_url"] for f in item["facts"] if f["source_url"]}),
                invalidation_conditions=[
                    "PMI生产不再满足扩张及增幅条件",
                    "行业利润改善或营收正增长条件失效",
                    "价格过期、公司行动不明时暂停收益评价",
                ],
                source_snapshot={
                    "snapshot_id": bundle["snapshot_id"],
                    "config": bundle["config"],
                    "candidate": item,
                },
                now=now,
                path=path,
            )
            created += result["created"]
    return {"screen": screen, "created_asset_windows": created}


def capture_growth(service: Workbench, now: datetime) -> dict[str, Any]:
    """把增长机会页同规则的全行业假设留档；不把它们映射为买入推荐。"""
    from industrypulse.economy.price_check import adjacent_periods
    from industrypulse.economy.state import growth_hypothesis
    from industrypulse.services.chain_demo import EvidenceReplay

    repository = service.repository()
    industries = {}
    wanted: set[str] = set()
    for path in sorted((service.root / "config/industries").glob("*.yaml")):
        registry = service.taxonomy(path.stem)
        profit_id = next(
            (s.series_id for s in registry.series if s.series_id.endswith("profit_ytd_yoy")), None
        )
        if registry.synthetic or profit_id is None:
            continue
        node = registry.nodes[0]
        specs = {
            kind: profit_id.replace("profit_ytd_yoy", kind + "_ytd_yoy")
            for kind in ("profit", "revenue", "cost")
        }
        industries[node.industry_id] = (node.names.get("zh", node.industry_id), specs)
        wanted.update(specs.values())
    bundle = {
        "vintages": [
            row.model_dump(mode="json") for row in repository.all_vintages(sorted(wanted))
        ],
        "policy": {
            "trading_days": [d.isoformat() for d in repository.trading_days],
            "lag_days": repository.conservative_lag_days,
            "series_lag_days": dict(repository.series_lag_days),
        },
    }
    replay = EvidenceReplay(bundle)
    selected = replay.select(now)
    created = 0
    for iid, (name, specs) in industries.items():
        profit = selected.get(specs["profit"], [])
        if len(profit) < 2:
            continue
        current, previous = profit[-1], profit[-2]
        revenue_rows = selected.get(specs["revenue"], [])
        if not revenue_rows or revenue_rows[-1].observation_start != current.observation_start:
            continue
        revenue = revenue_rows[-1]
        cost = next(
            (
                row
                for row in reversed(selected.get(specs["cost"], []))
                if row.observation_start == current.observation_start
            ),
            None,
        )
        if (
            current.value is None
            or previous.value is None
            or revenue.value is None
            or not adjacent_periods(previous.observation_start, current.observation_start)
        ):
            continue
        hypothesis: dict[str, Any] | None = growth_hypothesis(
            industry_id=iid,
            name=name,
            period=current.observation_start,
            profit_yoy=float(current.value),
            profit_change=float(current.value - previous.value),
            revenue_yoy=float(revenue.value),
            cost_yoy=float(cost.value) if cost and cost.value is not None else None,
            source_urls=sorted(
                {
                    str(row.source_url)
                    for row in (current, previous, revenue, cost)
                    if row and row.source_url
                }
            ),
        )
        if hypothesis is None:
            continue
        facts = [replay.fact(row) for row in (current, previous, revenue, cost) if row]
        source_id = str(hypothesis["hypothesis_id"]) + ":" + ledger._hash(facts)[:16]
        result = ledger.register(
            source_kind="growth_hypothesis",
            source_id=source_id,
            category="economic_hypothesis",
            subject=name,
            statement=str(hypothesis["theme"]) + "；" + str(hypothesis["mechanism"]),
            industries=[{"id": iid, "name": name}],
            horizon_months=int(hypothesis["horizon_months"]),
            source_at=max(
                replay.available(row) for row in (current, previous, revenue)
            ).isoformat(),
            source_snapshot={"hypothesis": hypothesis, "facts": facts},
            evidence=list(hypothesis["source_urls"]),
            invalidation_conditions=list(hypothesis["invalidation_conditions"]),
            next_verification="核对下一次发布的行业利润、收入及成本累计同比",
            now=now,
            path=service.root / "data/ledger/recommendations.jsonl",
        )
        created += result["created"]
    return {"industries_checked": len(industries), "created_hypotheses": created}


def _run(
    service: Workbench, *, now: datetime | None = None, refresh: bool = False
) -> dict[str, Any]:
    moment = ledger._moment(now)
    directory = service.root / "data/ledger"
    path = directory / "recommendations.jsonl"
    health_path = directory / "health.json"
    previous_health = (
        json.loads(health_path.read_text(encoding="utf-8")) if health_path.exists() else {}
    )
    result: dict[str, Any] = {
        "started_at": moment.isoformat(),
        "errors": [],
        "prices": previous_health.get("prices", []),
        "latest_refresh_at": moment.isoformat()
        if refresh
        else previous_health.get("latest_refresh_at"),
        "price_attempt_day": moment.date().isoformat()
        if refresh
        else previous_health.get("price_attempt_day"),
    }
    try:
        result["seed"] = ledger.seed(
            now=moment,
            path=path,
            hypotheses_path=service.root / "data/research/hypotheses.jsonl",
            events_path=service.root / "data/leading/events.jsonl",
        )
        market = DuckDBMarketRepository(service.path)
        if refresh:
            result["prices"] = refresh_prices(service, market)
        result["errors"].extend(
            item for item in result["prices"] if item.get("error") or item.get("dividend_error")
        )
        try:
            result["screening"] = capture_screen(service, market, moment)
        except (ValueError, OSError) as exc:
            result["errors"].append({"stage": "screening", "error": str(exc)[:240]})
        try:
            result["growth"] = capture_growth(service, moment)
        except (ValueError, OSError) as exc:
            result["errors"].append({"stage": "growth", "error": str(exc)[:240]})
        result["update"] = ledger.update(market, now=moment, path=path)
        daily = ledger.daily_report(now=moment, path=path)
        weekly = ledger.weekly_report(
            now=moment, path=path, tool_journal=service.root / "data/research/tool-journal.jsonl"
        )
        for name, payload, formatter in (
            ("daily", daily, ledger.format_daily),
            ("weekly", weekly, ledger.format_weekly),
        ):
            write_json(directory / f"{name}.json", payload)
            (directory / f"{name}.txt").write_text(formatter(payload), encoding="utf-8")
        result["status"] = "degraded" if result["errors"] else "ok"
        result["head_hash"] = daily["head_hash"]
    except Exception as exc:  # 监控失败留痕，不改写账本、不影响主工作台
        result.update(
            status="failed",
            errors=[*result["errors"], {"stage": "ledger", "error": str(exc)[:300]}],
        )
    result["finished_at"] = datetime.now(TZ_SHANGHAI).isoformat()
    write_json(directory / "health.json", result)
    return result


def run(
    service: Workbench, *, now: datetime | None = None, refresh: bool = False
) -> dict[str, Any]:
    # CLI、界面和后台线程共享跨进程租约，防止重复网络采集与报告互相覆盖。
    with ledger._lock(service.root / "data/ledger/runtime"):
        return _run(service, now=now, refresh=refresh)


class LedgerMonitor:
    def __init__(self, service: Workbench) -> None:
        self.service = service
        self.closed = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._loop, name="forward-ledger", daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        while not self.closed.is_set():
            try:
                options = settings(self.service)
                if options.get("enabled"):
                    now = datetime.now(TZ_SHANGHAI)
                    health_path = self.service.root / "data/ledger/health.json"
                    health = (
                        json.loads(health_path.read_text(encoding="utf-8"))
                        if health_path.exists()
                        else {}
                    )
                    # 每个收盘日最多自动尝试一次网络刷新；失败可手动重试，不刷爆上游。
                    refresh = bool(
                        options.get("refresh_prices", True)
                        and now.hour >= 18
                        and health.get("price_attempt_day") != now.date().isoformat()
                    )
                    outcome = run(self.service, now=now, refresh=refresh)
                    outcome["price_attempt_day"] = (
                        now.date().isoformat() if refresh else health.get("price_attempt_day")
                    )
                    write_json(health_path, outcome)
            except Exception as exc:
                write_json(
                    self.service.root / "data/ledger/monitor-error.json",
                    {"at": datetime.now(TZ_SHANGHAI).isoformat(), "error": str(exc)[:300]},
                )
            self.closed.wait(15 * 60)

    def stop(self) -> None:
        self.closed.set()
