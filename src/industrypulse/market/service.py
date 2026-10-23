"""统一行情/宏观数据服务：Dashboard 只消费本层标准模型，不关心底层来源。

数据链路（§5）：Raw Sources → providers → 标准模型 → DuckDB/内存缓存 → service → API。
所有源经 SourceHealthMonitor 记录健康；单一源失败降级不拖垮整个响应。
"""

from __future__ import annotations

import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import yaml

from industrypulse.contracts import TZ_SHANGHAI

from industrypulse.analytics.heat import compute_heat
from .health import SourceHealthMonitor
from .models import Bar, QuoteSnapshot
from .providers import akshare_provider, eastmoney, tencent

ROOT = Path(__file__).resolve().parents[3]
SECTORS_CONFIG = ROOT / "config/market/sectors.yaml"
MACRO_CONFIG = ROOT / "config/market/macro_indicators.yaml"

_TTL_QUOTES = 8 * 60
_TTL_KLINE = 12 * 3600
_TTL_FLOW = 30 * 60
_TREND_BARS = 180


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class MarketService:
    """行情 + 宏观 + 热度的统一入口；线程安全；单实例复用。"""

    def __init__(self, *, root: Path | None = None) -> None:
        base = root or Path(os.getenv("INDUSTRYPULSE_ROOT", str(ROOT)))
        self.sectors_config = _load_yaml(base / "config/market/sectors.yaml")
        self.macro_config = _load_yaml(base / "config/market/macro_indicators.yaml")
        self.db_path = base / "data/market.duckdb"
        self.health = SourceHealthMonitor(base / "data/market/health.json")
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_lock = threading.Lock()
        self._macro_sync_started = False
        self._refreshing = threading.Event()
        self._overview_path = base / "data/market/overview.json"
        self._config_fingerprint = str(
            hashlib.sha256(SECTORS_CONFIG.read_bytes()).hexdigest()[:16]
        ) if SECTORS_CONFIG.exists() else "dev"
        self._overview_memory: tuple[float, dict[str, Any]] | None = None
        self._pending_bars: list[tuple[str, list[Bar]]] = []
        self._pending_lock = threading.Lock()
        self._db_lock = threading.Lock()
        self.health.ensure(
            [
                "tencent:quotes", "tencent:kline",
                "eastmoney:kline", "eastmoney:moneyflow", "eastmoney:macro",
                "nbs-easyquery",
            ]
            + [f"sector:{domain_id}" for domain_id in self.sectors_config["domains"]]
        )
        self._init_db()

    # ------------------------------------------------------------------ infra
    def _init_db(self) -> None:
        import duckdb

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = duckdb.connect(str(self.db_path))
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS ui_bar("
            "symbol VARCHAR, trade_date DATE, close DOUBLE, volume DOUBLE, "
            "amount DOUBLE, source VARCHAR, PRIMARY KEY(symbol, trade_date))"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS macro_point("
            "indicator_id VARCHAR, period DATE, value DOUBLE, unit VARCHAR, "
            "source VARCHAR, fetched_at TIMESTAMP, "
            "PRIMARY KEY(indicator_id, period))"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS heat_point("
            "sector VARCHAR, day DATE, score DOUBLE, components JSON, "
            "PRIMARY KEY(sector, day))"
        )

    def _cached(self, key: str, ttl: int, loader: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        value = loader()
        with self._cache_lock:
            self._cache[key] = (now, value)
        return value

    # --------------------------------------------------------------- providers
    def quotes(self, symbols: list[str]) -> dict[str, QuoteSnapshot]:
        key = "quotes:" + ",".join(sorted(symbols))

        def load() -> dict[str, QuoteSnapshot]:
            try:
                return tencent.fetch_quotes(symbols)
            except Exception:  # noqa: BLE001 - 代理/网络瞬时失败重试一次
                time.sleep(0.6)
                return tencent.fetch_quotes(symbols)

        return self._cached(key, _TTL_QUOTES, load)

    def _kline(self, symbol: str) -> list[Bar]:
        return self._cached(f"kline:{symbol}", _TTL_KLINE, lambda: self._load_kline(symbol))

    def _load_kline(self, symbol: str) -> list[Bar]:
        if symbol.startswith("BK"):
            bars = eastmoney.fetch_board_kline(f"90.{symbol}")
            self.health.record_success("eastmoney:kline", 0)
        else:
            bars = tencent.fetch_daily_kline(symbol)
            self.health.record_success("tencent:kline", 0)
        self._pending_bars.append((symbol, bars))
        return bars

    def _flush_bars(self) -> None:
        with self._pending_lock:
            pending, self._pending_bars = self._pending_bars, []
        if not pending:
            return
        rows = [
            (symbol, bar.date, bar.close, bar.volume, bar.amount, bar.source)
            for symbol, bars in pending
            for bar in bars
        ]
        try:
            with self._db_lock:
                self._db.executemany(
                    "INSERT OR REPLACE INTO ui_bar VALUES (?, ?, ?, ?, ?, ?)", rows
                )
        except Exception as exc:  # noqa: BLE001 - 持久化失败不影响展示
            self.health.record_failure("market:persist", str(exc)[:120])

    def _moneyflow(self, board: str) -> list[dict[str, Any]]:
        def load() -> list[dict[str, Any]]:
            points = eastmoney.fetch_board_moneyflow(f"90.{board}")
            self.health.record_success("eastmoney:moneyflow", 0)
            return [
                {"date": point.date, "main_net_inflow": point.main_net_inflow}
                for point in points
            ]

        try:
            return self._cached(f"flow:{board}", _TTL_FLOW, load)
        except Exception as exc:  # noqa: BLE001 - 资金流失败降级为空
            self.health.record_failure("eastmoney:moneyflow", str(exc)[:160])
            return []

    # ------------------------------------------------------------ sector views
    def sector_ids(self) -> list[dict[str, str]]:
        return [
            {"id": domain_id, "name": config["name"]}
            for domain_id, config in self.sectors_config["domains"].items()
        ]

    def sector_snapshot(
        self, domain_id: str, stories: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        config = self.sectors_config["domains"].get(domain_id)
        if config is None:
            raise KeyError(domain_id)
        board = config["board"]
        etf_symbol = config["etfs"]["primary"]
        snapshot: dict[str, Any] = {"id": domain_id, "name": config["name"], "board": board}

        # 行业指数主源（东财板块），失败自动降级为该行业 ETF 真实 K 线（§8/§24）
        bars: list[Bar] | None = None
        try:
            bars = self._kline(board)
            snapshot["index_source"] = "eastmoney_board"
        except Exception as exc:  # noqa: BLE001
            self.health.record_failure("eastmoney:kline", str(exc)[:120])
            try:
                bars = self._kline(etf_symbol)
                snapshot["index_source"] = "etf"
            except Exception as exc2:  # noqa: BLE001
                self.health.record_failure("tencent:kline", str(exc2)[:120])

        if bars and len(bars) >= 2:
            last, prev = bars[-1], bars[-2]
            snapshot["change"] = round((last.close / prev.close - 1) * 100, 2)
            snapshot["latest_price"] = round(last.close, 2)
            snapshot["latest_date"] = last.date.isoformat()
            metric = last.amount if last.amount is not None else last.volume
            snapshot["amount"] = round(metric / 1e8, 2) if metric else None
            snapshot["amount_metric"] = "成交额(亿)" if last.amount else "成交量(亿份)"
            snapshot["trend"] = self._trend(bars)
            snapshot["trend_label"] = (
                "行业板块指数" if snapshot["index_source"] == "eastmoney_board" else "行业 ETF 净值"
            )
            amounts = [
                (bar.amount if bar.amount is not None else bar.volume) or 0.0 for bar in bars
            ]
        else:
            snapshot["index_status"] = "unavailable"
            amounts = []

        etf = self.quotes([etf_symbol]).get(etf_symbol)
        if etf:
            snapshot["etf"] = {
                "symbol": etf.symbol, "name": etf.name, "price": etf.price,
                "change_pct": etf.change_pct,
                "amount": round(etf.amount / 1e8, 2) if etf.amount else None,
            }
            if snapshot.get("change") is None:
                snapshot["change"] = etf.change_pct
                snapshot["change_source"] = "etf"

        try:
            flow = self._moneyflow(board)
            snapshot["net_flow"] = (
                round(flow[-1]["main_net_inflow"] / 1e8, 2) if flow else None
            )
            snapshot["net_flow_label"] = "板块主力净流入（东财）" if flow else None
            if not flow:
                snapshot["flow_status"] = "unavailable"
        except Exception:  # noqa: BLE001
            snapshot["net_flow"] = None
            snapshot["flow_status"] = "unavailable"

        news = stories or []
        now = datetime.now(TZ_SHANGHAI)
        count_24h = sum(1 for story in news if self._within(story, now, hours=24))
        count_prev = sum(
            1 for story in news if self._within(story, now - timedelta(hours=24), hours=24)
        )
        score, components = compute_heat(
            news_count_24h=count_24h,
            news_count_prev_24h=count_prev,
            amounts=amounts,
            change_pct=snapshot.get("change"),
        )
        snapshot["heat"] = score
        snapshot["heat_components"] = components
        snapshot["news_count_24h"] = count_24h
        return snapshot

    @staticmethod
    def _within(story: dict[str, Any], until: datetime, *, hours: int) -> bool:
        try:
            published = datetime.fromisoformat(str(story["published_at"]))
        except (ValueError, KeyError, TypeError):
            return False
        if published.tzinfo is None:
            published = published.replace(tzinfo=TZ_SHANGHAI)
        return until - timedelta(hours=hours) < published <= until

    def _trend(self, bars: list[Bar]) -> list[dict[str, Any]]:
        window = bars[-(_TREND_BARS + 1):]
        if not window:
            return []
        base = window[0].close or 1.0
        return [
            {
                "date": bar.date.isoformat(),
                "value": round(bar.close / base * 100, 2),
            }
            for bar in window[1:]
        ]

    def benchmark_trend(self, symbol: str) -> list[dict[str, Any]]:
        return self._trend(self._kline(symbol))

    def overview(
        self, stories: list[dict[str, Any]] | None = None, *, wait: bool = False
    ) -> dict[str, Any]:
        """市场总览：优先返回快照缓存；过期时后台刷新，绝不阻塞 API。"""
        snapshot, age = self._overview_snapshot()
        if age is not None and age < _TTL_QUOTES:
            return {**snapshot, "status": "ok"}
        if wait:
            return self._build_overview(stories)
        if self._refreshing.is_set():
            return {**snapshot, "status": "ok" if age is not None and age < _TTL_FLOW else "refreshing"}
        self._refreshing.set()
        threading.Thread(
            target=self._refresh_overview,
            args=(stories or [],),
            daemon=True,
            name="market-overview-refresh",
        ).start()
        if snapshot:
            return {**snapshot, "status": "refreshing"}
        return {
            "updated_at": datetime.now(TZ_SHANGHAI).isoformat(),
            "source_label": "东方财富行业板块 + 腾讯行情（公开接口）",
            "status": "cold",
            "message": "首次启动正在拉取行情，约几十秒后可用",
            "benchmarks": [],
            "sectors": [],
        }

    def _refresh_overview(self, stories: list[dict[str, Any]]) -> None:
        try:
            self._build_overview(stories)
        finally:
            self._refreshing.clear()

    def _overview_snapshot(self) -> tuple[dict[str, Any] | None, float | None]:
        import json as jsonlib

        if self._overview_memory:
            age = time.time() - self._overview_memory[0]
            return self._overview_memory[1], age
        if self._overview_path.exists():
            try:
                payload = jsonlib.loads(self._overview_path.read_text(encoding="utf-8"))
                if payload.get("_config") != self._config_fingerprint:
                    return None, None  # 配置已变更，旧快照作废
                age = time.time() - payload.get("_built_at", 0)
                data = {key: value for key, value in payload.items()
                        if key not in ("_built_at", "_config")}
                self._overview_memory = (payload["_built_at"], data)
                return data, age
            except (OSError, ValueError, KeyError):
                return None, None
        return None, None

    def _build_overview(self, stories: list[dict[str, Any]] | None) -> dict[str, Any]:
        stories = stories or []
        benchmarks = self.sectors_config["benchmarks"]
        quote_symbols = [item["symbol"] for item in benchmarks.values()]
        quote_symbols += [
            config["etfs"]["primary"] for config in self.sectors_config["domains"].values()
        ]
        try:
            snapshots = self.quotes(sorted(set(quote_symbols)))
            self.health.record_success("tencent:quotes", 0)
        except Exception as exc:  # noqa: BLE001
            snapshots = {}
            self.health.record_failure("tencent:quotes", str(exc)[:160])

        benchmark_view = []
        for key, config in benchmarks.items():
            quote = snapshots.get(config["symbol"])
            benchmark_view.append(
                {
                    "id": key,
                    "name": config["name"],
                    "symbol": config["symbol"],
                    "price": quote.price if quote else None,
                    "change_pct": quote.change_pct if quote else None,
                    "quoted_at": quote.quoted_at.isoformat() if quote and quote.quoted_at else None,
                }
            )

        domain_ids = list(self.sectors_config["domains"])
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="sector") as pool:
            sectors = list(
                pool.map(
                    lambda domain_id: self._safe_sector(domain_id, stories),
                    domain_ids,
                )
            )
        self._flush_bars()
        csi300 = benchmarks["csi300"]["symbol"]
        try:
            benchmark_series = self.benchmark_trend(csi300)
        except Exception:  # noqa: BLE001
            benchmark_series = []
        for sector in sectors:
            for point in sector.get("trend", []):
                match = next(
                    (item for item in benchmark_series if item["date"] == point["date"]), None
                )
                point["benchmark"] = match["value"] if match else None

        overview = {
            "_built_at": time.time(),
            "_config": self._config_fingerprint,
            "updated_at": datetime.now(TZ_SHANGHAI).isoformat(),
            "source_label": "东方财富行业板块 + 腾讯行情（公开接口）",
            "benchmark_label": "沪深300",
            "trend_label": "行业板块指数（起点 100）",
            "flow_label": "板块主力净流入",
            "benchmarks": benchmark_view,
            "sectors": sectors,
            "status": "ok",
        }
        self._overview_memory = (overview["_built_at"], {
            key: value for key, value in overview.items()
            if key not in ("_built_at", "_config")
        })
        self._overview_path.parent.mkdir(parents=True, exist_ok=True)
        import json as jsonlib

        self._overview_path.write_text(
            jsonlib.dumps(overview, ensure_ascii=False, default=str), encoding="utf-8"
        )
        return {
            key: value for key, value in overview.items()
            if key not in ("_built_at", "_config")
        }

    def _safe_sector(self, domain_id: str, stories: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            return self.sector_snapshot(domain_id, stories)
        except Exception as exc:  # noqa: BLE001 - 单行业失败不拖垮总览
            self.health.record_failure(f"sector:{domain_id}", str(exc)[:160])
            return {
                "id": domain_id,
                "name": self.sectors_config["domains"][domain_id]["name"],
                "status": "failed",
                "message": "行情源暂时失败，已保留上次数据",
            }

    def sector_detail(
        self, domain_id: str, stories: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        overview, _ = self._overview_snapshot()
        cached = next((s for s in (overview or {}).get("sectors", []) if s.get("id") == domain_id), None)
        if cached and cached.get("status") != "failed":
            self.overview(stories)  # 过期则顺带触发后台刷新
            return cached
        return self.sector_snapshot(domain_id, stories)

    # ------------------------------------------------------------------ macro
    def macro(self) -> dict[str, Any]:
        with self._db_lock:
            rows = self._db.execute(
                "SELECT indicator_id, period, value, unit, source, fetched_at "
                "FROM macro_point ORDER BY indicator_id, period"
            ).fetchall()
        grouped: dict[str, list[tuple[Any, ...]]] = {}
        for row in rows:
            grouped.setdefault(row[0], []).append(row)
        items: list[dict[str, Any]] = []
        for indicator in self.macro_config["indicators"]:
            history = grouped.get(indicator["id"], [])
            if not history:
                items.append(
                    {
                        "id": indicator["id"], "name": indicator["name"],
                        "unit": indicator["unit"], "status": "missing",
                        "message": "尚未同步（运行 market sync --macro）",
                    }
                )
                continue
            latest = history[-1]
            previous = history[-2] if len(history) > 1 else None
            fetched = latest[5]
            fetch_age = (datetime.now() - fetched).days if fetched else 999
            period_age = (date.today() - latest[1]).days if latest[1] else 999
            freq_limit = {"daily": 7, "monthly": 70, "quarterly": 220}.get(
                str(indicator["freq"]), 70
            )
            stale_days = max(period_age, 0) if period_age > freq_limit else fetch_age
            items.append(
                {
                    "id": indicator["id"],
                    "name": indicator["name"],
                    "value": latest[2],
                    "unit": latest[3] or indicator["unit"],
                    "period": latest[1].isoformat() if latest[1] else None,
                    "previous_value": previous[2] if previous else None,
                    "previous_period": previous[1].isoformat() if previous and previous[1] else None,
                    "source": latest[4],
                    "freq": indicator["freq"],
                    "fetched_at": fetched.isoformat() if fetched else None,
                    "status": "stale" if stale_days > 45 else "fresh",
                }
            )
        return {"updated_at": datetime.now(TZ_SHANGHAI).isoformat(), "indicators": items}

    def sync_macro(self) -> dict[str, int]:
        counts = {"ok": 0, "fail": 0}
        by_source: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for indicator in self.macro_config["indicators"]:
            key = (indicator["provider"], indicator["source"])
            if key not in by_source:
                by_source[key] = self._fetch_macro_source(indicator)
            series = by_source[key]
            rows = self._indicator_rows(indicator, series)
            payloads = [
                (
                    indicator["id"], row["period"], row["value"],
                    indicator["unit"], f"{indicator['provider']}:{indicator['source']}",
                    datetime.now(TZ_SHANGHAI),
                )
                for row in rows
                if row.get("value") is not None
            ]
            if payloads:
                with self._db_lock:
                    self._db.executemany(
                        "INSERT OR REPLACE INTO macro_point VALUES (?, ?, ?, ?, ?, ?)",
                        payloads,
                    )
            counts["ok" if rows else "fail"] = counts["ok" if rows else "fail"] + 1
        return counts

    def _fetch_macro_source(self, indicator: dict[str, Any]) -> list[dict[str, Any]]:
        source_id = f"{indicator['provider']}:macro:{indicator['source']}"
        start = time.perf_counter()
        try:
            if indicator["provider"] == "eastmoney":
                if indicator["source"] == "RPT_MUTUAL_DEAL_HISTORY":
                    series = eastmoney.fetch_northbound()
                else:
                    series = eastmoney.fetch_macro_report(indicator["source"])
            else:
                frame = akshare_provider.fetch_frame(indicator["source"])
                columns = [
                    column
                    for column in frame.columns
                    if column != indicator["date_field"]
                ]
                series = akshare_provider.normalize_frame(
                    frame, indicator["date_field"], columns
                )
        except Exception as exc:  # noqa: BLE001
            self.health.record_failure(source_id, str(exc)[:160])
            return []
        self.health.record_success(source_id, int((time.perf_counter() - start) * 1000))
        return series

    def _indicator_rows(
        self, indicator: dict[str, Any], series: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        field = indicator["field"]
        for item in series:
            if "values" in item:  # akshare 归一化形态
                period, value = item["period"], item["values"].get(field)
            else:  # eastmoney 原始行形态
                period = self._row_period(indicator, item)
                value = item.get(field)
                if isinstance(value, str):
                    try:
                        value = float(value)
                    except ValueError:
                        value = None
            if period is None or value is None:
                continue
            rows.append({"period": period, "value": float(value)})
        return rows

    @staticmethod
    def _row_period(indicator: dict[str, Any], item: dict[str, Any]) -> date | None:
        raw = item.get(indicator["date_field"])
        if raw is None:
            return None
        try:
            return akshare_provider.parse_period(raw)
        except ValueError:
            return None

    # -------------------------------------------------------------- companies
    def company_quotes(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for domain_id, config in self.sectors_config["domains"].items():
            for company in config.get("companies", []):
                rows.append({**company, "sector": domain_id})
        symbols = [row["symbol"] for row in rows]
        try:
            snapshots = self.quotes(symbols)
            self.health.record_success("tencent:quotes", 0)
        except Exception as exc:  # noqa: BLE001
            self.health.record_failure("tencent:quotes", str(exc)[:160])
            snapshots = {}
        enriched = []
        for row in rows:
            quote = snapshots.get(row["symbol"])
            enriched.append(
                {
                    **row,
                    "price": quote.price if quote else None,
                    "change_pct": quote.change_pct if quote else None,
                    "market_cap": quote.market_cap if quote else None,
                    "pe": quote.pe if quote else None,
                    "pb": quote.pb if quote else None,
                    "turnover_pct": quote.turnover_pct if quote else None,
                    "quoted_at": quote.quoted_at.isoformat() if quote and quote.quoted_at else None,
                }
            )
        return enriched

    # ------------------------------------------------------------------- misc
    def data_health(self) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in self.health.snapshot()]

    def ensure_macro_sync(self) -> None:
        """表为空时后台触发一次宏观同步（不阻塞 API）。"""
        if self._macro_sync_started:
            return
        with self._db_lock:
            count = self._db.execute("SELECT COUNT(*) FROM macro_point").fetchone()[0]
        if count == 0:
            self._macro_sync_started = True
            threading.Thread(target=self.sync_macro, daemon=True, name="macro-sync").start()



def enrich_sector_trends(feed: dict[str, Any], market: MarketService) -> dict[str, Any]:
    """把 market overview 合并进现有 feed dict（§26：不再由 feed() 独自拼装）。"""
    overview = market.overview(feed.get("stories") or [])
    by_id = {item["id"]: item for item in overview["sectors"]}
    for sector in feed.get("sectors", []):
        snapshot = by_id.get(sector["id"])
        if not snapshot:
            continue
        sector["change"] = snapshot.get("change")
        sector["net_flow"] = snapshot.get("net_flow")
        sector["heat"] = snapshot.get("heat")
        sector["heat_components"] = snapshot.get("heat_components")
        sector["news_count_24h"] = snapshot.get("news_count_24h")
        sector["trend"] = [
            {"date": point["date"], "value": point["value"], "benchmark": point.get("benchmark")}
            for point in snapshot.get("trend", [])
        ]
        sector["latest_date"] = snapshot.get("latest_date")
    leaders = market.company_quotes()
    quotes_by_name = {row["name"]: row for row in leaders}
    for company in feed.get("companies", []):
        leader = quotes_by_name.get(company.get("name") or "")
        if leader and leader.get("change_pct") is not None:
            company["change"] = leader["change_pct"]
            company["symbol"] = leader["symbol"]
            company["quote"] = {
                key: leader[key]
                for key in ("price", "market_cap", "pe", "pb", "turnover_pct", "quoted_at")
            }
    # 行业龙头直接注入公司页（§21）：新闻公司 + 带行情的龙头并存
    existing_names = {company.get("name") for company in feed.get("companies", [])}
    for leader in leaders:
        if leader["name"] in existing_names or leader.get("price") is None:
            continue
        feed.setdefault("companies", []).append(
            {
                "id": f"leader-{leader['symbol']}",
                "sector": leader["sector"],
                "name": leader["name"],
                "symbol": leader["symbol"],
                "focus": "行业龙头 · 公开行情",
                "change": leader.get("change_pct"),
                "quote": {
                    key: leader.get(key)
                    for key in ("price", "market_cap", "pe", "pb", "turnover_pct", "quoted_at")
                },
            }
        )
    feed["market"] = {
        "updated_at": overview["updated_at"],
        "source_label": overview["source_label"],
        "benchmark_label": overview["benchmark_label"],
        "trend_label": overview["trend_label"],
        "flow_label": overview["flow_label"],
        "benchmarks": overview["benchmarks"],
        "status": "ok",
    }
    return feed
