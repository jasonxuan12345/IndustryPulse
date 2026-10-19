"""ETF 摄取：原始归档、不可变 bar、幂等重放。"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from industrypulse.repositories import DuckDBMarketRepository
from industrypulse.services import EtfIngestError, EtfIngestService

PAYLOAD = json.dumps(
    [
        {
            "day": "2024-01-02",
            "open": "1.000",
            "high": "1.020",
            "low": "0.990",
            "close": "1.010",
            "volume": "1000",
        },
        {
            "day": "2024-01-03",
            "open": "1.010",
            "high": "1.030",
            "low": "1.000",
            "close": "1.020",
            "volume": "1200",
        },
    ]
)


def _service(tmp_path: Path) -> tuple[EtfIngestService, DuckDBMarketRepository]:
    market = DuckDBMarketRepository(tmp_path / "market.duckdb")
    return EtfIngestService(market, bronze_root=tmp_path / "bronze"), market


def test_ingest_registers_asset_and_bars(tmp_path: Path) -> None:
    service, market = _service(tmp_path)
    report = service.ingest_symbol(
        symbol="sh515210", asset_id="ETF_515210", name_zh="钢铁ETF", fetch=lambda _s: PAYLOAD
    )
    assert report.bars == 2 and report.inserted == 2
    assert report.first_day == "2024-01-02" and report.last_day == "2024-01-03"
    asset = market.asset("ETF_515210")
    assert "下界估计" in asset.notes
    assert "不复权" in asset.notes
    bars = market.bars("ETF_515210")
    assert len(bars) == 2 and bars[0].adj_method == "none"
    # 原始 JSON 已内容寻址归档
    archived = list((tmp_path / "bronze").rglob(report.artifact_sha256))
    assert archived and archived[0].read_bytes() == PAYLOAD.encode("utf-8")


def test_reingest_is_idempotent(tmp_path: Path) -> None:
    service, market = _service(tmp_path)
    service.ingest_symbol(
        symbol="sh515210", asset_id="ETF_515210", name_zh="钢铁ETF", fetch=lambda _s: PAYLOAD
    )
    again = service.ingest_symbol(
        symbol="sh515210", asset_id="ETF_515210", name_zh="钢铁ETF", fetch=lambda _s: PAYLOAD
    )
    assert again.inserted == 0
    assert market.bar_count() == 2


def test_conflicting_bar_is_rejected(tmp_path: Path) -> None:
    service, market = _service(tmp_path)
    service.ingest_symbol(
        symbol="sh515210", asset_id="ETF_515210", name_zh="钢铁ETF", fetch=lambda _s: PAYLOAD
    )
    altered = json.loads(PAYLOAD)
    altered[0]["close"] = "9.999"
    with pytest.raises(EtfIngestError, match="不可改写"):
        service.ingest_symbol(
            symbol="sh515210",
            asset_id="ETF_515210",
            name_zh="钢铁ETF",
            fetch=lambda _s: json.dumps(altered),
        )
    assert market.bars("ETF_515210")[0].close == Decimal("1.010")


def test_bad_payload_is_rejected(tmp_path: Path) -> None:
    service, _market = _service(tmp_path)
    with pytest.raises(EtfIngestError, match="解析失败"):
        service.ingest_symbol(
            symbol="sh515210", asset_id="ETF_515210", name_zh="x", fetch=lambda _s: "not json"
        )
    with pytest.raises(EtfIngestError, match="解析失败"):
        service.ingest_symbol(
            symbol="sh515210", asset_id="ETF_515210", name_zh="x", fetch=lambda _s: "[]"
        )
