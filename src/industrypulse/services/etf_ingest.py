"""ETF 行情摄取：抓取 -> 不可变 Bronze -> 资产主档 + 不可变 bar。

纪律：
- 原始 JSON 全部归档（内容寻址 sha256），先有证据再有数据。
- ``adj_method`` 固定 ``none``；复权口径已在采集层用双源交叉验证过，
  总收益必须靠现金分红加回，不允许悄悄用前复权价冒充原始价。
- 资产主档的 ``list_at`` 在无官方来源时由首个交易日推导，并在 notes 明确标注
  "下界估计"，不得当作已核验的上市日。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from industrypulse.contracts.domain import AssetMaster, Exchange, InstrumentType
from industrypulse.datahub.sina_etf import (
    SinaParseError,
    derived_list_at,
    parse_sina_kline,
    to_market_bars,
)
from industrypulse.repositories import DuckDBMarketRepository, MarketDataError


class EtfIngestError(ValueError):
    """摄取失败；已归档的 Bronze 保留（只增不改的预期行为）。"""


@dataclass(frozen=True)
class EtfIngestReport:
    asset_id: str
    symbol: str
    bars: int
    inserted: int
    first_day: str
    last_day: str
    artifact_sha256: str


class EtfIngestService:
    def __init__(
        self,
        market: DuckDBMarketRepository,
        *,
        bronze_root: Path,
        source_url: str = "https://finance.sina.com.cn/",
    ) -> None:
        self.market = market
        self.bronze_root = Path(bronze_root)
        self.source_url = source_url

    def _archive(self, payload: bytes) -> tuple[str, str]:
        digest = hashlib.sha256(payload).hexdigest()
        path = self.bronze_root / digest[:2] / digest
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise EtfIngestError("Bronze 原始文件被篡改")
        else:
            with path.open("xb") as stream:
                stream.write(payload)
        return digest, str(path)

    def ingest_symbol(
        self,
        *,
        symbol: str,
        asset_id: str,
        name_zh: str,
        fetch: Callable[[str], str],
        exchange: Exchange = Exchange.SSE,
        instrument_type: InstrumentType = InstrumentType.ETF,
        tracking_index_id: str | None = None,
    ) -> EtfIngestReport:
        raw_text = fetch(symbol)
        content = raw_text.encode("utf-8")
        digest, _path = self._archive(content)
        try:
            quotes = parse_sina_kline(raw_text)
        except SinaParseError as exc:
            raise EtfIngestError(f"{symbol} 解析失败：{exc}") from exc
        if not quotes:
            raise EtfIngestError(f"{symbol} 没有可用日线")
        list_at = derived_list_at(quotes)
        asset = AssetMaster(
            asset_id=asset_id,
            exchange=exchange,
            instrument_type=instrument_type,
            name_zh=name_zh,
            list_at=list_at,
            tracking_index_id=tracking_index_id,
            notes=(
                "第三方来源（新浪财经）；list_at 由首个交易日推导，属上市时点下界估计；"
                f"原始 JSON sha256={digest}；adj_method=none（不复权原始价）"
            ),
        )
        try:
            if asset_id not in {item.asset_id for item in self.market.assets()}:
                self.market.register_asset(asset)
            bars = to_market_bars(quotes, asset_id=asset_id)
            inserted = self.market.publish_bars(bars)
        except MarketDataError as exc:
            raise EtfIngestError(str(exc)) from exc
        return EtfIngestReport(
            asset_id=asset_id,
            symbol=symbol,
            bars=len(bars),
            inserted=inserted,
            first_day=quotes[0].trade_date.isoformat(),
            last_day=quotes[-1].trade_date.isoformat(),
            artifact_sha256=digest,
        )


__all__ = ["EtfIngestError", "EtfIngestReport", "EtfIngestService"]
