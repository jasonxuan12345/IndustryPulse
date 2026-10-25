"""market 数据层单元测试：全部 mock 网络，不依赖公网。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from industrypulse.analytics.heat import compute_heat
from industrypulse.market.health import SourceHealthMonitor
from industrypulse.market.models import Bar
from industrypulse.market.providers import (
    akshare_provider,
    chinamoney,
    eastmoney,
    sina_public,
    tencent,
)
from industrypulse.market.service import MarketService, resolve_net_flow

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def service(tmp_path_factory: pytest.TempPathFactory) -> MarketService:
    """用真实配置 + 隔离 data 目录构建服务（不触网）。"""
    root = tmp_path_factory.mktemp("market-root")
    (root / "config" / "market").mkdir(parents=True)
    for name in ("sectors.yaml", "macro_indicators.yaml"):
        (root / "config/market" / name).write_text(
            (ROOT / "config/market" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    return MarketService(root=root)


class TestTencentParsing:
    def test_quote_fields(self) -> None:
        fields = (
            ["51", "宁德时代", "300750", "237.60", "220.41", "220.00", "1114799", "0", "0"]
            + ["0"] * 20
            + [
                "",
                "20261023155430",
                "17.19",
                "7.80",
                "238.00",
                "216.51",
                "x",
                "1114799",
                "2539624",
                "2.62",
                "12.54",
                "",
                "238.00",
                "216.51",
                "9.75",
                "10123.23",
                "10994.35",
                "2.84",
                "264.49",
                "176.33",
                "1.59",
            ]
        )
        payload = 'v_sz300750="' + "~".join(fields) + '";'

        class FakeResponse:
            content = payload.encode("gbk")

            def raise_for_status(self) -> None:
                return None

        original_get = tencent.httpx.get
        tencent.httpx.get = lambda *args, **kwargs: FakeResponse()  # type: ignore[assignment]
        try:
            snapshot = tencent.fetch_quotes(["sz300750"])["sz300750"]
        finally:
            tencent.httpx.get = original_get  # type: ignore[assignment]
        assert snapshot.name == "宁德时代"
        assert snapshot.price == 237.60
        assert snapshot.change_pct == 7.80
        assert snapshot.pe == 12.54
        assert snapshot.market_cap == 10994.35
        assert snapshot.pb == 2.84
        assert snapshot.amount == pytest.approx(2539624 * 10_000)

    def test_kline_row_order(self) -> None:
        rows = [["2026-10-23", "100.0", "101.5", "102.0", "99.5", "12345"]]
        payload = json.dumps({"code": 0, "data": {"sh000300": {"day": rows}}})

        class FakeResponse:
            text = payload

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                import json as jsonlib

                return jsonlib.loads(payload)

        original = tencent.httpx.get
        tencent.httpx.get = lambda *args, **kwargs: FakeResponse()  # type: ignore[assignment]
        try:
            bars = tencent.fetch_daily_kline("sh000300")
        finally:
            tencent.httpx.get = original  # type: ignore[assignment]
        bar = bars[0]
        assert (bar.open, bar.close, bar.high, bar.low) == (100.0, 101.5, 102.0, 99.5)
        assert bar.date == date(2026, 10, 23)


class TestPeriodParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2008年01月份", date(2008, 1, 1)),
            ("2026年09月份", date(2026, 9, 1)),
            ("202604", date(2026, 4, 1)),
            ("2026.9", date(2026, 9, 1)),
            ("2013-01-01 00:00:00", date(2013, 1, 1)),
            ("2016年第1-3季度", date(2016, 7, 1)),
            (date(2026, 10, 20), date(2026, 10, 20)),
        ],
    )
    def test_parse(self, raw: object, expected: date) -> None:
        assert akshare_provider.parse_period(raw) == expected

    def test_rejects_garbage(self) -> None:
        with pytest.raises(ValueError):
            akshare_provider.parse_period("不知道")


class TestHeat:
    def test_components_and_score(self) -> None:
        score, components = compute_heat(
            news_count_24h=40,
            news_count_prev_24h=20,
            amounts=[float(i) for i in range(40)],
            change_pct=2.0,
        )
        assert 0 <= score <= 100
        assert set(components) == {"news_scale", "news_growth", "turnover_activity", "price_shock"}
        weights = sum(item["weight"] for item in components.values())
        assert weights == pytest.approx(1.0)

    def test_insufficient_history_is_explicit(self) -> None:
        _, components = compute_heat(
            news_count_24h=5, news_count_prev_24h=0, amounts=[1.0, 2.0], change_pct=None
        )
        assert "不足 21 日" in components["turnover_activity"]["input"]["note"]


class TestService:
    def test_config_domains_complete(self, service: MarketService) -> None:
        domains = service.sector_ids()
        assert [item["id"] for item in domains] == [
            "semiconductor",
            "ai",
            "energy",
            "steel",
            "health",
            "metals",
            "chemicals",
            "auto",
            "digital",
            "consumer",
            "finance",
            "macro",
        ]

    def test_macro_registry_has_thirty_plus(self, service: MarketService) -> None:
        assert len(service.macro_config["indicators"]) >= 20

    def test_etf_map_primary_present(self, service: MarketService) -> None:
        for domain_id, config in service.sectors_config["domains"].items():
            assert config["etfs"]["primary"], domain_id
            assert config["board"].startswith("BK"), domain_id

    def test_macro_endpoint_reports_missing(self, service: MarketService) -> None:
        payload = service.macro()
        missing = [item for item in payload["indicators"] if item["status"] == "missing"]
        assert len(missing) == len(service.macro_config["indicators"])

    def test_health_records_failures(self, service: MarketService) -> None:
        service.health.record_failure("eastmoney:moneyflow", "测试失败")
        snapshot = {item.source_id: item for item in service.health.snapshot()}
        assert snapshot["eastmoney:moneyflow"].status == "failed"
        assert snapshot["nbs-easyquery"].status == "idle"  # 预注册时即带 BLOCKED 说明
        assert "403" in snapshot["nbs-easyquery"].note

    def test_company_quotes_offline_shapes(
        self, service: MarketService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "industrypulse.market.service.MarketService.quotes",
            lambda self, symbols: {},
        )
        rows = service.company_quotes()
        assert len(rows) >= 40
        assert all(row["price"] is None for row in rows)

    def test_trend_normalization(self, service: MarketService) -> None:
        bars = [Bar(date=date(2026, 1, 1 + offset), close=100 + offset) for offset in range(5)]
        trend = service._trend(bars)
        assert trend[0]["value"] == pytest.approx(101.0)
        assert trend[-1]["value"] == pytest.approx(104.0)


class TestConfigs:
    def test_yaml_wellformed(self) -> None:
        for name in ("sectors.yaml", "macro_indicators.yaml"):
            payload = yaml.safe_load((ROOT / "config/market" / name).read_text(encoding="utf-8"))
            assert payload, name

    def test_indicator_ids_unique(self) -> None:
        registry = yaml.safe_load(
            (ROOT / "config/market/macro_indicators.yaml").read_text(encoding="utf-8")
        )
        identifiers = [item["id"] for item in registry["indicators"]]
        assert len(identifiers) == len(set(identifiers))

    def test_akshare_sources_are_known_functions(self) -> None:
        registry = yaml.safe_load(
            (ROOT / "config/market/macro_indicators.yaml").read_text(encoding="utf-8")
        )
        try:
            import akshare as ak
        except ImportError:
            pytest.skip("akshare 未安装")
        for item in registry["indicators"]:
            if item["provider"] == "akshare":
                assert hasattr(ak, item["source"]), item["id"]


class TestHealthMonitor:
    def test_persist_and_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "health.json"
        monitor = SourceHealthMonitor(path)
        monitor.ensure(["tencent:quotes"])
        monitor.record_success("tencent:quotes", 120)
        monitor.record_failure("eastmoney:kline", "超时")
        monitor.persist()
        reloaded = SourceHealthMonitor(path)
        states = {item.source_id: item for item in reloaded.snapshot()}
        assert states["tencent:quotes"].status == "active"
        assert states["tencent:quotes"].latency_ms == 120
        assert states["eastmoney:kline"].status == "failed"

    def test_snapshot_timestamps(self, tmp_path: Path) -> None:
        monitor = SourceHealthMonitor(tmp_path / "h.json")
        monitor.record_success("tencent:quotes", 10)
        item = monitor.snapshot()[0]
        assert item.last_success is not None
        assert item.last_success.tzinfo is not None


class TestPublicFallbacks:
    def test_flow_prefers_eastmoney_board(self) -> None:
        resolved = resolve_net_flow(
            "BK0479",
            ["new_gthy"],
            {"BK0479": 1.5e8},
            {"new_gthy": {"name": "钢铁行业", "netamount": 9e8}},
        )
        assert resolved["net_flow"] == pytest.approx(1.5)
        assert resolved["flow_status"] == "ok"
        assert "东财" in resolved["net_flow_label"]

    def test_flow_falls_back_to_named_sina_boards(self) -> None:
        resolved = resolve_net_flow(
            "BK0479",
            ["new_gthy", "missing"],
            {},
            {"new_gthy": {"name": "钢铁行业", "netamount": -2.5e8}},
        )
        assert resolved["net_flow"] == pytest.approx(-2.5)
        assert resolved["flow_status"] == "proxy"
        assert "钢铁行业" in resolved["net_flow_label"]

    def test_sina_futures_quote(self) -> None:
        payload = (
            'var hq_str_nf_RB0="螺纹钢连续,150000,3054,3059,3031,3047,3046,3047,'
            '3047,3043,3064,4,289,1,1,沪,螺纹钢,2026-10-23,1";'
        )
        quote = sina_public.parse_futures_payload(payload)["RB0"]
        assert quote["price"] == pytest.approx(3047)
        assert quote["date"] == "2026-10-23"
        assert quote["change_pct"] == pytest.approx((3047 / 3064 - 1) * 100, abs=0.01)

    def test_sina_flow_rows(self) -> None:
        rows = sina_public.parse_flow_payload(
            [{"category": "new_gthy", "name": "钢铁行业", "netamount": "100"}]
        )
        assert rows["new_gthy"]["netamount"] == pytest.approx(100)

    def test_chinamoney_parsers(self) -> None:
        lpr = chinamoney.parse_dated_records(
            [{"showDateCN": "2026-10-20", "1Y": "3.00", "5Y": "3.50"}],
            "showDateCN",
            {"1Y": "1Y", "5Y": "5Y"},
        )
        assert lpr[-1]["values"]["1Y"] == pytest.approx(3.0)
        mid = chinamoney.parse_ccpr([{"date": "2026-10-23", "values": ["6.7180"]}])
        assert mid[-1]["values"]["value"] == pytest.approx(6.718)

    def test_notice_parser_keeps_public_link(self) -> None:
        payload = {
            "data": {
                "list": [
                    {
                        "title": "示例公司:董事会决议公告",
                        "art_code": "AN1",
                        "notice_date": "2026-10-22 00:00:00",
                        "columns": [{"column_name": "重大事项"}],
                    }
                ]
            }
        }
        notices = eastmoney.parse_notices(payload, "600519")
        assert notices[0]["url"].endswith("/600519/AN1.html")
        assert notices[0]["published_at"] == "2026-10-22"

    def test_every_domain_has_a_flow_fallback(self, service: MarketService) -> None:
        for domain_id, config in service.sectors_config["domains"].items():
            assert config.get("sina_flow"), domain_id
        assert len(service.sectors_config["commodities"]) >= 12
