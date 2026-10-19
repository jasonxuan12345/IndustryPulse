"""按月回填官方公告：发现 -> 抓取 -> 解析 -> 单一冻结快照（O-006）。

这条链把此前手工完成的三件事串成一次可重放的操作：

1. **发现**：抓取官方新闻发布列表页，用 :mod:`datahub.discovery` 的严格正则识别
   目标公告。不调用搜索、不猜 URL；识别不了就不入库。
2. **抓取**：全部走 S 线 ``Fetcher``（许可门禁 + 主机白名单 + 内容寻址归档）。
3. **解析入库**：利润公告产出 6 条序列、PMI 产出 6 条、工业增加值产出 1 条；
   全部文档发布进**同一个**快照，便于跨期 as-of 回放。

解析失败的文档不会静默丢弃：记入 ``skipped`` 并附原因，其余照常入库。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from industrypulse.contracts.observation import RawArtifact
from industrypulse.core.html_tables import tables
from industrypulse.datahub.adapters import (
    PMI_MATRIX_COLUMNS,
    ParsedObservation,
    parse_iva,
    parse_pmi_matrix,
    parse_profit,
    resolve_table_column,
)
from industrypulse.datahub.discovery import Announcement, discover, in_range
from industrypulse.repositories import DuckDBObservationRepository, PublicationError
from industrypulse.services.as_published import AsPublishedClaim
from industrypulse.services.ingest import (
    BatchIngestReport,
    BatchItem,
    IngestError,
    IngestService,
)

#: 国家统计局新闻发布列表页（相对页码）。
LISTING_BASE = "https://www.stats.gov.cn/sj/zxfb/"

#: 钢铁行业在国家统计局分行业表中的精确标签。
STEEL_ROW = "黑色金属冶炼和压延加工业"


class ArtifactFetcher(Protocol):
    def fetch(
        self, source_id: str, url: str, *, discovery_url: str | None = None
    ) -> RawArtifact: ...


class UnusableDocument(IngestError):
    """该文档无法为当前序列提供合法观测（口径不符等），换表也无济于事。"""


@dataclass(frozen=True)
class BackfillOutcome:
    report: BatchIngestReport
    discovered: int
    skipped: tuple[dict[str, str], ...]


def _first_working_table(
    build: Callable[[int], list[ParsedObservation]], table_count: int
) -> list[ParsedObservation]:
    """依次尝试每张表；全部失败时抛出最后一次错误，不猜。"""
    last: Exception | None = None
    for index in range(1, table_count + 1):
        try:
            return build(index)
        except UnusableDocument:
            raise  # 口径问题与"换张表"无关，立即上报
        except ValueError as exc:
            last = exc
    raise IngestError(f"没有可用表格（共 {table_count} 张）：{last}")


def parse_profit_document(
    html: str, period: str, *, row: str = STEEL_ROW
) -> list[ParsedObservation]:
    """分行业财务表 -> 营收/成本/利润 × 金额/同比 共 6 条序列。"""

    def build(index: int) -> list[ParsedObservation]:
        results: list[ParsedObservation] = []
        for metric in ("营业收入", "营业成本", "利润总额"):
            for sub in ("金额", "同比增长"):
                column = resolve_table_column(html, table_index=index, metric=metric, sub=sub)
                results.append(
                    parse_profit(
                        html,
                        period=period,
                        row=row,
                        column=column.label,
                        unit=column.unit,
                        table_index=index,
                        metric=metric,
                    )
                )
        return results

    return _first_working_table(build, len(tables(html)))


def parse_pmi_document(html: str, period: str) -> list[ParsedObservation]:
    """PMI 月表 -> 6 个分项（当月那一行）。"""
    year, month = period.split("-")
    label = f"{int(year)}年{int(month)}月"

    def build(index: int) -> list[ParsedObservation]:
        results: list[ParsedObservation] = []
        for indicator in PMI_MATRIX_COLUMNS:
            last: Exception | None = None
            parsed: ParsedObservation | None = None
            for candidate in (indicator, indicator.replace(" ", "")):
                try:
                    parsed = parse_pmi_matrix(
                        html,
                        period=period,
                        month_label=label,
                        indicator=candidate,
                        table_index=index,
                    )
                    break
                except ValueError as exc:
                    last = exc
            if parsed is None:
                raise IngestError(f"PMI 列 {indicator!r} 无法定位：{last}")
            results.append(parsed)
        return results

    return _first_working_table(build, len(tables(html)))


def parse_iva_document(html: str, period: str, *, row: str = STEEL_ROW) -> list[ParsedObservation]:
    """工业增加值表 -> 行业当月同比。兼容全角/半角括号的列名。"""
    month = int(period.split("-")[1])
    monthly = [f"{month}月 / 同比增长 （%）", f"{month}月 / 同比增长 (%)"]
    cumulative = [
        f"1—{month}月 / 同比增长 （%）",
        f"1—{month}月 / 同比增长 (%)",
        f"1-{month}月 / 同比增长 (%)",
    ]

    def build(index: int) -> list[ParsedObservation]:
        last: Exception | None = None
        for column in monthly:
            try:
                return [
                    parse_iva(
                        html,
                        period=period,
                        row=row,
                        column=column,
                        kind="industry",
                        unit="%",
                        table_index=index,
                    )
                ]
            except ValueError as exc:
                last = exc
        # 1—2 月合并公告只给累计口径；本序列是当月同比，混用会造成口径污染
        for column in cumulative:
            try:
                parse_iva(
                    html,
                    period=period,
                    row=row,
                    column=column,
                    kind="industry",
                    unit="%",
                    table_index=index,
                )
            except ValueError:
                continue
            raise UnusableDocument(
                f"{period} 公告只提供 1—{month} 月累计口径，本序列为当月同比，拒绝混用"
            )
        raise IngestError(f"工业增加值列无法定位：{last}")

    return _first_working_table(build, len(tables(html)))


def parse_document(announcement: Announcement, html: str) -> list[ParsedObservation]:
    if announcement.kind == "profit":
        return parse_profit_document(html, announcement.period)
    if announcement.kind == "pmi":
        return parse_pmi_document(html, announcement.period)
    if announcement.kind == "iva":
        return parse_iva_document(html, announcement.period)
    raise IngestError(f"未支持的公告类别：{announcement.kind}")


class MonthlyBackfill:
    """按月回填编排：所有文档发布进同一个冻结快照。"""

    def __init__(
        self,
        repository: DuckDBObservationRepository,
        fetcher: ArtifactFetcher,
        *,
        listing_base: str = LISTING_BASE,
        max_pages: int = 30,
    ) -> None:
        if max_pages < 1:
            raise ValueError("max_pages 必须为正")
        self.repository = repository
        self.fetcher = fetcher
        self.listing_base = listing_base
        self.max_pages = max_pages

    def _page_url(self, page: int) -> str:
        return self.listing_base if page == 0 else f"{self.listing_base}index_{page}.html"

    def discover(
        self, *, source_id: str, kinds: Sequence[str], start: str, end: str
    ) -> list[Announcement]:
        """翻列表页直到越过起始期；返回区间内的公告（按出现顺序去重）。"""
        collected: dict[tuple[str, str], Announcement] = {}
        for page in range(self.max_pages):
            url = self._page_url(page)
            artifact = self.fetcher.fetch(source_id, url, discovery_url=self.listing_base)
            html = Path(artifact.uri).read_text(encoding="utf-8")
            found = discover(html, base_url=url, kinds=tuple(kinds))
            added = 0
            for item in found:
                if (item.kind, item.period) not in collected:
                    collected[(item.kind, item.period)] = item
                    added += 1
            periods = [item.period for item in collected.values()]
            # 同属期的不同类别公告发布日不同，可能落在后续页，因此必须越过起始期；
            # 若区间已覆盖且本页没有新增，说明列表已到底，可以停。
            if periods and min(periods) < start:
                break
            if periods and min(periods) <= start and added == 0:
                break
        return in_range(list(collected.values()), start, end)

    def run(
        self,
        announcements: Sequence[Announcement],
        *,
        source_id: str,
        snapshot_id: str,
        parser_version: str = "g2-v1",
        as_published: AsPublishedClaim | None = None,
        extra: Sequence[BatchItem] = (),
    ) -> BackfillOutcome:
        if not announcements and not extra:
            raise IngestError("没有可回填的公告")
        items: list[BatchItem] = []
        skipped: list[dict[str, str]] = []
        for announcement in announcements:
            artifact = self.fetcher.fetch(
                source_id, announcement.url, discovery_url=self.listing_base
            )
            content = Path(artifact.uri).read_bytes()
            try:
                html = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                skipped.append(
                    {
                        "kind": announcement.kind,
                        "period": announcement.period,
                        "url": announcement.url,
                        "reason": f"非 UTF-8：{exc}",
                    }
                )
                continue
            try:
                parsed = parse_document(announcement, html)
                specs = {
                    item.series_id: self.repository.series_spec(item.series_id) for item in parsed
                }
            except (ValueError, IngestError, PublicationError) as exc:
                skipped.append(
                    {
                        "kind": announcement.kind,
                        "period": announcement.period,
                        "url": announcement.url,
                        "reason": str(exc)[:240],
                    }
                )
                continue
            items.append(BatchItem(artifact=artifact, content=content, parsed=parsed, specs=specs))
        items.extend(extra)
        if not items:
            raise IngestError(f"全部 {len(announcements)} 份文档均解析失败")
        report = IngestService(self.repository, parser_version=parser_version).ingest_batch(
            items, snapshot_id=snapshot_id, as_published=as_published
        )
        return BackfillOutcome(report=report, discovered=len(announcements), skipped=tuple(skipped))


__all__ = [
    "BackfillOutcome",
    "MonthlyBackfill",
    "STEEL_ROW",
    "UnusableDocument",
    "parse_document",
    "parse_iva_document",
    "parse_pmi_document",
    "parse_profit_document",
]
