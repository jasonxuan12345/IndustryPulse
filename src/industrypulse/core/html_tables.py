"""Deterministic HTML cell extraction; script and instruction text are inert."""

import re
from dataclasses import dataclass
from decimal import Decimal
from html.parser import HTMLParser


@dataclass(frozen=True)
class Cell:
    text: str
    locator: str
    rowspan: int = 1
    colspan: int = 1


class _Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[Cell]]] = []
        self.row: list[Cell] | None = None
        self.parts: list[str] | None = None
        self.attrs: dict[str, str | None] = {}
        self.ignore = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.ignore += 1
        elif not self.ignore and tag == "table":
            self.tables.append([])
        elif not self.ignore and tag == "tr" and self.tables:
            self.row = []
            self.tables[-1].append(self.row)
        elif not self.ignore and tag in {"td", "th"} and self.row is not None:
            self.parts, self.attrs = [], dict(attrs)

    def handle_data(self, data: str) -> None:
        if self.parts is not None and not self.ignore:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.ignore:
            self.ignore -= 1
        elif tag in {"td", "th"} and self.parts is not None and self.row is not None:
            self.row.append(
                Cell(
                    " ".join("".join(self.parts).split()),
                    f"table[{len(self.tables)}]/tr[{len(self.tables[-1])}]"
                    f"/cell[{len(self.row) + 1}]",
                    int(self.attrs.get("rowspan") or 1),
                    int(self.attrs.get("colspan") or 1),
                )
            )
            self.parts = None
        elif tag == "tr":
            self.row = None


def tables(html: str) -> list[list[list[Cell]]]:
    parser = _Tables()
    parser.feed(html)
    result: list[list[list[Cell]]] = []
    for table in parser.tables:
        grid: dict[tuple[int, int], Cell] = {}
        for r, row in enumerate(table):
            col = 0
            for cell in row:
                while (r, col) in grid:
                    col += 1
                if not 1 <= cell.rowspan <= 100 or not 1 <= cell.colspan <= 100:
                    raise ValueError("unsupported table span")
                for dr in range(cell.rowspan):
                    for dc in range(cell.colspan):
                        grid[r + dr, col + dc] = cell
                col += cell.colspan
        height = max((r for r, _ in grid), default=-1) + 1
        width = max((c for _, c in grid), default=-1) + 1
        result.append(
            [[grid.get((r, c), Cell("", "missing")) for c in range(width)] for r in range(height)]
        )
    return result


def parse_number(text: str) -> Decimal | None:
    text = text.strip().replace(",", "").replace("−", "-").replace("％", "%")
    if text in {"", "—", "--", "…", "...", "-", "N/A"}:
        return None
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?%?", text):
        raise ValueError(f"not a numeric cell: {text!r}")
    value = Decimal(text.removesuffix("%"))
    if not value.is_finite():
        raise ValueError("non-finite number")
    return value


def header_span(table: list[list[Cell]]) -> tuple[int, int]:
    """返回表头区 ``[start, end)``。

    真实官方表格常有三类干扰，合成 fixture 一个都没有：

    1. **表题/单位横幅行**：合并单元格展开后整行文本相同（如全行 "单位：%"）；
    2. **多级表头**：利润总额 / 金额 / （亿元）；
    3. **同表数据行**：表格把表头和数据放在同一张表里。

    若把"目标行之上的所有行"都当表头，数据值会混进表头标签集合导致精确列定位
    失败。这里先跳过横幅行，再取"首列仍等于表头存根标签"的连续块。
    """
    if not table:
        return (0, 0)
    start = 0
    while (
        start < len(table)
        and len(table[start]) > 1
        and len({cell.text for cell in table[start]}) == 1
    ):
        start += 1
    if start >= len(table):
        return (start, start)
    stub = table[start][0].text
    end = start
    while end < len(table) and table[end] and table[end][0].text == stub:
        end += 1
    return (start, end)


def header_row_count(table: list[list[Cell]]) -> int:
    """表头行数（:func:`header_span` 的便捷封装）。"""
    start, end = header_span(table)
    return end - start


def locate(html: str, row_label: str, column_label: str, *, table_index: int | None = None) -> Cell:
    matches: list[Cell] = []
    for index, table in enumerate(tables(html), start=1):
        if table_index is not None and index != table_index:
            continue
        # Some legal exports transpose industry labels into the header row.
        if table:
            for c, header in enumerate(table[0][1:], start=1):
                if header.text == row_label:
                    for data_row in table[1:]:
                        if data_row and data_row[0].text == column_label:
                            matches.append(data_row[c])
        header_start, header_end = header_span(table)
        for r, row in enumerate(table):
            if not row or row[0].text != row_label:
                continue
            for c in range(1, len(row)):
                headers = list(
                    dict.fromkeys(
                        table[h][c].text
                        for h in range(header_start, min(r, header_end))
                        if c < len(table[h])
                    )
                )
                if column_label in headers or column_label == " / ".join(headers):
                    matches.append(row[c])
    if len(matches) != 1:
        raise ValueError(f"expected one exact row/column, found {len(matches)}")
    return matches[0]
