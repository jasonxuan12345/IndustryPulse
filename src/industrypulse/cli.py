"""IndustryPulse 统一 CLI（IP-009）。

G2 真源接入之前，仓库里没有任何可执行的业务入口：所有流程只能靠测试里的
Python 样板串起来。本模块提供最小但完整的操作面：

    industrypulse doctor                        环境与依赖自检
    industrypulse source list                   列出目录中的候选来源与许可状态
    industrypulse source permit SRC-NBS-PROFIT --as cn.nbs ...
                                                完成许可核验并把源登记进仓储（写审计）
    industrypulse init --database ...           登记 taxonomy / series
    industrypulse ingest ...                    摄取一份官方文档 -> Bronze -> Silver
    industrypulse calendar validate ...         校验交易日历并记录出处指纹

推荐顺序：doctor -> source permit -> init -> calendar validate -> ingest。
"""

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, NoReturn

import httpx
import typer

from industrypulse.contracts import CONTRACTS_VERSION
from industrypulse.contracts.context import ResearchMode
from industrypulse.contracts.domain import Exchange
from industrypulse.contracts.observation import RawArtifact
from industrypulse.contracts.sources import LegalStatus, SeriesSpec, SourceDescriptor
from industrypulse.core import CalendarError, Settings, load_trading_calendar
from industrypulse.core.calendar import DEFAULT_MAX_GAP_DAYS
from industrypulse.datahub.adapters import (
    PMI_MATRIX_COLUMNS,
    ParsedObservation,
    parse_iva,
    parse_pmi,
    parse_pmi_matrix,
    parse_profit,
    parse_shfe_daily,
    parse_tsf,
    resolve_table_column,
)
from industrypulse.datahub.exchange_calendar import (
    ProbeError,
    build_calendar,
    derive_trading_days,
)
from industrypulse.datahub.fetcher import Fetcher
from industrypulse.datahub.fund_dividend import (
    DividendParseError,
    parse_dividend_table,
    to_corporate_actions,
)
from industrypulse.datahub.sina_etf import fetch_sina_kline
from industrypulse.datahub.source_registry import SourceRegistry
from industrypulse.repositories import (
    DuckDBMarketRepository,
    DuckDBObservationRepository,
    MarketDataError,
    PublicationError,
)
from industrypulse.services import (
    AsPublishedClaim,
    BacktestRequest,
    BatchItem,
    ClaimError,
    EtfIngestError,
    EtfIngestService,
    IngestError,
    IngestService,
    MonthlyBackfill,
    build_handlers,
    format_table,
    parse_claim,
    run_backtest,
)
from industrypulse.services import (
    compare_modes as build_pit_comparison,
)
from industrypulse.taxonomy import TaxonomyRegistry

app = typer.Typer(add_completion=False, no_args_is_help=True, help="IndustryPulse 本地 CLI")
source_app = typer.Typer(no_args_is_help=True, help="数据源目录、许可与生命周期")
calendar_app = typer.Typer(no_args_is_help=True, help="交易日历装载与校验")
etf_app = typer.Typer(no_args_is_help=True, help="ETF/指数行情采集")
app.add_typer(source_app, name="source")
app.add_typer(calendar_app, name="calendar")
app.add_typer(etf_app, name="etf")

DEFAULT_CATALOG = Path("source_catalog.csv")
DEFAULT_TAXONOMY = Path("config/industries/steel.yaml")
DEFAULT_AUDIT = Path("data/registry-audit.jsonl")


def _emit(payload: Any) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _fail(message: str) -> NoReturn:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


SHFE_DAILY_ENDPOINT = "https://www.shfe.com.cn/data/tradedata/future/dailydata/kx{stamp}.dat"


@calendar_app.command("derive")
def calendar_derive(
    start: Annotated[str, typer.Option("--from", help="起始日 YYYY-MM-DD")],
    end: Annotated[str, typer.Option("--to", help="结束日 YYYY-MM-DD")],
    out: Annotated[Path, typer.Option("--out", help="输出规范化日历 JSON")],
    exchange: Annotated[str, typer.Option("--exchange", help="目前仅支持 shfe")] = "shfe",
    interval: Annotated[float, typer.Option("--interval", help="请求间隔秒数")] = 0.15,
    lag_days: Annotated[int, typer.Option("--lag-days", help="行情发布滞后天数")] = 2,
) -> None:
    """按"官方日行情文件是否存在"推导交易日历（可验证证据，不猜）。"""
    if exchange.lower() != "shfe":
        _fail(f"暂不支持交易所 {exchange}；目前仅实现 shfe")
    try:
        first = date.fromisoformat(start)
        last = date.fromisoformat(end)
    except ValueError as exc:
        _fail(f"--from/--to 不是合法日期：{exc}")
    headers = {
        "User-Agent": "IndustryPulse/0.1 (research; contact=local)",
        "Referer": "https://www.shfe.com.cn/",
    }

    with httpx.Client(timeout=30, follow_redirects=False, headers=headers) as client:

        def probe(day: date) -> bool:
            url = SHFE_DAILY_ENDPOINT.format(stamp=day.strftime("%Y%m%d"))
            for _ in range(2):
                time.sleep(interval)
                try:
                    response = client.head(url)
                except httpx.HTTPError as exc:
                    raise ProbeError(f"{day} 探测失败：{exc!r}") from exc
                if response.status_code == 200:
                    return True
                if response.status_code != 404:
                    raise ProbeError(f"{day} 返回未知状态 {response.status_code}，拒绝推断")
            return False

        try:
            days, _evidence, report = derive_trading_days(
                probe, start=first, end=last, today=date.today(), publication_lag_days=lag_days
            )
        except ProbeError as exc:
            _fail(str(exc))
    calendar = build_calendar(
        days,
        report=report,
        source_ref=f"SHFE 日行情端点探测 {SHFE_DAILY_ENDPOINT}",
        version=f"shfe-derived-{report.evidence_sha256[:12]}",
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(calendar.model_dump_json(indent=2) + "\n", encoding="utf-8")
    _emit(
        {
            "version": calendar.version,
            "exchange": calendar.exchange,
            "first_day": calendar.days[0].isoformat(),
            "last_day": calendar.days[-1].isoformat(),
            "trading_days": len(calendar.days),
            "probed_weekdays": report.probed,
            "evidence_sha256": report.evidence_sha256,
            "written_to": str(out),
        }
    )


def _resolve_claim(raw: str | None) -> AsPublishedClaim | None:
    """把 CLI 取值解析为证据声明；非法取值直接失败。"""
    try:
        return parse_claim(raw)
    except ClaimError as exc:
        _fail(str(exc))


@etf_app.command("ingest")
def etf_ingest(
    database: Annotated[Path, typer.Option("--database")],
    symbol: Annotated[str, typer.Option("--symbol", help="新浪代码，如 sh515210")],
    asset_id: Annotated[str, typer.Option("--asset-id", help="合同层 asset_id，如 ETF_515210")],
    name: Annotated[str, typer.Option("--name", help="中文名称")],
    exchange: Annotated[str, typer.Option("--exchange")] = "SSE",
    tracking_index: Annotated[
        str | None, typer.Option("--tracking-index", help="跟踪指数 asset_id（可选）")
    ] = None,
    interval: Annotated[float, typer.Option("--interval", help="请求间隔秒数")] = 1.2,
) -> None:
    """采集 ETF/指数日线并写入不可变行情仓储（不复权原始价）。"""
    market = DuckDBMarketRepository(database)
    service = EtfIngestService(market, bronze_root=database.parent / "bronze-etf")
    headers = {"User-Agent": "IndustryPulse/0.1 (research; contact=local)"}
    try:
        exchange_value = Exchange(exchange.upper())
    except ValueError:
        _fail(f"未知交易所：{exchange}")
    try:
        with httpx.Client(timeout=40, follow_redirects=True, headers=headers) as client:
            report = service.ingest_symbol(
                symbol=symbol,
                asset_id=asset_id,
                name_zh=name,
                exchange=exchange_value,
                tracking_index_id=tracking_index,
                fetch=lambda code: fetch_sina_kline(code, client=client, interval=interval),
            )
    except (EtfIngestError, MarketDataError, ValueError) as exc:
        _fail(str(exc))
    _emit(
        {
            "asset_id": report.asset_id,
            "symbol": report.symbol,
            "bars": report.bars,
            "inserted": report.inserted,
            "first_day": report.first_day,
            "last_day": report.last_day,
            "artifact_sha256": report.artifact_sha256,
        }
    )


@etf_app.command("dividends")
def etf_dividends(
    database: Annotated[Path, typer.Option("--database")],
    symbol: Annotated[str, typer.Option("--symbol", help="新浪代码，如 sh515210")],
    asset_id: Annotated[str, typer.Option("--asset-id")],
    interval: Annotated[float, typer.Option("--interval")] = 1.5,
) -> None:
    """采集基金分红送配并写入公司行动（每份分红金额）。"""
    code = symbol[2:] if symbol[:2] in ("sh", "sz") else symbol
    url = f"https://fund.eastmoney.com/f10/FHSP_{code}.html"
    headers = {"User-Agent": "IndustryPulse/0.1 (research; contact=local)"}
    try:
        time.sleep(interval)
        with httpx.Client(timeout=40, follow_redirects=True, headers=headers) as client:
            response = client.get(url)
        response.raise_for_status()
        records = parse_dividend_table(response.text)
    except (httpx.HTTPError, DividendParseError) as exc:
        _fail(f"分红采集失败：{exc}")
    if not records:
        _fail(f"{asset_id} 未解析到任何分红记录（页面结构可能变化）")
    market = DuckDBMarketRepository(database)
    actions = to_corporate_actions(
        records,
        asset_id=asset_id,
        source_note=f"来源 {url}（第三方聚合，C 级证据）",
    )
    for action in actions:
        try:
            market.register_action(action)
        except MarketDataError as exc:
            _fail(str(exc))
    _emit(
        {
            "asset_id": asset_id,
            "source": url,
            "actions": [
                {
                    "ex_date": action.ex_date.isoformat(),
                    "per_unit": str(action.distribution_per_unit),
                }
                for action in actions
            ],
        }
    )


@etf_app.command("price-lines")
def etf_price_lines(
    database: Annotated[Path, typer.Option("--database")],
    config: Annotated[Path, typer.Option("--config")] = Path(
        "config/market/industry_price_lines.yaml"
    ),
    interval: Annotated[float, typer.Option("--interval")] = 1.5,
) -> None:
    """按行业价格线配置采集全部 ETF 日线与现金分红（已登记的资产只追加新交易日）。"""
    import yaml

    document = yaml.safe_load(config.read_text(encoding="utf-8"))
    lines = {
        spec["asset_id"]: spec for spec in [document["benchmark"], *document["industries"].values()]
    }
    market = DuckDBMarketRepository(database)
    service = EtfIngestService(market, bronze_root=database.parent / "bronze-etf")
    headers = {"User-Agent": "IndustryPulse/0.1 (research; contact=local)"}
    results = []
    with httpx.Client(timeout=40, follow_redirects=True, headers=headers) as client:
        for asset_id, spec in lines.items():
            symbol = spec["symbol"]
            entry: dict[str, object] = {"asset_id": asset_id, "symbol": symbol}
            try:
                report = service.ingest_symbol(
                    symbol=symbol,
                    asset_id=asset_id,
                    name_zh=spec["name"],
                    exchange=Exchange.SSE if symbol.startswith("sh") else Exchange.SZSE,
                    fetch=lambda code: fetch_sina_kline(code, client=client, interval=interval),
                )
                entry.update(
                    bars=report.bars,
                    inserted=report.inserted,
                    first_day=report.first_day,
                    last_day=report.last_day,
                )
            except (EtfIngestError, MarketDataError, ValueError, httpx.HTTPError) as exc:
                entry["error"] = str(exc)
                results.append(entry)
                continue
            url = f"https://fund.eastmoney.com/f10/FHSP_{symbol[2:]}.html"
            try:
                time.sleep(interval)
                response = client.get(url)
                response.raise_for_status()
                records = parse_dividend_table(response.text)
                actions = to_corporate_actions(
                    records,
                    asset_id=asset_id,
                    source_note=f"来源 {url}（第三方聚合，C 级证据）",
                )
                for action in actions:
                    market.register_action(action)
                entry["dividends"] = len(actions)
            except (httpx.HTTPError, DividendParseError, MarketDataError) as exc:
                entry["dividend_error"] = str(exc)
            results.append(entry)
    _emit({"lines": results})


@etf_app.command("list")
def etf_list(database: Annotated[Path, typer.Option("--database")]) -> None:
    """列出行情仓储中的资产与 bar 数。"""
    market = DuckDBMarketRepository(database)
    _emit(
        [
            {
                "asset_id": asset.asset_id,
                "name": asset.name_zh,
                "type": asset.instrument_type.value,
                "bars": len(market.bars(asset.asset_id)),
            }
            for asset in market.assets()
        ]
    )


@app.command("backtest")
def backtest(
    database: Annotated[Path, typer.Option("--database")],
    asset_id: Annotated[str, typer.Option("--asset-id", help="被回测的 ETF")],
    series_id: Annotated[str, typer.Option("--series-id", help="驱动信号的经济指标序列")],
    snapshot_id: Annotated[str, typer.Option("--snapshot-id", help="冻结观测快照")],
    start: Annotated[str, typer.Option("--from", help="起始日 YYYY-MM-DD")],
    end: Annotated[str, typer.Option("--to", help="结束日 YYYY-MM-DD")],
    benchmark: Annotated[str | None, typer.Option("--benchmark", help="基准 asset_id")] = None,
    threshold: Annotated[float, typer.Option("--threshold", help="规则阈值")] = 0.0,
    commission_bps: Annotated[float, typer.Option("--commission-bps")] = 3.0,
    slippage_bps: Annotated[float, typer.Option("--slippage-bps")] = 5.0,
    cash_rate: Annotated[float, typer.Option("--cash-rate", help="现金年化利率")] = 0.015,
    max_weight: Annotated[float, typer.Option("--max-weight")] = 1.0,
    max_turnover: Annotated[float | None, typer.Option("--max-turnover")] = None,
    out: Annotated[Path | None, typer.Option("--out", help="可选：写出完整 JSON 报告")] = None,
) -> None:
    """执行一次基线规则回测（V-005..V-010），输出净值与成本口径。"""
    try:
        request = BacktestRequest(
            asset_id=asset_id,
            benchmark_asset_id=benchmark,
            series_id=series_id,
            snapshot_id=snapshot_id,
            start=date.fromisoformat(start),
            end=date.fromisoformat(end),
            threshold=threshold,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            cash_rate_annual=cash_rate,
            max_weight=max_weight,
            max_turnover=max_turnover,
        )
    except ValueError as exc:
        _fail(str(exc))
    market = DuckDBMarketRepository(database)
    observations = DuckDBObservationRepository(database)
    try:
        result, metadata = run_backtest(market, observations, request)
    except (ValueError, PublicationError, MarketDataError) as exc:
        _fail(str(exc))
    m = result.metrics
    _emit(
        {
            "experiment": metadata,
            "metrics": m.model_dump(),
            "benchmark_metrics": result.benchmark_metrics.model_dump()
            if result.benchmark_metrics
            else None,
            "final_equity": str(result.final_equity),
            "trades": len(result.trades),
            "warnings": result.warnings,
            "written_to": None,
        }
    )
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        typer.echo(f"完整报告已写入 {out}")


@app.command("serve")
def serve(
    database: Annotated[Path, typer.Option("--database")],
    taxonomy: Annotated[Path, typer.Option("--taxonomy")] = Path("config/industries/steel-g2.yaml"),
    snapshot_id: Annotated[str, typer.Option("--snapshot-id")] = "",
    report_dir: Annotated[Path, typer.Option("--report-dir")] = Path("artifacts/reports"),
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8000,
) -> None:
    """以真实数据启动 API 服务（15 条冻结路由，能接的接真服务）。"""
    import uvicorn

    from industrypulse.api import create_app

    observations = DuckDBObservationRepository(database)
    try:
        registry = TaxonomyRegistry.load(taxonomy)
    except Exception as exc:  # noqa: BLE001
        _fail(f"taxonomy 加载失败：{exc}")
    resolved_snapshot = snapshot_id
    if not resolved_snapshot:
        with observations.database.transaction() as conn:
            row = conn.execute(
                "SELECT snapshot_id FROM publication ORDER BY snapshot_id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            _fail("数据库中没有已发布快照；先运行 backfill")
        resolved_snapshot = str(row[0])
    handlers = build_handlers(
        observations=observations,
        taxonomy=registry,
        snapshot_id=resolved_snapshot,
        report_dir=report_dir,
    )
    typer.echo(f"snapshot={resolved_snapshot} 已绑定 {len(handlers)} 条路由，监听 {host}:{port}")
    uvicorn.run(create_app(handlers), host=host, port=port)


@app.command("doctor")
def doctor() -> None:
    """打印环境、依赖与契约版本自检信息。"""
    _emit({"contracts_version": CONTRACTS_VERSION, **Settings.from_env().diagnostics()})


@source_app.command("list")
def source_list(
    catalog: Annotated[
        Path, typer.Option("--catalog", help="source_catalog.csv 路径")
    ] = DEFAULT_CATALOG,
) -> None:
    """列出目录中的候选来源（许可默认 pending_review，不代表已获采集授权）。"""
    if not catalog.is_file():
        _fail(f"来源目录不存在：{catalog}")
    registry = SourceRegistry.from_csv(catalog)
    _emit(
        [
            {
                "source_id": source.source_id,
                "priority": source.priority.value,
                "frequency": source.frequency.value,
                "legal_status": source.legal_status.value,
                "status": source.status.value,
                "url": source.url,
            }
            for source in sorted(registry.sources.values(), key=lambda item: item.source_id)
        ]
    )


@source_app.command("permit")
def source_permit(
    catalog_source_id: Annotated[str, typer.Argument(help="目录中的来源 ID，如 SRC-NBS-PROFIT")],
    contract_source_id: Annotated[
        str,
        typer.Option("--as", help="合同层 source_id，如 cn.nbs（SeriesSpec.source_id 必须一致）"),
    ],
    database: Annotated[Path, typer.Option("--database", help="DuckDB 文件路径")],
    reason: Annotated[str, typer.Option("--reason", help="许可核验依据（写入追加式审计日志）")],
    actor: Annotated[str, typer.Option("--actor", help="操作人")],
    legal_status: Annotated[
        LegalStatus, typer.Option("--legal-status", help="完成核验后的许可状态")
    ] = LegalStatus.PUBLIC_OFFICIAL,
    catalog: Annotated[Path, typer.Option("--catalog")] = DEFAULT_CATALOG,
    audit: Annotated[Path, typer.Option("--audit")] = DEFAULT_AUDIT,
) -> None:
    """把一条目录来源的许可核验结果落到仓储。

    目录 ID（SRC-NBS-PROFIT）与合同 source_id（cn.nbs）属于两个命名空间，
    这里要求显式桥接并把映射写进审计，而不是靠隐式重命名。
    """
    if not catalog.is_file():
        _fail(f"来源目录不存在：{catalog}")
    registry = SourceRegistry.from_csv(catalog)
    registry.audit_path = audit
    key = catalog_source_id.strip().lower().replace("-", ".")
    if key not in registry.sources:
        _fail(f"目录中不存在来源 {catalog_source_id}（归一化后为 {key}）")
    try:
        registry.set_permission(key, legal_status, reason=reason, actor=actor)
        descriptor = registry.sources[key]
        contract_source = SourceDescriptor.model_validate(
            {**descriptor.model_dump(), "source_id": contract_source_id}
        )
    except (PermissionError, ValueError, KeyError) as exc:
        _fail(str(exc))
    repository = DuckDBObservationRepository(database)
    try:
        repository.register_source(contract_source)
    except PublicationError as exc:
        _fail(str(exc))
    _emit(
        {
            "catalog_source_id": catalog_source_id,
            "contract_source_id": contract_source_id,
            "legal_status": legal_status.value,
            "audit_path": str(audit),
            "audit_event": asdict(registry.events[-1]) if registry.events else None,
        }
    )


@app.command("init")
def init(
    database: Annotated[Path, typer.Option("--database", help="DuckDB 文件路径")],
    taxonomy: Annotated[Path, typer.Option("--taxonomy", help="taxonomy 配置路径")] = (
        DEFAULT_TAXONOMY
    ),
) -> None:
    """登记 taxonomy：行业节点、指标与序列规格（要求来源已 permit）。"""
    if not taxonomy.is_file():
        _fail(f"taxonomy 不存在：{taxonomy}")
    try:
        registry = TaxonomyRegistry.load(taxonomy)
    except Exception as exc:  # noqa: BLE001 - 配置错误需原样反馈给操作者
        _fail(f"taxonomy 校验失败：{exc}")
    repository = DuckDBObservationRepository(database)
    registered: list[str] = []
    for series in registry.series:
        try:
            repository.register_series(series)
        except PublicationError as exc:
            _fail(f"{series.series_id}: {exc}（先运行 source permit 登记 {series.source_id}）")
        registered.append(series.series_id)
    repository.register_taxonomy(registry.nodes, registry.indicators)
    _emit(
        {
            "taxonomy_version": registry.version,
            "synthetic": registry.synthetic,
            "series": registered,
            "industries": [node.industry_id for node in registry.nodes],
            "indicators": [spec.indicator_id for spec in registry.indicators],
        }
    )


@calendar_app.command("validate")
def calendar_validate(
    path: Annotated[Path, typer.Argument(help="交易日历文件（JSON，或带 date 列的 CSV）")],
    exchange: Annotated[str, typer.Option("--exchange")] = "SSE",
    max_gap_days: Annotated[int, typer.Option("--max-gap-days")] = DEFAULT_MAX_GAP_DAYS,
    out: Annotated[
        Path | None, typer.Option("--out", help="可选：写出规范化 JSON 供回测引用")
    ] = None,
) -> None:
    """校验交易日历结构并记录文件指纹；结构可疑时拒绝装载。"""
    try:
        calendar = load_trading_calendar(path, exchange=exchange, max_gap_days=max_gap_days)
    except CalendarError as exc:
        _fail(str(exc))
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(calendar.model_dump_json(indent=2) + "\n", encoding="utf-8")
    _emit(
        {
            "version": calendar.version,
            "exchange": calendar.exchange,
            "source_ref": calendar.source_ref,
            "source_sha256": calendar.source_sha256,
            "first_day": calendar.days[0].isoformat(),
            "last_day": calendar.days[-1].isoformat(),
            "trading_days": len(calendar.days),
            "written_to": str(out) if out is not None else None,
        }
    )


def _run_parser(
    name: str, html: str, period: str, params: dict[str, Any]
) -> list[ParsedObservation]:
    """把 CLI 的 --parser/--params 映射到 S 线确定性解析器；不猜测缺失参数。"""

    def required(key: str) -> Any:
        if key not in params:
            raise IngestError(f"parser={name} 缺少参数 {key!r}")
        return params[key]

    table_index = params.get("table_index")
    if name == "profit":
        return [
            parse_profit(
                html,
                period=period,
                row=required("row"),
                column=required("column"),
                unit=params.get("unit", "亿元"),
                table_index=table_index,
            )
        ]
    if name == "iva":
        return [
            parse_iva(
                html,
                period=period,
                row=required("row"),
                column=required("column"),
                kind=required("kind"),
                unit=required("unit"),
                table_index=table_index,
            )
        ]
    if name == "profit-table":
        # 一份公告同时给出 营业收入/营业成本/利润总额 的金额与同比
        index = required("table_index")
        results: list[ParsedObservation] = []
        for metric in ("营业收入", "营业成本", "利润总额"):
            for sub in ("金额", "同比增长"):
                column = resolve_table_column(html, table_index=index, metric=metric, sub=sub)
                results.append(
                    parse_profit(
                        html,
                        period=period,
                        row=required("row"),
                        column=column.label,
                        unit=column.unit,
                        table_index=index,
                        metric=metric,
                    )
                )
        return results
    if name == "shfe-json":
        trading_day = date.fromisoformat(str(required("trading_date")))
        if period != f"{trading_day.year:04d}-{trading_day.month:02d}":
            raise IngestError("--period 必须与 trading_date 同年月")
        return [
            parse_shfe_daily(html, trading_date=trading_day, contract=str(required("contract")))
        ]
    if name == "pmi-table":
        index = required("table_index")
        month_label = required("month_label")
        return [
            parse_pmi_matrix(
                html,
                period=period,
                month_label=month_label,
                indicator=indicator,
                table_index=index,
            )
            for indicator in PMI_MATRIX_COLUMNS
        ]
    if name == "pmi":
        return [
            parse_pmi(
                html,
                period=period,
                row=required("row"),
                column=required("column"),
                category=required("category"),
                table_index=required("table_index"),
            )
        ]
    if name == "tsf":
        return [
            parse_tsf(
                html,
                period=period,
                row=required("row"),
                column=required("column"),
                is_ytd=bool(required("is_ytd")),
                population=required("population"),
                unit=required("unit"),
                table_index=table_index,
                kind=params.get("kind", "flow"),
            )
        ]
    raise IngestError(f"未知 parser：{name}（可选 profit/iva/pmi/tsf）")


@app.command("ingest")
def ingest(
    database: Annotated[Path, typer.Option("--database")],
    source_id: Annotated[str, typer.Option("--source-id", help="合同层 source_id，须已 permit")],
    file: Annotated[Path, typer.Option("--file", help="官方文档本地副本（HTML）")],
    period: Annotated[str, typer.Option("--period", help="所属期 YYYY-MM")],
    source_url: Annotated[str, typer.Option("--source-url", help="原件原始出处 URL（https）")],
    parser: Annotated[str, typer.Option("--parser", help="profit / iva / pmi / tsf")],
    params: Annotated[str, typer.Option("--params", help="解析器参数的 JSON 对象")] = "{}",
    params_file: Annotated[
        Path | None,
        typer.Option("--params-file", help="解析器参数 JSON 文件（Windows 下比 --params 更可靠）"),
    ] = None,
    parser_version: Annotated[str, typer.Option("--parser-version")] = "g2-v1",
    as_published: Annotated[
        str | None,
        typer.Option(
            "--as-published",
            help="同期原件证据：auto（用抓取时点）或 capture:<ISO8601>；缺省则数值证据只能为 C",
        ),
    ] = None,
    trusted_copy: Annotated[bool, typer.Option("--trusted-copy")] = False,
    snapshot_id: Annotated[
        str | None, typer.Option("--snapshot-id", help="缺省按内容寻址生成")
    ] = None,
) -> None:
    """摄取一份官方文档：归档原件 -> 构造观测 -> 发布快照。"""
    if not file.is_file():
        _fail(f"文档不存在：{file}")
    if params_file is not None:
        if params != "{}":
            _fail("--params 与 --params-file 只能二选一")
        if not params_file.is_file():
            _fail(f"参数文件不存在：{params_file}")
        # Windows 记事本/PS 5.1 写出的 JSON 常带 BOM，必须容忍
        source_text = params_file.read_text(encoding="utf-8-sig")
        origin = "--params-file"
    else:
        source_text = params
        origin = "--params"
    try:
        payload: Any = json.loads(source_text)
    except json.JSONDecodeError as exc:
        _fail(f"{origin} 不是合法 JSON：{exc}")
    if not isinstance(payload, dict):
        _fail("--params 必须是 JSON 对象")
    content = file.read_bytes()
    try:
        html = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail(f"文档不是 UTF-8 HTML：{exc}")
    claim = _resolve_claim(as_published)
    repository = DuckDBObservationRepository(database)
    try:
        parsed = _run_parser(parser, html, period, payload)
        specs: dict[str, SeriesSpec] = {}
        for item in parsed:
            try:
                specs[item.series_id] = repository.series_spec(item.series_id)
            except PublicationError:
                _fail(
                    "未登记 SeriesSpec："
                    + item.series_id
                    + "（先运行 industrypulse source permit 与 init）"
                )
        digest = hashlib.sha256(content).hexdigest()
        artifact = RawArtifact(
            artifact_id=digest,
            source_id=source_id,
            uri=str(file.resolve()),
            sha256=digest,
            mime="text/html",
            byte_size=len(content),
            retrieved_at=datetime.now(UTC),
            source_url=source_url,
        )
        report = IngestService(repository, parser_version=parser_version).ingest_document(
            artifact=artifact,
            content=content,
            parsed=parsed,
            specs=specs,
            snapshot_id=snapshot_id,
            as_published=claim,
            trusted_copy=trusted_copy,
        )
    except (IngestError, PublicationError, ValueError) as exc:
        _fail(str(exc))
    _emit(report.model_dump(mode="json"))


@app.command("compare-modes")
def compare_modes(
    database: Annotated[Path, typer.Option("--database")],
    snapshot_id: Annotated[str, typer.Option("--snapshot-id", help="冻结快照 ID")],
    series: Annotated[list[str], typer.Option("--series", help="series_id，可重复")],
    as_of: Annotated[list[str], typer.Option("--as-of", help="ISO 时间（含时区），可重复")],
    mode: Annotated[
        list[str] | None, typer.Option("--mode", help="strict_pit/reconstructed_pit/exploratory")
    ] = None,
    out: Annotated[Path | None, typer.Option("--out", help="可选：写出 JSON 报告")] = None,
    calendar_path: Annotated[
        Path | None,
        typer.Option("--calendar", help="交易日历 JSON；用于 B 级发布的次一交易日规则"),
    ] = None,
) -> None:
    """三种研究模式的 as-of 对照表（§20.5）：含行级可用性原因。"""
    try:
        points = [datetime.fromisoformat(value) for value in as_of]
    except ValueError as exc:
        _fail(f"--as-of 不是合法 ISO 时间：{exc}")
    if any(point.tzinfo is None for point in points):
        _fail("--as-of 必须带时区，例如 2025-08-27T09:30:00+08:00")
    try:
        modes = (
            [ResearchMode(value) for value in mode]
            if mode
            else [ResearchMode.STRICT_PIT, ResearchMode.RECONSTRUCTED_PIT]
        )
    except ValueError as exc:
        _fail(f"--mode 非法：{exc}")
    trading_days: tuple[date, ...] = ()
    if calendar_path is not None:
        try:
            trading_days = load_trading_calendar(calendar_path).as_tuple()
        except CalendarError as exc:
            _fail(f"交易日历不可用：{exc}")
    repository = DuckDBObservationRepository(database, trading_days=trading_days)
    try:
        comparison = build_pit_comparison(
            repository, series, points, modes, snapshot_id=snapshot_id
        )
    except (ValueError, PublicationError) as exc:
        _fail(str(exc))
    typer.echo(format_table(comparison))
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(comparison.model_dump_json(indent=2) + "\n", encoding="utf-8")
        typer.echo(f"\nJSON 报告已写入 {out}")


def _batch_items_from_manifest(
    repository: DuckDBObservationRepository, manifest: Path, retrieved_at: datetime
) -> list[BatchItem]:
    """把回填清单解析为 BatchItem 列表；任何结构问题都给出定位到序号的错误。"""
    if not manifest.is_file():
        _fail(f"清单不存在：{manifest}")
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        _fail(f"{manifest} 不是合法 JSON：{exc}")
    entries = raw.get("documents") if isinstance(raw, dict) else raw
    if not isinstance(entries, list) or not entries:
        _fail(f"{manifest} 必须是非空 JSON 数组（或含 documents 数组的对象）")
    items: list[BatchItem] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            _fail(f"{manifest} 第 {index} 项不是对象")
        missing = [
            key for key in ("source_id", "period", "file", "url", "parser") if key not in entry
        ]
        if missing:
            _fail(f"{manifest} 第 {index} 项缺少字段：{', '.join(missing)}")
        document = Path(entry["file"])
        if not document.is_file():
            _fail(f"{manifest} 第 {index} 项文件不存在：{document}")
        if "params_file" in entry:
            params_source = Path(entry["params_file"])
            if not params_source.is_file():
                _fail(f"{manifest} 第 {index} 项参数文件不存在：{params_source}")
            params_text = params_source.read_text(encoding="utf-8-sig")
        else:
            params_text = json.dumps(entry.get("params", {}), ensure_ascii=False)
        try:
            params_object: Any = json.loads(params_text)
        except json.JSONDecodeError as exc:
            _fail(f"{manifest} 第 {index} 项参数不是合法 JSON：{exc}")
        if not isinstance(params_object, dict):
            _fail(f"{manifest} 第 {index} 项参数必须是对象")
        content = document.read_bytes()
        try:
            html = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            _fail(f"{manifest} 第 {index} 项不是 UTF-8 文本：{exc}")
        try:
            parsed = _run_parser(entry["parser"], html, entry["period"], params_object)
        except (IngestError, ValueError) as exc:
            _fail(f"{manifest} 第 {index} 项解析失败：{exc}")
        specs: dict[str, SeriesSpec] = {}
        for item in parsed:
            try:
                specs[item.series_id] = repository.series_spec(item.series_id)
            except PublicationError:
                _fail(
                    "未登记 SeriesSpec："
                    + item.series_id
                    + "（先运行 industrypulse source permit 与 init）"
                )
        digest = hashlib.sha256(content).hexdigest()
        items.append(
            BatchItem(
                artifact=RawArtifact(
                    artifact_id=digest,
                    source_id=entry["source_id"],
                    uri=str(document.resolve()),
                    sha256=digest,
                    mime="text/html",
                    byte_size=len(content),
                    retrieved_at=retrieved_at,
                    source_url=entry["url"],
                ),
                content=content,
                parsed=parsed,
                specs=specs,
            )
        )
    return items


@app.command("backfill")
def backfill(
    database: Annotated[Path, typer.Option("--database")],
    manifest: Annotated[Path, typer.Option("--manifest", help="回填清单 JSON")],
    snapshot_id: Annotated[str, typer.Option("--snapshot-id", help="冻结快照 ID")],
    parser_version: Annotated[str, typer.Option("--parser-version")] = "g2-v1",
    as_published: Annotated[
        str | None,
        typer.Option("--as-published", help="同期原件证据：auto 或 capture:<ISO8601>"),
    ] = None,
    trusted_copy: Annotated[bool, typer.Option("--trusted-copy")] = False,
) -> None:
    """离线批量回填多份公告到同一个冻结快照（O-006）。

    清单格式（JSON 数组）：:

        [{"source_id": "cn.nbs", "period": "2025-07",
          "file": "data/incoming/2025-07-profit.html",
          "url": "https://www.stats.gov.cn/...",
          "parser": "profit",
          "params_file": "data/incoming/params-2025-07.json"}]
    """
    claim = _resolve_claim(as_published)
    repository = DuckDBObservationRepository(database)
    items = _batch_items_from_manifest(repository, manifest, datetime.now(UTC))
    try:
        report = IngestService(repository, parser_version=parser_version).ingest_batch(
            items, snapshot_id=snapshot_id, as_published=claim, trusted_copy=trusted_copy
        )
    except (IngestError, PublicationError, ValueError) as exc:
        _fail(str(exc))
    _emit(report.model_dump(mode="json"))


@app.command("backfill-monthly")
def backfill_monthly(
    database: Annotated[Path, typer.Option("--database")],
    source_id: Annotated[str, typer.Option("--source-id", help="合同层 source_id，须已 permit")],
    start: Annotated[str, typer.Option("--from", help="起始所属期 YYYY-MM")],
    end: Annotated[str, typer.Option("--to", help="结束所属期 YYYY-MM")],
    snapshot_id: Annotated[str, typer.Option("--snapshot-id", help="冻结快照 ID")],
    kinds: Annotated[
        str, typer.Option("--kinds", help="逗号分隔：profit,pmi,iva")
    ] = "profit,pmi,iva",
    workdir: Annotated[Path, typer.Option("--workdir", help="抓取原件的内容寻址目录")] = Path(
        "data/incoming/raw"
    ),
    max_pages: Annotated[int, typer.Option("--max-pages", help="列表页翻页上限")] = 30,
    interval: Annotated[float, typer.Option("--interval", help="请求间隔秒数")] = 1.0,
    parser_version: Annotated[str, typer.Option("--parser-version")] = "g2-v1",
    as_published: Annotated[
        str | None, typer.Option("--as-published", help="同期原件证据：auto 或 capture:<ISO8601>")
    ] = None,
    include_manifest: Annotated[
        Path | None,
        typer.Option("--include-manifest", help="可选：把其它来源的文档并入同一快照"),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="只发现并列出公告，不抓取正文")
    ] = False,
) -> None:
    """按月回填：自动发现官方公告 -> 抓取 -> 解析 -> 单一冻结快照（O-006）。"""
    selected = [item.strip() for item in kinds.split(",") if item.strip()]
    if not selected:
        _fail("--kinds 不能为空")
    claim = _resolve_claim(as_published)
    repository = DuckDBObservationRepository(database)
    try:
        descriptor = repository.source_descriptor(source_id)
    except PublicationError as exc:
        _fail(f"{exc}（先运行 source permit）")
    registry = SourceRegistry([descriptor])
    workdir.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "IndustryPulse/0.1 (research; contact=local)"}
    with httpx.Client(timeout=60, follow_redirects=False, headers=headers) as client:
        fetcher = Fetcher(registry, workdir, client, retries=2, interval=interval)
        backfill = MonthlyBackfill(repository, fetcher, max_pages=max_pages)
        try:
            announcements = backfill.discover(
                source_id=source_id, kinds=selected, start=start, end=end
            )
        except (ValueError, PublicationError) as exc:
            _fail(f"公告发现失败：{exc}")
        typer.echo(f"发现公告 {len(announcements)} 份")
        for announcement in announcements:
            typer.echo(f"  {announcement.kind:<7}{announcement.period}  {announcement.title[:44]}")
        if dry_run:
            return
        try:
            extra = (
                _batch_items_from_manifest(repository, include_manifest, datetime.now(UTC))
                if include_manifest is not None
                else []
            )
            outcome = backfill.run(
                announcements,
                source_id=source_id,
                snapshot_id=snapshot_id,
                parser_version=parser_version,
                as_published=claim,
                extra=extra,
            )
        except (IngestError, PublicationError, ValueError) as exc:
            _fail(str(exc))
    typer.echo("")
    _emit(
        {
            "snapshot_id": outcome.report.snapshot_id,
            "created": outcome.report.created,
            "reused": outcome.report.reused,
            "documents_ingested": outcome.discovered - len(outcome.skipped),
            "documents_skipped": len(outcome.skipped),
            "vintages": len(outcome.report.vintages),
            "skipped": list(outcome.skipped),
        }
    )


@app.command("workbench-run")
def workbench_run(
    action: Annotated[
        str, typer.Argument(help="forecast / experiment / backtest / backup / expand / holdout")
    ],
    options_file: Annotated[Path | None, typer.Option("--options-file")] = None,
) -> None:
    """运行真实工作台任务；参数可从 JSON 文件读取。"""
    from industrypulse.services.industry_expand import expand_industries
    from industrypulse.services.workbench import Workbench

    service = Workbench()
    options = json.loads(options_file.read_text(encoding="utf-8-sig")) if options_file else {}
    options.setdefault("as_of", datetime.now().astimezone().isoformat())
    actions: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
        "forecast": service.forecast,
        "experiment": service.experiment,
        "backtest": service.backtest,
        "backup": service.backup,
        "holdout": service.final_holdout,
        "expand": lambda payload: expand_industries(service, payload),
    }
    if action not in actions:
        _fail("未知任务类型")
    try:
        _emit(actions[action](options))
    except (ValueError, KeyError) as exc:
        _fail(str(exc))


def _growth_series_map(service: Any, only: list[str] | None = None) -> dict[str, dict[str, str]]:
    """行业名 -> {profit, revenue} 序列映射；与 economic_state 的取数口径一致。"""
    wanted: dict[str, dict[str, str]] = {}
    for path in sorted((service.root / "config/industries").glob("*.yaml")):
        registry = service.taxonomy(path.stem)
        if registry.synthetic:
            continue
        profit_id = next(
            (
                item.series_id
                for item in registry.series
                if item.series_id.endswith("profit_ytd_yoy")
            ),
            "",
        )
        if not profit_id:
            continue
        if only and path.stem not in only:
            continue
        wanted[path.stem] = {
            "profit": profit_id,
            "revenue": profit_id.replace("profit_ytd_yoy", "revenue_ytd_yoy"),
        }
    return wanted


@app.command("replay-signals")
def replay_signals(
    as_of: Annotated[str, typer.Option("--as-of", help="历史决策日（ISO 时间，含时区）")] = "",
    mode: Annotated[
        str, typer.Option("--mode", help="strict_pit / reconstructed_pit / exploratory")
    ] = "reconstructed_pit",
    snapshot_id: Annotated[
        str, typer.Option("--snapshot-id", help="冻结快照 ID；缺省取最大快照")
    ] = "",
    industry: Annotated[
        list[str] | None, typer.Option("--industry", help="只重放这些行业，可重复")
    ] = None,
    out: Annotated[Path | None, typer.Option("--out", help="可选：写出 JSON 报告")] = None,
) -> None:
    """利润/收入/旧期值的联合可见时间，并重放历史决策日上的候选（阶段 1 基线）。"""
    from industrypulse.services.signal_replay import format_report, run_signal_replay
    from industrypulse.services.workbench import Workbench

    try:
        moment = datetime.fromisoformat(as_of) if as_of else datetime.now().astimezone()
    except ValueError as exc:
        _fail(f"--as-of 不是合法 ISO 时间：{exc}")
    if moment.tzinfo is None:
        _fail("--as-of 必须带时区，例如 2026-09-27T10:00:00+08:00")
    try:
        research_mode = ResearchMode(mode)
    except ValueError as exc:
        _fail(f"--mode 非法：{exc}")
    service = Workbench()
    repository = service.repository()
    try:
        snapshot = service.snapshot(repository, snapshot_id)
    except ValueError as exc:
        _fail(str(exc))
    industries = _growth_series_map(service, list(industry) if industry else None)
    if not industries:
        _fail("没有找到带利润/收入序列的行业")
    payload = run_signal_replay(
        repository,
        snapshot_id=snapshot,
        industries=industries,
        as_of=moment,
        mode=research_mode,
    )
    typer.echo(format_report(payload))
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )
        typer.echo(f"\nJSON 报告已写入 {out}")


@app.command("etf-audit")
def etf_audit(
    as_of: Annotated[
        str, typer.Option("--as-of", help="回放时点（ISO 日期或时间）；缺省为今天")
    ] = "",
    threshold: Annotated[
        float, typer.Option("--threshold", help="疑似拆并的缺口阈值（小数）")
    ] = 0.20,
    out: Annotated[Path | None, typer.Option("--out", help="可选：写出 JSON 报告")] = None,
) -> None:
    """核对 ETF 分红记录、疑似份额拆并与代理范围（阶段 1 基线）。"""
    from industrypulse.services.etf_audit import audit_price_lines, format_audit
    from industrypulse.services.workbench import Workbench

    try:
        moment = datetime.fromisoformat(as_of) if as_of else datetime.now().astimezone()
    except ValueError as exc:
        _fail(f"--as-of 不是合法日期：{exc}")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    service = Workbench()
    repository = service.repository()
    config_path = service.root / "config/market/industry_price_lines.yaml"
    if not config_path.exists():
        _fail(f"没有找到 {config_path}")
    market = DuckDBMarketRepository(repository.database)
    payload = audit_price_lines(config_path, market, as_of=moment, threshold=threshold)
    typer.echo(format_audit(payload))
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )
        typer.echo(f"\nJSON 报告已写入 {out}")


def _leading_stores() -> tuple[Any, Any, str | None]:
    """扫描/核实共用的仓储：研究库 + 市场库 + 当前最大快照。"""
    from industrypulse.services.workbench import Workbench

    service = Workbench()
    repository = service.repository()
    try:
        snapshot = service.snapshot(repository)
    except ValueError:
        snapshot = None
    market = DuckDBMarketRepository(repository.database)
    return repository, market, snapshot


@app.command("leading-verify")
def leading_verify(
    out: Annotated[Path | None, typer.Option("--out", help="可选：写出 JSON 报告")] = None,
) -> None:
    """逐候选核实领先指标：发布时间、许可、历史深度、采集能力（阶段 2）。"""
    from industrypulse.services.leading import format_verify, verify

    repository, market, snapshot = _leading_stores()
    payload = verify(repository, market, snapshot_id=snapshot)
    typer.echo(format_verify(payload))
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )
        typer.echo(f"\nJSON 报告已写入 {out}")


@app.command("leading-scan")
def leading_scan(
    out: Annotated[Path | None, typer.Option("--out", help="可选：写出 JSON 报告")] = None,
) -> None:
    """采集领先候选 → 变化检测 → 追加事件账本（什么变了/相对哪期/影响谁/下次怎么验证）。"""
    from industrypulse.services.leading import format_scan, scan

    repository, market, snapshot = _leading_stores()
    payload = scan(repository, market, snapshot_id=snapshot)
    typer.echo(format_scan(payload))
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )
        typer.echo(f"\nJSON 报告已写入 {out}")


@app.command("ai-hypothesize")
def ai_hypothesize(
    max_events: Annotated[int, typer.Option("--max-events", help="本次最多处理几个排队事件")] = 2,
    out: Annotated[Path | None, typer.Option("--out", help="可选：写出 JSON 报告")] = None,
) -> None:
    """对 AI 门槛通过的领先事件做两步假设研究：先立假设，再独立找反证（阶段 3）。"""
    from industrypulse.ai_research.model_gateway import (
        complete as model_complete,
    )
    from industrypulse.ai_research.model_gateway import (
        load_connection,
        public_connection,
    )
    from industrypulse.contracts.context import ResearchMode
    from industrypulse.services.economic_state import build_economic_state
    from industrypulse.services.hypothesis_research import (
        format_run,
        run_hypothesis_research,
    )
    from industrypulse.services.workbench import Workbench

    connection = load_connection()
    if connection is None or not connection.enabled:
        _fail("尚未配置可用模型：先在设置页保存并启用。行情与统计不依赖模型，不受影响。")
    model_id = str(public_connection(connection).get("model") or "")
    service = Workbench()
    state = build_economic_state(
        service, ResearchMode.RECONSTRUCTED_PIT, datetime.now().astimezone()
    )
    payload = run_hypothesis_research(
        model_complete,
        connection,
        state,
        model_id=model_id,
        max_events=max_events,
    )
    typer.echo(format_run(payload))
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )
        typer.echo(f"\nJSON 报告已写入 {out}")


@app.command("model-baseline")
def model_baseline(
    industry: Annotated[
        list[str] | None,
        typer.Option("--industry", help="只跑这些行业（config/industries 文件名），可重复"),
    ] = None,
    train_end: Annotated[
        str, typer.Option("--train-end", help="训练截止（ISO 日期）")
    ] = "2025-06-30",
    test_end: Annotated[
        str, typer.Option("--test-end", help="外折测试截止（ISO 日期）")
    ] = "2026-06-30",
    out: Annotated[Path | None, typer.Option("--out", help="可选：写出 JSON 验收报告")] = None,
) -> None:
    """三个冻结目标分开跑基线模型，只在未见时段比较（阶段 4）。"""
    import re as _re

    import yaml as _yaml

    from industrypulse.contracts import TZ_SHANGHAI
    from industrypulse.forecast.registry import create_model
    from industrypulse.repositories import DuckDBMarketRepository
    from industrypulse.research.validation import run_experiment
    from industrypulse.services.model_targets import (
        advanced_gate,
        attach_market_features,
        build_relative_return_frames,
        experiment_spec_for,
        format_baseline_report,
        summarize_report,
    )
    from industrypulse.services.research_run import build_frames
    from industrypulse.services.workbench import Workbench

    try:
        cutoff_train = date.fromisoformat(train_end)
        cutoff_test = date.fromisoformat(test_end)
    except ValueError as exc:
        _fail(f"日期不合法：{exc}")
    service = Workbench()
    repository = service.repository()
    snapshot = service.snapshot(repository)
    market = DuckDBMarketRepository(repository.database)
    mapping = _yaml.safe_load(
        (service.root / "config/market/industry_price_lines.yaml").read_text(encoding="utf-8")
    )
    price_map = mapping.get("industries") or {}
    ran_at = datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")
    summaries: list[dict[str, Any]] = []
    targets_meta = [
        ("T1", "生产方向（工业增加值同比的方向）", "领先 3 个月"),
        ("T2", "盈利改善（利润累计同比的方向）", "领先 1 个公布期"),
        ("T3", "行业资产相对收益（63 个交易日）", "63 个交易日"),
    ]
    only = set(industry or [])
    for path in sorted((service.root / "config/industries").glob("*.yaml")):
        if only and path.stem not in only:
            continue
        registry = service.taxonomy(path.stem)
        if registry.synthetic:
            continue
        industry_id = registry.nodes[0].industry_id
        profit_id = next(
            (s.series_id for s in registry.series if s.series_id.endswith("profit_ytd_yoy")), ""
        )
        if not profit_id:
            continue
        revenue_id = profit_id.replace("profit_ytd_yoy", "revenue_ytd_yoy")
        cost_id = profit_id.replace("profit_ytd_yoy", "cost_ytd_yoy")
        iva_id = next(
            (
                s.series_id
                for s in registry.series
                if _re.fullmatch(r"cn\.nbs\.industry\.[a-z]\d+", s.series_id)
            ),
            "",
        )
        price_spec = price_map.get(industry_id) or {}
        asset_id = str(price_spec.get("asset_id") or "")
        run_dir = service.root / "artifacts/reports/experiments" / (
            "baseline-" + ran_at.replace(":", "").replace("-", "")
        )
        for target_id, question, horizon in targets_meta:
            entry: dict[str, Any] = {
                "industry": path.stem,
                "industry_id": industry_id,
                "target": target_id,
                "name": question,
            }
            try:
                if target_id == "T1":
                    if not iva_id:
                        raise ValueError("本行业没有工业增加值序列（只有 C31 官方单列）")
                    built = build_frames(
                        repository,
                        snapshot_id=snapshot,
                        industry_id=industry_id,
                        feature_series=[iva_id],
                        label_series=iva_id,
                        start=date(2021, 12, 1),
                        end=cutoff_test,
                        horizon_months=3,
                    )
                    feature_list = [iva_id]
                elif target_id == "T2":
                    built = build_frames(
                        repository,
                        snapshot_id=snapshot,
                        industry_id=industry_id,
                        feature_series=[profit_id, revenue_id, cost_id],
                        label_series=profit_id,
                        start=date(2021, 12, 1),
                        end=cutoff_test,
                        horizon_months=1,
                    )
                    if asset_id:
                        built = attach_market_features(built, market, asset_id)
                    feature_list = [profit_id, revenue_id, cost_id]
                    if asset_id:
                        feature_list += ["etf_rel_ret_20d", "etf_rel_ret_60d", "etf_vol_20d"]
                else:
                    if not asset_id:
                        raise ValueError("没有对口的场内 ETF 价格线")
                    built = build_relative_return_frames(
                        repository,
                        market,
                        snapshot_id=snapshot,
                        industry_id=industry_id,
                        asset_id=asset_id,
                        profit_series=profit_id,
                        revenue_series=revenue_id,
                        start=date(2021, 12, 1),
                        end=cutoff_test,
                    )
                    feature_list = [
                        "etf_rel_ret_20d",
                        "etf_rel_ret_60d",
                        "etf_vol_20d",
                        "profit_yoy",
                        "revenue_yoy",
                    ]
                spec = experiment_spec_for(
                    built,
                    experiment_id=f"{target_id}-{path.stem}",
                    feature_list=feature_list,
                    train_end=cutoff_train,
                    outer_start=cutoff_train + timedelta(days=1),
                    outer_end=cutoff_test,
                )
                report = run_experiment(
                    built.features, built.targets, spec, run_dir, model_factory=create_model
                )
                gate = advanced_gate(built)
                entry = summarize_report(target_id, question, horizon, report, gate)
                entry["industry"] = path.stem
                entry["industry_id"] = industry_id
                entry["name"] = question
            except (ValueError, RuntimeError) as exc:
                entry = {
                    **entry,
                    "status": "failed",
                    "reason": str(exc)[:200],
                    "question": question,
                    "horizon": horizon,
                    "advanced_gate": {
                        "eligible": False,
                        "scorable_rows": 0,
                        "class_one": 0,
                        "class_zero": 0,
                        "threshold": "样本≥60 且每类≥10",
                    },
                    "probability_meaning": "未训练：先解决数据覆盖，再谈模型。",
                }
            summaries.append(entry)
    payload = {
        "ran_at": ran_at,
        "snapshot_id": snapshot,
        "feature_note": (
            "基本面特征取决策日 as-of 可见版本；价格特征只用决策日已收盘 K 线"
            "（相对强弱 20/60 日、20 日波动，分红加回口径）。"
            "没有估值历史与一致预期数据，不设估值特征，不声称市场预期差；"
            "资金流没有历史深度，暂不能作特征。"
        ),
        "targets": summaries,
    }
    typer.echo(format_baseline_report(payload))
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )
        typer.echo(f"\nJSON 报告已写入 {out}")


@app.command("market-sync")
def market_sync(
    macro: bool = typer.Option(
        False, "--macro", help="同步宏观指标（东财数据中心 + 中国货币网）"
    ),
    history: bool = typer.Option(False, "--history", help="回填行业指数/ETF/基准日 K 历史"),
    bars: int = typer.Option(760, "--bars", help="历史回填棒数（约 3 年）"),
) -> None:
    """行情与宏观数据同步（Dashboard 市场数据层的离线刷新入口）。"""
    from industrypulse.market.service import MarketService

    service = MarketService()
    if not (macro or history):
        macro = history = True
    summary: dict[str, object] = {}
    if history:
        symbols: list[str] = []
        for config in service.sectors_config["domains"].values():
            symbols.append(config["board"])
            symbols.append(config["etfs"]["primary"])
            symbols.extend(alternative for alternative in config["etfs"].get("alternatives", []))
        symbols += [item["symbol"] for item in service.sectors_config["benchmarks"].values()]
        succeeded = failed = 0
        for symbol in dict.fromkeys(symbols):
            try:
                service._load_kline(symbol)
                succeeded += 1
            except Exception as exc:  # noqa: BLE001 - 单源失败继续
                failed += 1
                typer.echo(f"  失败 {symbol}: {str(exc)[:80]}")
        summary["history"] = {"succeeded": succeeded, "failed": failed, "bars": bars}
        service._flush_bars()
        service.health.persist()
    if macro:
        counts = service.sync_macro()
        service.health.persist()
        summary["macro"] = counts
    _emit(summary)


if __name__ == "__main__":
    app()
