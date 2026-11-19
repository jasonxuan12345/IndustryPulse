"""免费公开硬异动探测器：不靠泛财经通稿堆砌，直抓先导硬数据与跨市场时差。

包含 6 条实测可用公开通道：
1. 东财全市场业绩预告簇（RPT_PUBLIC_OP_NEWPREDICT）
2. 巨潮资讯硬公告检索（中标/重大合同/回购/增持/停产）
3. 台湾证交所公开月度营收同比（TWSE OpenAPI）
4. 腾讯美股/港股映射 20 日动量（计算海外领先时差）
5. SEC EDGAR 链主 8-K 突发披露
6. OpenFDA 近期新药审批（映射医药生物）
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
import yaml

from industrypulse.core import TZ_SHANGHAI, replace_file

ROOT = Path(__file__).resolve().parents[3]
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) IndustryPulse/2.0"
_SEC_UA = os.getenv(
    "INDUSTRYPULSE_SEC_USER_AGENT", "IndustryPulse/2.0 (local-research-workbench@example.com)"
)


_FILING_BOILERPLATE = (
    "归属于上市公司股东",
    "上市公司",
    "股份有限公司",
    "金融机构",
    "专项贷款",
    "集中竞价",
    "法律意见",
    "股份回购",
    "股票",
    "股东",
)
_NON_FILING_FINANCE_KWS = {
    "上市",
    "股市",
    "a股",
    "美股",
    "ipo",
    "stock",
    "market",
    "equities",
    "etf",
    "nasdaq",
}
_CNINFO_NOISE_WORDS = (
    "法律意见",
    "独立意见",
    "核查意见",
    "提示性",
    "实施完成",
    "实施结果",
    "回购进展",
    "回购股份的进展",
    "回购股份比例达到",
    "进展公告",
)


def _match_sector(
    text: str, domains: list[dict[str, Any]], *, filing_mode: bool = False
) -> str | None:
    cleaned = text
    if filing_mode:
        for phrase in _FILING_BOILERPLATE:
            cleaned = cleaned.replace(phrase, " ")
    lowered = cleaned.lower()
    best_id: str | None = None
    best_hits = 0
    for domain in domains:
        is_finance = str(domain.get("id")) == "finance"
        hits = 0
        for kw in domain.get("keywords", []):
            low_kw = str(kw).lower()
            if filing_mode and is_finance and low_kw in _NON_FILING_FINANCE_KWS:
                continue
            if low_kw in lowered:
                hits += 1
        if hits > best_hits:
            best_hits = hits
            best_id = str(domain["id"])
    return best_id


def _load_domain_keywords(root: Path) -> list[dict[str, Any]]:
    path = root / "config/news_sources.yaml"
    if not path.exists():
        return []
    raw = cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))
    result: list[dict[str, Any]] = []
    for domain in raw.get("domains") or []:
        kws: list[str] = [str(domain.get("name") or "")]
        for ind in domain.get("industries") or []:
            kws.append(str(ind.get("name") or ""))
            kws.extend(str(k) for k in (ind.get("keywords") or []))
        result.append({"id": domain["id"], "name": domain["name"], "keywords": kws})
    return result


class SignalService:
    """采集、归类并缓存全网免费硬异动信号；单通道失败明确标注，不拖垮整体。"""

    def __init__(self, *, root: Path | None = None) -> None:
        base = root or Path(os.getenv("INDUSTRYPULSE_ROOT", str(ROOT)))
        self.root = base
        self.config_path = base / "config/signals.yaml"
        self.data_dir = Path(os.getenv("INDUSTRYPULSE_SIGNALS_DIR", str(base / "data/signals")))
        self.cache_path = self.data_dir / "anomalies.json"

    def _config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}
        return cast(dict[str, Any], yaml.safe_load(self.config_path.read_text(encoding="utf-8")))

    def load_cached(self) -> dict[str, Any]:
        if not self.cache_path.exists():
            return {
                "updated_at": None,
                "by_sector": {},
                "channels": {},
                "highlights": [],
            }
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return dict(data) if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {"updated_at": None, "by_sector": {}, "channels": {}, "highlights": []}

    def _save_cache(self, payload: dict[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.cache_path.with_name(f".{uuid4().hex}.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        replace_file(temp, self.cache_path)

    # ------------------------------------------------------------ 1. 业绩预告簇
    def fetch_earnings_surprises(
        self, client: httpx.Client, domains: list[dict[str, Any]], *, days: int = 21
    ) -> list[dict[str, Any]]:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        resp = client.get(
            url,
            params={
                "reportName": "RPT_PUBLIC_OP_NEWPREDICT",
                "columns": "ALL",
                "pageSize": "120",
                "sortColumns": "NOTICE_DATE",
                "sortTypes": "-1",
            },
        )
        resp.raise_for_status()
        rows = (resp.json().get("result") or {}).get("data") or []
        cutoff = (datetime.now(TZ_SHANGHAI) - timedelta(days=days)).date().isoformat()
        by_code: dict[str, tuple[tuple[int, float], dict[str, Any]]] = {}
        for row in rows:
            notice = str(row.get("NOTICE_DATE") or "")[:10]
            if notice and notice < cutoff:
                continue
            ptype = str(row.get("PREDICT_TYPE") or "")
            amp = row.get("ADD_AMP_LOWER")
            if ptype not in {"预增", "扭亏", "略增"}:
                continue
            amp_val = float(amp) if amp is not None else 0.0
            if amp is not None and amp_val < 30 and ptype != "扭亏":
                continue
            reason = str(row.get("CHANGE_REASON_EXPLAIN") or row.get("PREDICT_CONTENT") or "")
            name = str(row.get("SECURITY_NAME_ABBR") or "")
            code = str(row.get("SECURITY_CODE") or "")
            sector = _match_sector(f"{name} {reason}", domains, filing_mode=True)
            if not sector:
                continue
            metric = str(row.get("PREDICT_FINANCE") or "")
            is_net_profit = int(
                "净利润" in metric and "扣非" not in metric and "非经常性" not in metric
            )
            rank = (is_net_profit, amp_val)
            item = {
                "kind": "earnings_surprise",
                "tag": "业绩预增",
                "sector": sector,
                "title": f"{name}({code}) {ptype}" + (f" +{amp}%" if amp is not None else ""),
                "detail": reason[:200],
                "date": notice,
                "url": f"https://data.eastmoney.com/bbsj/yjyg/{code}.html" if code else "",
            }
            dedup_key = code or f"{name}:{notice}"
            prev = by_code.get(dedup_key)
            if prev is None or rank > prev[0]:
                by_code[dedup_key] = (rank, item)
        return [entry[1] for entry in by_code.values()]

    # ------------------------------------------------------------ 2. 巨潮硬公告
    def fetch_cninfo_anomalies(
        self,
        client: httpx.Client,
        cfg: dict[str, Any],
        domains: list[dict[str, Any]],
        *,
        days: int = 21,
    ) -> list[dict[str, Any]]:
        url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
        cutoff = (datetime.now(TZ_SHANGHAI) - timedelta(days=days)).date().isoformat()
        items: list[dict[str, Any]] = []
        seen_codes: set[str] = set()
        for entry in list(cfg.get("cninfo_keywords") or []):
            kw = str(entry.get("keyword") or "")
            tag = str(entry.get("tag") or kw)
            if not kw:
                continue
            resp = client.post(
                url,
                data={
                    "pageNum": "1",
                    "pageSize": "15",
                    "tabName": "fulltext",
                    "searchkey": kw,
                    "column": "szse",
                },
            )
            resp.raise_for_status()
            for ann in resp.json().get("announcements") or []:
                title = (
                    str(ann.get("announcementTitle") or "").replace("<em>", "").replace("</em>", "")
                )
                if not title or any(noise in title for noise in _CNINFO_NOISE_WORDS):
                    continue
                sec_name = str(ann.get("secName") or "")
                sec_code = str(ann.get("secCode") or "")
                dedup_key = sec_code or title
                if dedup_key in seen_codes:
                    continue
                ts_ms = ann.get("announcementTime")
                ann_date = (
                    datetime.fromtimestamp(float(ts_ms) / 1000, tz=TZ_SHANGHAI).date().isoformat()
                    if ts_ms
                    else ""
                )
                if ann_date and ann_date < cutoff:
                    continue
                sector = _match_sector(f"{sec_name} {title}", domains, filing_mode=True)
                if not sector:
                    continue
                seen_codes.add(dedup_key)
                adj = str(ann.get("adjunctUrl") or "")
                items.append(
                    {
                        "kind": "cninfo_announcement",
                        "tag": tag,
                        "sector": sector,
                        "title": f"{sec_name}({sec_code})：{title}",
                        "detail": title,
                        "date": ann_date,
                        "url": f"https://static.cninfo.com.cn/{adj}" if adj else "",
                    }
                )
        return items

    # ------------------------------------------------------------ 3. 台股月度营收
    def fetch_twse_revenue_pulse(
        self, client: httpx.Client, cfg: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        resp = client.get("https://openapi.twse.com.tw/v1/opendata/t187ap05_L")
        resp.raise_for_status()
        rows = resp.json()
        if not isinstance(rows, list):
            return {}
        ind_map = dict(cfg.get("twse_industry_map") or {})
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            sector = ind_map.get(str(row.get("產業別") or "").strip())
            if not sector:
                continue
            try:
                yoy = float(row.get("營業收入-去年同月增減(%)") or "")
            except (TypeError, ValueError):
                continue
            grouped.setdefault(sector, []).append(
                {
                    "name": str(row.get("公司名稱") or ""),
                    "code": str(row.get("公司代號") or ""),
                    "industry": str(row.get("產業別") or ""),
                    "period": str(row.get("資料年月") or ""),
                    "yoy": round(yoy, 1),
                }
            )
        pulse: dict[str, dict[str, Any]] = {}
        for sector, companies in grouped.items():
            yoy_vals = sorted(c["yoy"] for c in companies)
            median_yoy = round(yoy_vals[len(yoy_vals) // 2], 1)
            boom_count = sum(1 for v in yoy_vals if v >= 30.0)
            boom_ratio = round(boom_count / len(yoy_vals) * 100.0, 1)
            top = sorted(companies, key=lambda c: c["yoy"], reverse=True)[:3]
            pulse[sector] = {
                "company_count": len(companies),
                "median_yoy": median_yoy,
                "boom_ratio_pct": boom_ratio,
                "period": companies[0]["period"],
                "top_movers": top,
                "triggered": median_yoy >= 12.0 or boom_ratio >= 30.0,
            }
        return pulse

    # ------------------------------------------------------------ 4. 海外映射 20D 动量
    def _fetch_proxy_return_20d(self, client: httpx.Client, sym: str) -> float | None:
        is_us = sym.startswith("us")
        endpoint = (
            "https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get"
            if is_us
            else "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        )
        candidates = (
            [sym] if (not is_us or "." in sym) else [f"{sym}.OQ", f"{sym}.N", f"{sym}.AM", sym]
        )
        for cand in candidates:
            try:
                r = client.get(endpoint, params={"param": f"{cand},day,,,25,qfq"})
                r.raise_for_status()
                node = (r.json().get("data") or {}).get(cand) or {}
                day_rows = node.get("qfqday") or node.get("day") or []
                closes = [float(d[2]) for d in day_rows if len(d) >= 3 and float(d[2]) > 0]
                if len(closes) >= 21:
                    return round((closes[-1] / closes[-21] - 1.0) * 100.0, 2)
            except Exception:  # noqa: BLE001
                continue
        return None

    def fetch_overseas_leads(
        self, client: httpx.Client, cfg: dict[str, Any]
    ) -> dict[str, list[dict[str, Any]]]:
        proxies = dict(cfg.get("overseas_proxies") or {})
        symbols_seen: dict[str, float | None] = {}
        out: dict[str, list[dict[str, Any]]] = {}
        for sector, specs in proxies.items():
            sector_rows: list[dict[str, Any]] = []
            for spec in specs:
                sym = str(spec["symbol"])
                if sym not in symbols_seen:
                    symbols_seen[sym] = self._fetch_proxy_return_20d(client, sym)
                ret = symbols_seen[sym]
                if ret is not None:
                    sector_rows.append({"symbol": sym, "name": spec["name"], "return_20d": ret})
            if sector_rows:
                out[sector] = sector_rows
        return out

    # ------------------------------------------------------------ 5. SEC 链主 8-K & OpenFDA
    def fetch_sec_chain_events(
        self, client: httpx.Client, cfg: dict[str, Any]
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        cutoff = (datetime.now(TZ_SHANGHAI) - timedelta(days=21)).date().isoformat()
        for leader in list(cfg.get("sec_chain_leaders") or []):
            cik = str(leader.get("cik") or "").zfill(10)
            url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            resp = client.get(url, headers={"User-Agent": _SEC_UA})
            resp.raise_for_status()
            recent = (resp.json().get("filings") or {}).get("recent") or {}
            forms = recent.get("form") or []
            dates = recent.get("filingDate") or []
            descs = recent.get("primaryDocDescription") or []
            acc_nums = recent.get("accessionNumber") or []
            for idx, form in enumerate(forms[:25]):
                fdate = str(dates[idx]) if idx < len(dates) else ""
                if str(form) in {"8-K", "10-Q", "10-K"} and fdate >= cutoff:
                    acc = str(acc_nums[idx]).replace("-", "")
                    events.append(
                        {
                            "kind": "sec_chain_filing",
                            "tag": f"链主{form}",
                            "sector": str(leader["sector"]),
                            "title": f"{leader['name']} 披露 {form} ({fdate})",
                            "detail": str(descs[idx] if idx < len(descs) else form),
                            "date": fdate,
                            "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}",
                        }
                    )
                    break
        return events

    def fetch_fda_approvals(self, client: httpx.Client, *, days: int = 21) -> list[dict[str, Any]]:
        today = datetime.now(TZ_SHANGHAI).date()
        cutoff = today - timedelta(days=days)
        cutoff_compact = cutoff.strftime("%Y%m%d")
        today_compact = today.strftime("%Y%m%d")
        search = (
            "submissions.submission_status:AP "
            f"AND submissions.submission_status_date:[{cutoff_compact} TO {today_compact}]"
        )
        resp = client.get(
            "https://api.fda.gov/drug/drugsfda.json",
            params={"search": search, "limit": "10"},
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        ranked: list[tuple[tuple[int, str], dict[str, Any]]] = []
        for row in resp.json().get("results") or []:
            sponsor = str(row.get("sponsor_name") or "")
            products = row.get("products") or []
            brand = str(products[0].get("brand_name") or "") if products else ""
            recent_ap = [
                sub
                for sub in (row.get("submissions") or [])
                if str(sub.get("submission_status") or "") == "AP"
                and cutoff_compact <= str(sub.get("submission_status_date") or "") <= today_compact
            ]
            if not recent_ap or not (brand or sponsor):
                continue
            best_sub = max(
                recent_ap,
                key=lambda s: (
                    int(str(s.get("submission_type") or "") == "ORIG"),
                    str(s.get("submission_status_date") or ""),
                ),
            )
            raw_date = str(best_sub.get("submission_status_date") or "")
            sub_type = str(best_sub.get("submission_type") or "")
            fmt_date = (
                f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                if len(raw_date) == 8
                else raw_date
            )
            type_label = "首次获批(ORIG)" if sub_type == "ORIG" else f"审批更新({sub_type or 'AP'})"
            ranked.append(
                (
                    (int(sub_type == "ORIG"), raw_date),
                    {
                        "kind": "fda_approval",
                        "tag": "FDA审批",
                        "sector": "health",
                        "title": f"FDA {type_label}：{brand or sponsor}",
                        "detail": f"申请方：{sponsor}",
                        "date": fmt_date,
                        "url": "https://www.accessdata.fda.gov/scripts/cder/daf/",
                    },
                )
            )
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in ranked[:2]]

    # ------------------------------------------------------------ 汇总刷新
    def refresh(
        self, *, a_share_returns_20d: dict[str, float | None] | None = None
    ) -> dict[str, Any]:
        cfg = self._config()
        domains = _load_domain_keywords(self.root)
        channels: dict[str, dict[str, Any]] = {}
        by_sector: dict[str, dict[str, Any]] = {
            str(d["id"]): {
                "hard_signals": [],
                "twse_pulse": None,
                "overseas_proxies": [],
                "overseas_avg_20d": None,
                "overseas_lead_20d": None,
                "hard_signal_count": 0,
            }
            for d in domains
        }

        def ensure_sector(sid: str) -> dict[str, Any]:
            return by_sector.setdefault(
                sid,
                {
                    "hard_signals": [],
                    "twse_pulse": None,
                    "overseas_proxies": [],
                    "overseas_avg_20d": None,
                    "overseas_lead_20d": None,
                    "hard_signal_count": 0,
                },
            )

        with httpx.Client(timeout=15, headers={"User-Agent": _UA}, follow_redirects=True) as client:
            tasks: tuple[tuple[str, Callable[[], list[dict[str, Any]]]], ...] = (
                ("eastmoney_forecast", lambda: self.fetch_earnings_surprises(client, domains)),
                ("cninfo_ann", lambda: self.fetch_cninfo_anomalies(client, cfg, domains)),
                ("sec_chain", lambda: self.fetch_sec_chain_events(client, cfg)),
                ("openfda", lambda: self.fetch_fda_approvals(client)),
            )
            for key, fn in tasks:
                try:
                    rows = fn()
                    channels[key] = {"status": "ok", "count": len(rows)}
                    for item in rows:
                        bucket = ensure_sector(str(item["sector"]))
                        if len(bucket["hard_signals"]) < 8:
                            bucket["hard_signals"].append(item)
                except Exception as exc:  # noqa: BLE001
                    channels[key] = {
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {str(exc)[:100]}",
                    }

            try:
                twse = self.fetch_twse_revenue_pulse(client, cfg)
                channels["twse_monthly_rev"] = {"status": "ok", "count": len(twse)}
                for sid, info in twse.items():
                    bucket = ensure_sector(sid)
                    bucket["twse_pulse"] = info
                    if info.get("triggered"):
                        top_str = "、".join(
                            f"{m['name']}({m['yoy']:+.0f}%)" for m in info["top_movers"][:2]
                        )
                        bucket["hard_signals"].append(
                            {
                                "kind": "twse_monthly_revenue",
                                "tag": "台股月度营收加速",
                                "sector": sid,
                                "title": (
                                    f"台股同产业链 {info['period']} "
                                    f"月营收中位同比 {info['median_yoy']:+.1f}%"
                                ),
                                "detail": (
                                    f"高增(>30%)占比 {info['boom_ratio_pct']}%；代表：{top_str}"
                                ),
                                "date": info["period"],
                                "url": "https://openapi.twse.com.tw/",
                            }
                        )
            except Exception as exc:  # noqa: BLE001
                channels["twse_monthly_rev"] = {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {str(exc)[:100]}",
                }

            try:
                overseas = self.fetch_overseas_leads(client, cfg)
                channels["overseas_proxies"] = {"status": "ok", "count": len(overseas)}
                for sid, proxies in overseas.items():
                    bucket = ensure_sector(sid)
                    bucket["overseas_proxies"] = proxies
                    avg_overseas = round(sum(p["return_20d"] for p in proxies) / len(proxies), 2)
                    a_ret = (a_share_returns_20d or {}).get(sid)
                    lead = round(avg_overseas - a_ret, 2) if a_ret is not None else None
                    bucket["overseas_avg_20d"] = avg_overseas
                    bucket["overseas_lead_20d"] = lead
                    if avg_overseas >= 6.0 and (lead is None or lead >= 6.0):
                        names = "、".join(
                            f"{p['name']}({p['return_20d']:+.1f}%)" for p in proxies[:2]
                        )
                        detail_text = (
                            f"海外平均 {avg_overseas:+.1f}% vs A股 {a_ret:+.1f}%，时差 {lead:+.1f}%"
                            if lead is not None
                            else f"海外平均 {avg_overseas:+.1f}%"
                        )
                        bucket["hard_signals"].append(
                            {
                                "kind": "overseas_lead",
                                "tag": "海外映射显著领先",
                                "sector": sid,
                                "title": f"海外映射近20日走强 ({names})",
                                "detail": detail_text,
                                "date": datetime.now(TZ_SHANGHAI).date().isoformat(),
                                "url": "",
                            }
                        )
            except Exception as exc:  # noqa: BLE001
                channels["overseas_proxies"] = {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {str(exc)[:100]}",
                }

        highlights: list[dict[str, Any]] = []
        for bucket in by_sector.values():
            bucket["hard_signal_count"] = len(bucket["hard_signals"])
            for sig in bucket["hard_signals"][:2]:
                highlights.append(sig)

        result = {
            "updated_at": datetime.now(TZ_SHANGHAI).isoformat(),
            "channels": channels,
            "by_sector": by_sector,
            "highlights": highlights[:20],
        }
        self._save_cache(result)
        return result
