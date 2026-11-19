from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx

from industrypulse.core import TZ_SHANGHAI
from industrypulse.signals.anomalies import SignalService


def test_signal_service_collects_and_degrades_gracefully(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INDUSTRYPULSE_SIGNALS_DIR", str(tmp_path / "signals"))
    today_iso = datetime.now(TZ_SHANGHAI).date().isoformat()
    today_ms = int(datetime.now(TZ_SHANGHAI).timestamp() * 1000)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "datacenter-web.eastmoney.com" in url:
            return httpx.Response(
                200,
                json={
                    "result": {
                        "data": [
                            {
                                "SECURITY_CODE": "300308",
                                "SECURITY_NAME_ABBR": "光模块示例",
                                "NOTICE_DATE": f"{today_iso} 00:00:00",
                                "PREDICT_TYPE": "预增",
                                "ADD_AMP_LOWER": 120.0,
                                "CHANGE_REASON_EXPLAIN": "算力光模块需求放量带动扩产",
                            }
                        ]
                    }
                },
            )
        if "cninfo.com.cn" in url:
            return httpx.Response(
                200,
                json={
                    "announcements": [
                        {
                            "secCode": "002371",
                            "secName": "半导体设备",
                            "announcementTitle": "关于签订芯片设备重大中标合同的公告",
                            "announcementTime": today_ms,
                            "adjunctUrl": "finalpage/test.pdf",
                        }
                    ]
                },
            )
        if "openapi.twse.com.tw" in url:
            return httpx.Response(
                200,
                json=[
                    {
                        "資料年月": "11510",
                        "公司代號": "2330",
                        "公司名稱": "台積電",
                        "產業別": "半導體業",
                        "營業收入-去年同月增減(%)": "35.5",
                    }
                ],
            )
        if "web.ifzq.gtimg.cn" in url:
            bars = [
                ["2026-10-01", "100", str(100.0 + i * 0.6), "105", "99", "1000"] for i in range(25)
            ]
            sym = str(request.url.params.get("param", "")).split(",")[0]
            return httpx.Response(200, json={"code": 0, "data": {sym: {"day": bars}}})
        if "data.sec.gov" in url:
            return httpx.Response(
                200,
                json={
                    "filings": {
                        "recent": {
                            "form": ["8-K"],
                            "filingDate": [today_iso],
                            "primaryDocDescription": ["8-K"],
                            "accessionNumber": ["0001045810-26-000001"],
                        }
                    }
                },
            )
        # 故意让 OpenFDA 失败，验证单通道失败不影响其余 5 条通道
        return httpx.Response(503, text="down")

    orig = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: orig(**kw, transport=httpx.MockTransport(handler))
    )

    svc = SignalService()
    res = svc.refresh(a_share_returns_20d={"semiconductor": -2.0})
    assert res["channels"]["eastmoney_forecast"]["status"] == "ok"
    assert res["channels"]["cninfo_ann"]["status"] == "ok"
    assert res["channels"]["twse_monthly_rev"]["status"] == "ok"
    assert res["channels"]["overseas_proxies"]["status"] == "ok"
    assert res["channels"]["sec_chain"]["status"] == "ok"
    assert res["channels"]["openfda"]["status"] == "failed"

    semi = res["by_sector"]["semiconductor"]
    assert semi["twse_pulse"]["triggered"] is True
    assert semi["overseas_lead_20d"] > 10.0
    assert semi["hard_signal_count"] >= 3
    assert json.loads(svc.cache_path.read_text(encoding="utf-8"))["updated_at"] == res["updated_at"]


def test_signal_service_hygiene_filters_stale_duplicates_and_boilerplate(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("INDUSTRYPULSE_SIGNALS_DIR", str(tmp_path / "signals"))
    now = datetime.now(TZ_SHANGHAI)
    today_iso = now.date().isoformat()
    today_compact = now.date().strftime("%Y%m%d")
    stale_ms = int((now - timedelta(days=40)).timestamp() * 1000)
    today_ms = int(now.timestamp() * 1000)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "datacenter-web.eastmoney.com" in url:
            return httpx.Response(
                200,
                json={
                    "result": {
                        "data": [
                            {
                                "SECURITY_CODE": "301661",
                                "SECURITY_NAME_ABBR": "光模块设备",
                                "NOTICE_DATE": f"{today_iso} 00:00:00",
                                "PREDICT_TYPE": "预增",
                                "PREDICT_FINANCE": "归属于上市公司股东的净利润",
                                "ADD_AMP_LOWER": 157.9,
                                "CHANGE_REASON_EXPLAIN": "光模块需求放量",
                            },
                            {
                                "SECURITY_CODE": "301661",
                                "SECURITY_NAME_ABBR": "光模块设备",
                                "NOTICE_DATE": f"{today_iso} 00:00:00",
                                "PREDICT_TYPE": "预增",
                                "PREDICT_FINANCE": "扣除非经常性损益后的净利润",
                                "ADD_AMP_LOWER": 239.7,
                                "CHANGE_REASON_EXPLAIN": "光模块需求放量",
                            },
                            {
                                "SECURITY_CODE": "688827",
                                "SECURITY_NAME_ABBR": "精密机床",
                                "NOTICE_DATE": f"{today_iso} 00:00:00",
                                "PREDICT_TYPE": "预增",
                                "PREDICT_FINANCE": "归属于上市公司股东的净利润",
                                "ADD_AMP_LOWER": 37.6,
                                "CHANGE_REASON_EXPLAIN": "预计归属于上市公司股东的净利润同比增长",
                            },
                        ]
                    }
                },
            )
        if "cninfo.com.cn" in url:
            return httpx.Response(
                200,
                json={
                    "announcements": [
                        {
                            "secCode": "301522",
                            "secName": "高温合金",
                            "announcementTitle": "控股股东取得金融机构增持专项贷款承诺函的公告",
                            "announcementTime": today_ms,
                            "adjunctUrl": "finalpage/a.pdf",
                        },
                        {
                            "secCode": "688105",
                            "secName": "某生物",
                            "announcementTitle": "关于控股股东增持股份的法律意见",
                            "announcementTime": today_ms,
                            "adjunctUrl": "finalpage/b.pdf",
                        },
                        {
                            "secCode": "600309",
                            "secName": "某化工",
                            "announcementTitle": "关于化工装置停产检修公告",
                            "announcementTime": stale_ms,
                            "adjunctUrl": "finalpage/c.pdf",
                        },
                    ]
                },
            )
        if "openapi.twse.com.tw" in url:
            return httpx.Response(200, json=[])
        if "web.ifzq.gtimg.cn" in url:
            bars = [
                ["2026-10-01", "100", str(100.0 + i * 0.5), "105", "99", "1000"] for i in range(25)
            ]
            sym = str(request.url.params.get("param", "")).split(",")[0]
            key = "qfqday" if sym.startswith("us") else "day"
            return httpx.Response(200, json={"code": 0, "data": {sym: {key: bars}}})
        if "data.sec.gov" in url:
            return httpx.Response(200, json={"filings": {"recent": {}}})
        if "api.fda.gov" in url:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "sponsor_name": "TEST PHARMA",
                            "products": [{"brand_name": "OLDDRUG"}],
                            "submissions": [
                                {
                                    "submission_type": "ORIG",
                                    "submission_status": "AP",
                                    "submission_status_date": "20160721",
                                }
                            ],
                        },
                        {
                            "sponsor_name": "PFIZER",
                            "products": [{"brand_name": "NEWDRUG"}],
                            "submissions": [
                                {
                                    "submission_type": "SUPPL",
                                    "submission_status": "AP",
                                    "submission_status_date": "20240101",
                                },
                                {
                                    "submission_type": "ORIG",
                                    "submission_status": "AP",
                                    "submission_status_date": today_compact,
                                },
                            ],
                        },
                    ]
                },
            )
        return httpx.Response(404)

    orig = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: orig(**kw, transport=httpx.MockTransport(handler))
    )

    svc = SignalService()
    res = svc.refresh(a_share_returns_20d={"semiconductor": -2.0})
    assert res["channels"]["eastmoney_forecast"] == {"status": "ok", "count": 1}
    assert res["channels"]["cninfo_ann"] == {"status": "ok", "count": 0}
    assert res["channels"]["openfda"] == {"status": "ok", "count": 1}
    assert [
        s for s in res["by_sector"]["finance"]["hard_signals"] if s["kind"] != "overseas_lead"
    ] == []
    digital_earnings = [
        s for s in res["by_sector"]["digital"]["hard_signals"] if s["kind"] == "earnings_surprise"
    ]
    assert len(digital_earnings) == 1
    assert "+157.9%" in digital_earnings[0]["title"]
    assert "NEWDRUG" in res["by_sector"]["health"]["hard_signals"][0]["title"]
    assert res["by_sector"]["semiconductor"]["overseas_avg_20d"] is not None
    assert len(res["by_sector"]["semiconductor"]["overseas_proxies"]) == 3
