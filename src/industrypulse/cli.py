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
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from industrypulse.contracts import CONTRACTS_VERSION
from industrypulse.contracts.context import ResearchMode
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
from industrypulse.datahub.source_registry import SourceRegistry
from industrypulse.repositories import DuckDBObservationRepository, PublicationError
from industrypulse.services import (
    BatchItem,
    IngestError,
    IngestService,
    format_table,
)
from industrypulse.services import (
    compare_modes as build_pit_comparison,
)
from industrypulse.taxonomy import TaxonomyRegistry

app = typer.Typer(add_completion=False, no_args_is_help=True, help="IndustryPulse 本地 CLI")
source_app = typer.Typer(no_args_is_help=True, help="数据源目录、许可与生命周期")
calendar_app = typer.Typer(no_args_is_help=True, help="交易日历装载与校验")
app.add_typer(source_app, name="source")
app.add_typer(calendar_app, name="calendar")

DEFAULT_CATALOG = Path("source_catalog.csv")
DEFAULT_TAXONOMY = Path("config/industries/steel.yaml")
DEFAULT_AUDIT = Path("data/registry-audit.jsonl")


def _emit(payload: Any) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _fail(message: str) -> NoReturn:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


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
    contemporary: Annotated[
        bool,
        typer.Option("--contemporary", help="已人工核验该文件是同期原件（否则数值证据只能为 C）"),
    ] = False,
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
            contemporary=contemporary,
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
    repository = DuckDBObservationRepository(database)
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


@app.command("backfill")
def backfill(
    database: Annotated[Path, typer.Option("--database")],
    manifest: Annotated[Path, typer.Option("--manifest", help="回填清单 JSON")],
    snapshot_id: Annotated[str, typer.Option("--snapshot-id", help="冻结快照 ID")],
    parser_version: Annotated[str, typer.Option("--parser-version")] = "g2-v1",
    contemporary: Annotated[
        bool,
        typer.Option("--contemporary", help="已核验全部文档均为同期原件（默认 False）"),
    ] = False,
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
    if not manifest.is_file():
        _fail(f"清单不存在：{manifest}")
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        _fail(f"--manifest 不是合法 JSON：{exc}")
    entries = raw.get("documents") if isinstance(raw, dict) else raw
    if not isinstance(entries, list) or not entries:
        _fail("清单必须是非空 JSON 数组（或含 documents 数组的对象）")
    repository = DuckDBObservationRepository(database)
    retrieved_at = datetime.now(UTC)
    items: list[BatchItem] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            _fail(f"清单第 {index} 项不是对象")
        missing = [
            key for key in ("source_id", "period", "file", "url", "parser") if key not in entry
        ]
        if missing:
            _fail(f"清单第 {index} 项缺少字段：{', '.join(missing)}")
        document = Path(entry["file"])
        if not document.is_file():
            _fail(f"清单第 {index} 项文件不存在：{document}")
        if "params_file" in entry:
            params_source = Path(entry["params_file"])
            if not params_source.is_file():
                _fail(f"清单第 {index} 项参数文件不存在：{params_source}")
            params_text = params_source.read_text(encoding="utf-8-sig")
        else:
            params_text = json.dumps(entry.get("params", {}), ensure_ascii=False)
        try:
            params_object: Any = json.loads(params_text)
        except json.JSONDecodeError as exc:
            _fail(f"清单第 {index} 项参数不是合法 JSON：{exc}")
        if not isinstance(params_object, dict):
            _fail(f"清单第 {index} 项参数必须是对象")
        content = document.read_bytes()
        try:
            html = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            _fail(f"清单第 {index} 项不是 UTF-8 HTML：{exc}")
        try:
            parsed = _run_parser(entry["parser"], html, entry["period"], params_object)
        except (IngestError, ValueError) as exc:
            _fail(f"清单第 {index} 项解析失败：{exc}")
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
    try:
        report = IngestService(repository, parser_version=parser_version).ingest_batch(
            items, snapshot_id=snapshot_id, contemporary=contemporary, trusted_copy=trusted_copy
        )
    except (IngestError, PublicationError, ValueError) as exc:
        _fail(str(exc))
    _emit(report.model_dump(mode="json"))


if __name__ == "__main__":
    app()
