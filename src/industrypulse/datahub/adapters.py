"""Offline official-table adapters; no guessed remote APIs or contract rolling."""

import csv
import json
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree
from zipfile import ZipFile

from industrypulse.contracts.common import TZ_SHANGHAI
from industrypulse.contracts.observation import MissingReason, RawArtifact
from industrypulse.contracts.sources import TimestampPrecision
from industrypulse.core.html_tables import (
    header_span,
    is_publisher_footnote,
    locate,
    parse_number,
    tables,
)
from industrypulse.datahub.publication import extract_publication
from industrypulse.datahub.source_registry import SourceRegistry


@dataclass(frozen=True)
class ParsedObservation:
    series_id: str
    period: str
    value: Decimal | None
    unit: str
    row_label: str
    column_label: str
    locator: str
    raw_text: str
    is_ytd: bool
    statistical_population: str
    missing_reason: MissingReason | None = None
    publication_at: datetime | None = None
    retrieved_at: datetime | None = None
    artifact_id: str | None = None
    source_url: str | None = None
    timestamp_precision: TimestampPrecision = TimestampPrecision.UNKNOWN
    #: 日频/非月度序列的真实所属期边界；None 时由 period(YYYY-MM) 推自然月。
    observation_start: date | None = None
    observation_end: date | None = None


def parse_table(
    html: str,
    *,
    series_id: str,
    period: str,
    row: str,
    column: str,
    unit: str,
    is_ytd: bool,
    population: str,
    table_index: int | None = None,
) -> ParsedObservation:
    if not unit or not population:
        raise ValueError("unit and statistical population must be declared")
    date.fromisoformat(period + "-01")
    cell = locate(html, row, column, table_index=table_index)
    value = parse_number(cell.text)
    publication_at, precision = extract_publication(html)
    return ParsedObservation(
        series_id,
        period,
        value,
        unit,
        row,
        column,
        cell.locator,
        cell.text,
        is_ytd,
        population,
        (
            MissingReason.WITHDRAWN_BY_PUBLISHER
            if value is None and is_publisher_footnote(cell.text)
            else MissingReason.NOT_COLLECTED
            if value is None
            else None
        ),
        publication_at=publication_at,
        timestamp_precision=precision,
    )


@dataclass(frozen=True)
class TableColumn:
    """表格某一列的精确表头标签与单位（供 parse_* 原样传入）。"""

    label: str
    unit: str


_PAREN_TRANSLATION = str.maketrans({"（": "(", "）": ")", "％": "%"})


def _normalized_unit(text: str) -> str:
    return text.translate(_PAREN_TRANSLATION).strip().strip("()")


def resolve_table_column(
    html: str, *, table_index: int, metric: str = "利润总额", sub: str = "金额"
) -> TableColumn:
    """从多级表头中解析出指定指标的精确列标签与单位。

    只做确定性结构匹配：表头首格必须等于 ``metric``，且表头块中存在一格等于
    ``sub``；匹配数不为 1 即拒绝。这样既兼容官方页面全角/半角括号的排版差异，
    又不会在列含义不清时"猜一个"。

    返回的 ``label`` 必须原样传给 :func:`parse_profit` 等解析器，由 ``locate``
    再做一次精确匹配，形成双重确认。
    """
    collection = tables(html)
    if not 1 <= table_index <= len(collection):
        raise ValueError(f"table_index 越界：{table_index}，共 {len(collection)} 张表")
    table = collection[table_index - 1]
    header_start, header_end = header_span(table)
    if header_end <= header_start:
        raise ValueError("未识别到表头区，拒绝猜测列含义")
    width = max((len(row) for row in table), default=0)
    matches: list[TableColumn] = []
    for column in range(1, width):
        cells = [
            table[row][column].text
            for row in range(header_start, header_end)
            if column < len(table[row])
        ]
        if not cells or cells[0] != metric or sub not in cells[1:]:
            continue
        unit = _normalized_unit(cells[-1])
        if unit not in {"亿元", "万元", "%"}:
            raise ValueError(f"未登记的表头单位：{cells[-1]!r}")
        matches.append(TableColumn(label=" / ".join(dict.fromkeys(cells)), unit=unit))
    if len(matches) != 1:
        raise ValueError(f"{metric}/{sub} 列定位不唯一：found {len(matches)}")
    return matches[0]


#: 分行业财务表的指标 -> series 后缀（列名必须显式包含该指标名）。
_INDUSTRY_METRICS = {"营业收入": "revenue", "营业成本": "cost", "利润总额": "profit"}


def parse_profit(
    html: str,
    *,
    period: str,
    row: str,
    column: str,
    unit: str = "亿元",
    table_index: int | None = None,
    metric: str = "利润总额",
) -> ParsedObservation:
    """规模以上工业企业分行业财务表：营业收入 / 营业成本 / 利润总额 的金额或同比。

    ``metric`` 必须与列名一致，且单位必须与口径匹配；不猜、不做单位换算。
    """
    # January/February is a joint period; never fabricate a January value.
    if period.endswith("-01"):
        raise ValueError("industrial profit has no standalone January release")
    if row != "黑色金属冶炼和压延加工业":
        raise ValueError("the C31 adapter requires the exact official industry label")
    if metric not in _INDUSTRY_METRICS:
        raise ValueError(f"未登记的财务指标：{metric!r}")
    if metric not in column:
        raise ValueError("columns must name the metric explicitly")
    is_growth = "同比" in column
    if (is_growth and unit != "%") or (not is_growth and unit not in {"亿元", "万元"}):
        raise ValueError("amount/growth unit mismatch")
    suffix = _INDUSTRY_METRICS[metric]
    return parse_table(
        html,
        series_id=f"cn.nbs.industry_c31.{suffix}_ytd" + ("_yoy" if is_growth else ""),
        period=period,
        row=row,
        column=column,
        unit=unit,
        is_ytd=True,
        population="规模以上工业企业",
        table_index=table_index,
    )


def parse_iva(
    html: str,
    *,
    period: str,
    row: str,
    column: str,
    kind: str,
    unit: str,
    table_index: int | None = None,
) -> ParsedObservation:
    allowed = {"industry": {"黑色金属冶炼和压延加工业"}, "product": {"粗钢", "钢材", "生铁"}}
    if kind not in allowed or row not in allowed[kind]:
        raise ValueError("industry and product mappings must remain distinct")
    return parse_table(
        html,
        series_id=f"cn.nbs.{kind}."
        + {
            "黑色金属冶炼和压延加工业": "c31",
            "粗钢": "crude_steel",
            "钢材": "steel",
            "生铁": "pig_iron",
        }[row],
        period=period,
        row=row,
        column=column,
        unit=unit,
        is_ytd=False,
        population="规模以上工业",
        table_index=table_index,
    )


def parse_pmi(
    html: str, *, period: str, row: str, column: str, category: str, table_index: int
) -> ParsedObservation:
    if category not in {"headline", "component"}:
        raise ValueError("unknown PMI category")
    if (category == "headline") != (row == "制造业PMI"):
        raise ValueError("headline/component mismatch")
    indicators = {
        "制造业PMI": "headline",
        "生产": "production",
        "新订单": "new_orders",
        "购进价格": "input_prices",
        "出厂价格": "output_prices",
        "原材料库存": "raw_inventory",
        "产成品库存": "finished_inventory",
    }
    if row not in indicators:
        raise ValueError("PMI component has no reviewed series mapping")
    return parse_table(
        html,
        series_id=f"cn.nbs.pmi.{indicators[row]}",
        period=period,
        row=row,
        column=column,
        unit="%",
        is_ytd=False,
        population="制造业采购经理调查",
        table_index=table_index,
    )


#: 真实 PMI 月表的列名 -> series 后缀（列名取自官方页面原文，含内部空格）。
PMI_MATRIX_COLUMNS = {
    "PMI": "headline",
    "生产": "production",
    "新订单": "new_orders",
    "原材料 库存": "raw_inventory",
    "从业人员": "employment",
    "供应商 配送时间": "supplier_delivery",
}


def parse_pmi_matrix(
    html: str, *, period: str, month_label: str, indicator: str, table_index: int
) -> ParsedObservation:
    """PMI 月表（真实公告版式：行为月份、列为指标）。

    与 :func:`parse_pmi` 的区别：后者假设行是指标、列是所属期，那是合成 fixture
    的形状；国家统计局实际页面是反过来的，且首行是 "单位：%" 横幅。两个形状
    都保留，调用方必须显式选择，不做自动判别。
    """
    if indicator not in PMI_MATRIX_COLUMNS:
        raise ValueError(f"未登记的 PMI 列：{indicator!r}")
    return parse_table(
        html,
        series_id=f"cn.nbs.pmi.{PMI_MATRIX_COLUMNS[indicator]}",
        period=period,
        row=month_label,
        column=indicator,
        unit="%",
        is_ytd=False,
        population="制造业采购经理调查",
        table_index=table_index,
    )


def parse_tsf(
    html: str,
    *,
    period: str,
    row: str,
    column: str,
    is_ytd: bool,
    population: str,
    unit: str,
    table_index: int | None = None,
    kind: str = "flow",
) -> ParsedObservation:
    if kind not in {"flow", "stock"} or (kind == "stock" and is_ytd):
        raise ValueError("TSF stock cannot use cumulative-flow semantics")
    return parse_table(
        html,
        series_id="cn.pbc.tsf."
        + ("stock" if kind == "stock" else "flow_ytd" if is_ytd else "flow_monthly"),
        period=period,
        row=row,
        column=column,
        unit=unit,
        is_ytd=is_ytd,
        population=population,
        table_index=table_index,
    )


@dataclass(frozen=True)
class FuturesQuote:
    trading_date: date
    contract: str
    settlement: Decimal
    volume: int
    open_interest: int
    unit: str = "CNY/tonne"


def parse_shfe(rows: list[dict[str, str]], trading_date: date) -> list[FuturesQuote]:
    result = []
    seen = set()
    for row in rows:
        if date.fromisoformat(row["trading_date"]) != trading_date:
            raise ValueError("trading date mismatch")
        contract = row["contract"].upper()
        if contract[:2] not in {"RB", "HC"}:
            continue
        if len(contract) != 6 or not contract[2:].isdigit() or contract in seen:
            raise ValueError("invalid or duplicate individual futures contract")
        seen.add(contract)
        settlement = Decimal(row["settlement"])
        volume, interest = int(row["volume"]), int(row["open_interest"])
        if not settlement.is_finite() or settlement <= 0 or min(volume, interest) < 0:
            raise ValueError("invalid quote")
        result.append(FuturesQuote(trading_date, contract, settlement, volume, interest))
    return result


@dataclass(frozen=True)
class InventoryReport:
    values: dict[str, Decimal]
    expected_periods: int
    covered_periods: int
    source_verified: bool = False

    @property
    def coverage(self) -> float:
        return self.covered_periods / self.expected_periods if self.expected_periods else 0.0


def parse_inventory(rows: list[dict[str, str]], expected_dates: set[date]) -> InventoryReport:
    values: dict[str, Decimal] = {}
    covered: set[date] = set()
    for row in rows:
        day = date.fromisoformat(row["date"])
        if day not in expected_dates or row["commodity"] not in {"RB", "HC"}:
            raise ValueError("unexpected inventory date or commodity")
        if row["unit"] not in {"吨", "tonne"}:
            raise ValueError("inventory unit mismatch")
        key = f"{day}:{row['commodity']}"
        value = Decimal(row["inventory"])
        if key in values or not value.is_finite() or value < 0:
            raise ValueError("invalid/duplicate inventory")
        values[key] = value
    for day in expected_dates:
        if all(f"{day}:{product}" in values for product in ("RB", "HC")):
            covered.add(day)
    return InventoryReport(values, len(expected_dates), len(covered))


def _shfe_publication(
    payload: dict[str, object],
) -> tuple[datetime | None, TimestampPrecision]:
    """从 ``update_date``（如 ``20261019 15:56:16``）取发布时间；缺失即 UNKNOWN。"""
    raw = payload.get("update_date")
    if not isinstance(raw, str):
        return None, TimestampPrecision.UNKNOWN
    try:
        stamp = datetime.strptime(raw.strip().replace("/", "-"), "%Y%m%d %H:%M:%S")
    except ValueError:
        return None, TimestampPrecision.UNKNOWN
    return stamp.replace(tzinfo=TZ_SHANGHAI), TimestampPrecision.SECOND


def parse_shfe_daily(payload_text: str, *, trading_date: date, contract: str) -> ParsedObservation:
    """上期所日行情 JSON -> 指定合约的结算价观测。

    只取调用方**显式指定**的合约，绝不自动挑"主力"：主力切换会产生合约拼接式
    未来函数（S-007 明令禁止）。``小计`` 等非合约行与缺失结算价的行一律跳过，
    其余数值校验复用 :func:`parse_shfe`。
    """
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"SHFE 日行情不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("SHFE 日行情顶层必须是对象")
    instruments = payload.get("o_curinstrument")
    if not isinstance(instruments, list) or not instruments:
        raise ValueError("SHFE 日行情缺少 o_curinstrument 数组")
    report = str(payload.get("report_date") or "")
    if report and report != trading_date.strftime("%Y%m%d"):
        raise ValueError(f"文件报告日 {report} 与请求交易日 {trading_date.isoformat()} 不一致")
    rows: list[dict[str, str]] = []
    for item in instruments:
        if not isinstance(item, dict):
            continue
        product = str(item.get("PRODUCTID", ""))
        month = str(item.get("DELIVERYMONTH", ""))
        settlement = item.get("SETTLEMENTPRICE")
        if not product.endswith("_f") or len(month) != 4 or not month.isdigit():
            continue  # 小计行 / 非期货品种
        if settlement in (None, ""):
            continue
        rows.append(
            {
                "trading_date": trading_date.isoformat(),
                "contract": f"{product[:-2].upper()}{month}",
                "settlement": str(settlement),
                "volume": str(item.get("VOLUME") or 0),
                "open_interest": str(item.get("OPENINTEREST") or 0),
            }
        )
    quotes = parse_shfe(rows, trading_date)
    target = contract.strip().upper()
    match = next((quote for quote in quotes if quote.contract == target), None)
    if match is None:
        listed = ", ".join(quote.contract for quote in quotes)
        raise ValueError(f"当日行情中没有合约 {target}；可选：{listed}")
    publication_at, precision = _shfe_publication(payload)
    return ParsedObservation(
        series_id=f"cn.shfe.{target[:2].lower()}{target[2:]}.settlement",
        period=f"{trading_date.year:04d}-{trading_date.month:02d}",
        value=match.settlement,
        unit="元/吨",
        row_label=match.contract,
        column_label="结算价",
        locator=f"kx[{trading_date.isoformat()}]/{match.contract}/SETTLEMENTPRICE",
        raw_text=str(match.settlement),
        is_ytd=False,
        statistical_population="上海期货交易所 期货合约日行情",
        publication_at=publication_at,
        timestamp_precision=precision,
        observation_start=trading_date,
        observation_end=trading_date,
    )


def _xlsx_rows(path: Path) -> list[dict[str, str]]:
    """Read OOXML values without optional Excel engines; reject unevaluated formulas."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(path) as archive:
        strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            strings = ["".join(si.itertext()) for si in root]
        sheets = sorted(
            name
            for name in archive.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        if len(sheets) != 1:
            raise ValueError("manual import requires exactly one worksheet")
        root = ElementTree.fromstring(archive.read(sheets[0]))
        matrix: list[list[str]] = []
        for row in root.findall(".//m:row", ns):
            values: dict[int, str] = {}
            for cell in row.findall("m:c", ns):
                if cell.find("m:f", ns) is not None:
                    raise ValueError("formula cells are not authoritative raw observations")
                col = 0
                for char in cell.attrib["r"]:
                    if char.isalpha():
                        col = col * 26 + ord(char.upper()) - 64
                value = cell.findtext("m:v", "", ns)
                if cell.attrib.get("t") == "s":
                    value = strings[int(value)]
                elif cell.attrib.get("t") == "inlineStr":
                    value = "".join(cell.find("m:is", ns).itertext())  # type: ignore[union-attr]
                values[col - 1] = value
            matrix.append([values.get(index, "") for index in range(max(values, default=-1) + 1)])
    if not matrix:
        return []
    return [dict(zip(matrix[0], row, strict=True)) for row in matrix[1:]]


def import_manual(
    path: Path, artifact: RawArtifact, registry: SourceRegistry
) -> list[ParsedObservation]:
    source = registry.require_permission(artifact.source_id, manual=True)
    import hashlib

    if hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256:
        raise ValueError("manual file does not match archived artifact")
    if not artifact.source_url:
        raise ValueError("manual imports require the original official source URL")
    original = urlsplit(artifact.source_url)
    if (
        original.scheme != "https"
        or original.username
        or original.hostname != urlsplit(source.url).hostname
    ):
        raise PermissionError("manual source URL must match the declared original publisher")
    if path.suffix.lower() == ".xlsx":
        rows = _xlsx_rows(path)
    else:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
    result = []
    for index, row in enumerate(rows, 2):
        publication = datetime.fromisoformat(row["publication_at"])
        if publication.tzinfo is None:
            raise ValueError("manual publication timestamps must be timezone aware")
        date.fromisoformat(row["period"] + "-01")
        value = parse_number(row["value"])
        if row["is_ytd"] not in {"true", "false"}:
            raise ValueError("is_ytd must be explicit true/false")
        result.append(
            ParsedObservation(
                row["series_id"],
                row["period"],
                value,
                row["unit"],
                row["series_id"],
                "value",
                f"{path.name}!row[{index}]",
                row["value"],
                row["is_ytd"] == "true",
                row["statistical_population"],
                MissingReason.NOT_COLLECTED if value is None else None,
            )
        )
    return [
        replace(
            item,
            publication_at=datetime.fromisoformat(row["publication_at"]),
            retrieved_at=artifact.retrieved_at,
            artifact_id=artifact.artifact_id,
            source_url=artifact.source_url,
        )
        for item, row in zip(result, rows, strict=True)
    ]
