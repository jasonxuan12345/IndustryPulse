"""Archived cross-source checks. An empty or failed page never proves no dividend."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import httpx
import yaml

from industrypulse.services import ledger
from industrypulse.services.etf_audit import audit_price_lines
from industrypulse.services.workbench import write_json

if TYPE_CHECKING:
    from industrypulse.services.workbench import Workbench


def archive(service: Workbench, response: httpx.Response, now: datetime) -> dict[str, Any]:
    response.raise_for_status()
    if len(response.content) < 100:
        raise ValueError("空响应或挑战页，不能作为数据证据")
    sha = hashlib.sha256(response.content).hexdigest()
    path = service.root / "data/baseline/raw" / sha
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        raise ValueError("原件指纹被改写")
    if not path.exists():
        path.write_bytes(response.content)
    return {
        "url": str(response.url),
        "retrieved_at": now.isoformat(),
        "sha256": sha,
        "path": str(path.relative_to(service.root)),
    }


def clean(content: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", "", content, flags=re.S | re.I)),
    )


def decode(response: httpx.Response) -> str:
    declared = re.search(rb'charset\s*=\s*["\x27]?([\w-]+)', response.content[:2048], re.I)
    if declared and declared[1].lower().startswith(b"gb"):
        return response.content.decode("gb18030", errors="replace")
    return response.content.decode("utf-8", errors="replace")


def parse_sina_dividends(text: str, code: str) -> list[dict[str, Any]]:
    plain = clean(text)
    if code not in plain or "历史分红" not in plain:
        raise ValueError("新浪页面未提供该基金的历史分红表")
    body = text.split("历史分红", 1)[1].split("合计", 1)[0]
    rows = []
    for raw_row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", body, flags=re.S | re.I):
        match = re.fullmatch(
            r"\s*(\d{4}/\d{1,2}/\d{1,2})\s+(\d{4}/\d{1,2}/\d{1,2})\s+([\d.]+)\s*", clean(raw_row)
        )
        if match and Decimal(match[3]) > 0 and not match[2].startswith("1970/"):
            rows.append(match.groups())
    if not rows:
        raise ValueError("历史表为空，不能推断从未分红")
    return [
        {
            "record_day": datetime.strptime(a, "%Y/%m/%d").date().isoformat(),
            "pay_day": datetime.strptime(b, "%Y/%m/%d").date().isoformat(),
            "per_unit": str(Decimal(c)),
        }
        for a, b, c in rows
    ]


def parse_eastmoney(text: str, code: str) -> dict[str, Any]:
    from industrypulse.datahub.fund_dividend import parse_dividend_table

    text_clean = clean(text)
    if code not in text_clean or "分红送配详情" not in text_clean:
        raise ValueError("没有识别到指定基金的分红详情表，可能为挑战页")
    rows = parse_dividend_table(text)
    if not rows and "暂无分红信息" not in text_clean:
        raise ValueError("未识别到分红行或明确无分红声明")
    return {
        "records": [{"ex_date": r.ex_date.isoformat(), "per_unit": str(r.per_unit)} for r in rows],
        "explicit_empty": not rows and "暂无分红信息" in text_clean,
    }


def run(
    service: Workbench, *, now: datetime | None = None, refresh: bool = False
) -> dict[str, Any]:
    from industrypulse.repositories import DuckDBMarketRepository

    moment = ledger._moment(now)
    market = DuckDBMarketRepository(service.path)
    mapping = yaml.safe_load(
        (service.root / "config/market/industry_price_lines.yaml").read_text(encoding="utf-8")
    )
    report_path = service.root / "data/baseline/latest.json"
    previous = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    checks = previous.get("assets", {})
    documents = previous.get("corporate_documents", [])
    for document in documents:
        original = service.root / document.get("path", "missing-proof")
        if document.get("verified") and (
            not original.is_file()
            or hashlib.sha256(original.read_bytes()).hexdigest() != document.get("sha256")
        ):
            document.update(verified=False, error="公司行动原件缺失或指纹不符，撤销证据资格")
    if refresh:
        specs = {s["asset_id"]: s for s in [mapping["benchmark"], *mapping["industries"].values()]}
        with httpx.Client(
            timeout=20, follow_redirects=True, headers={"User-Agent": "IndustryPulse/research"}
        ) as client:
            for aid in specs:
                code = aid.split("_")[1]
                item: dict[str, Any] = {
                    "asset_id": aid,
                    "checked_at": moment.isoformat(),
                    "sources": [],
                    "errors": [],
                }
                try:
                    response = client.get(f"https://fundf10.eastmoney.com/fhsp_{code}.html")
                    proof = archive(service, response, moment)
                    east = parse_eastmoney(decode(response), code)
                    item["eastmoney"] = east
                    item["sources"].append(proof)
                except (ValueError, httpx.HTTPError, OSError) as exc:
                    item["errors"].append(str(exc)[:200])
                try:
                    response = client.get(
                        f"https://stock.finance.sina.com.cn/fundInfo/view/FundInfo_JJFH.php?symbol={code}"
                    )
                    proof = archive(service, response, moment)
                    sina = parse_sina_dividends(decode(response), code)
                    item["sina"] = sina
                    item["sources"].append(proof)
                except (ValueError, httpx.HTTPError, OSError) as exc:
                    item["errors"].append(str(exc)[:200])
                    try:
                        response = client.get(
                            f"https://finance.sina.com.cn/fund/quotes/{code}/bc.shtml"
                        )
                        proof = archive(service, response, moment)
                        summary = clean(decode(response))
                        total = re.search(r"累计分红[：:]\s*([\d.]+)\s*元", summary)
                        if code not in summary or total is None:
                            raise ValueError("备用新浪概况页未给出该基金明确的累计分红")
                        item["sina_cumulative_per_unit"] = str(Decimal(total[1]))
                        item["sources"].append(proof)
                    except (ValueError, httpx.HTTPError, OSError) as exc:
                        item["errors"].append(str(exc)[:200])
                # Reconcile independent provider amounts by record date, not payment/ex-date.
                acts = market.actions(aid)
                dates_and_amounts = {
                    (a.ex_date.isoformat(), str(a.distribution_per_unit.normalize()))
                    for a in acts
                    if a.action_type == "cash_dividend"
                }
                east_rows = item.get("eastmoney", {}).get("records", [])
                same_local = {
                    (r["ex_date"], str(Decimal(r["per_unit"]).normalize())) for r in east_rows
                } == dates_and_amounts
                independent = item.get("sina", [])
                matches = len(east_rows) == len(independent) and all(
                    Decimal(a["per_unit"]) == Decimal(b["per_unit"])
                    and a["ex_date"] <= b["pay_day"]
                    and b["record_day"] <= a["ex_date"]
                    for a, b in zip(
                        east_rows, sorted(independent, key=lambda r: r["record_day"]), strict=False
                    )
                )
                empty_checked = (
                    item.get("eastmoney", {}).get("explicit_empty")
                    and item.get("sina_cumulative_per_unit") == "0"
                    and not dates_and_amounts
                )
                item["status"] = (
                    "cross_checked"
                    if empty_checked or (east_rows and independent and matches and same_local)
                    else "unresolved"
                )
                item["note"] = "第三方交叉核对，非官方完整性证明；空页不视为零分红"
                checks[aid] = item
            # PDFs already captured by the explicit verification script are retained;
            # network failures are never converted into verified notices.
    audit = audit_price_lines(
        service.root / "config/market/industry_price_lines.yaml", market, as_of=moment
    )
    classified = {(e["asset_id"], e["price_day"]): e for e in documents if e.get("verified")}
    gaps = []
    audits = {audit["benchmark"]["asset_id"]: audit["benchmark"]}
    audits.update({v["asset_id"]: v["audit"] for v in audit["industries"].values()})
    for aid, item in audits.items():
        for barrier in item["suspected_splits"]:
            gaps.append(
                {
                    "asset_id": aid,
                    **barrier,
                    "classification": classified.get((aid, barrier["day"])),
                    "execution": "跨越公司行动窗口继续隔离，直至引擎支持份额处理",
                }
            )
    result = {
        "as_of": moment.isoformat(),
        "assets": checks,
        "corporate_documents": documents,
        "audit": audit,
        "price_barriers": gaps,
        "unclassified_gaps": sum(not e["classification"] for e in gaps),
        "dividend_unresolved": [
            aid for aid in audits if checks.get(aid, {}).get("status") != "cross_checked"
        ],
        "pit_mode": "reconstructed_pit",
        "strict_contemporary_history": False,
        "return_conventions": {
            "event_forecast_T3": "63交易日、次日开盘、双边费用、现金分红",
            "legacy_price_check": "收盘到收盘、零费用；仅作历史研究",
            "ledger": "实际登记后开盘、3/6日历月、双边费用、分红加回",
        },
    }
    write_json(report_path, result)
    return result


def asset_ready(service: Workbench, aid: str, now: datetime) -> tuple[bool, str]:
    path = service.root / "data/baseline/latest.json"
    if not path.exists():
        return False, "尚未生成分红独立核对证据"
    item = json.loads(path.read_text(encoding="utf-8")).get("assets", {}).get(aid, {})
    if item.get("status") != "cross_checked":
        return False, "分红独立来源尚未核验完整"
    checked = datetime.fromisoformat(item["checked_at"])
    if checked > now:
        return False, "核对时间晚于研究时点"
    if (now - checked).total_seconds() > 7 * 86400:
        return False, "分红核对超过7天，需刷新"
    for proof in item.get("sources", []):
        raw = service.root / proof["path"]
        if not raw.exists() or hashlib.sha256(raw.read_bytes()).hexdigest() != proof["sha256"]:
            return False, "独立来源原件缺失或指纹不符"
    if len(item.get("sources", [])) < 2:
        return False, "缺少两个独立来源的原件"
    return True, "两来源分红金额一致且原件完整（第三方证据）"
