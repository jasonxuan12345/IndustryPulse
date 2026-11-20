"""Read the two latest consecutive 13F reporting periods from EDGAR."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any
from xml.etree import ElementTree as ET

FORMS = {"13F-HR", "13F-HR/A"}
_DOCUMENT = re.compile(r"<DOCUMENT>(.*?)</DOCUMENT>", re.DOTALL | re.IGNORECASE)
_XML = re.compile(r"<XML>\s*(.*?)\s*</XML>", re.DOTALL | re.IGNORECASE)


def recent_filings(cik: str, recent: dict[str, list[Any]]) -> list[dict[str, Any]]:
    filings: dict[str, dict[str, Any]] = {}
    for index, form in enumerate(recent.get("form", [])):
        if form not in FORMS:
            continue
        accession = recent["accessionNumber"][index]
        period = date.fromisoformat(recent["reportDate"][index]).isoformat()
        if not isinstance(accession, str) or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
            raise ValueError("SEC 申报编号格式无效")
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"
        accepted = recent.get("acceptanceDateTime") or []
        filings.setdefault(
            accession,
            {
                "form": form,
                "accessionNumber": accession,
                "filing_date": recent["filingDate"][index],
                "accepted_at": accepted[index] if index < len(accepted) else None,
                "report_date": period,
                "url": f"{base}/{accession}-index.htm",
                "submission_url": f"{base}/{accession}.txt",
            },
        )
    return list(filings.values())  # SEC returns newest disclosure first.


def previous_quarter(period: str) -> str:
    end = date.fromisoformat(period)
    return (date(end.year, (end.month - 1) // 3 * 3 + 1, 1) - timedelta(days=1)).isoformat()


def parse_submission(text: str, form: str) -> dict[str, Any]:
    """Find XML by the EDGAR document type in the complete submission."""
    documents: dict[str, list[tuple[str, ET.Element]]] = {}
    for body in _DOCUMENT.findall(text):
        doc_type = re.search(r"^<TYPE>\s*([^\r\n<]+)", body, re.MULTILINE | re.IGNORECASE)
        if not doc_type or doc_type[1].strip().upper() not in {form, "INFORMATION TABLE"}:
            continue
        xml = _XML.search(body)
        file_match = re.search(r"^<FILENAME>\s*([^\r\n<]+)", body, re.MULTILINE | re.IGNORECASE)
        if not xml or not file_match:
            continue
        if re.search(r"<!\s*(DTD|DOCTYPE|ENTITY)\b", xml[1], re.IGNORECASE):
            raise ValueError("13F XML 含不支持的声明")
        documents.setdefault(doc_type[1].strip().upper(), []).append(
            (file_match[1].strip(), ET.fromstring(xml[1]))
        )
    if len(documents.get(form, [])) != 1 or not documents.get("INFORMATION TABLE"):
        raise ValueError("申报缺少封面 XML 或 INFORMATION TABLE XML")

    cover = documents[form][0][1]
    amendment = (cover.findtext(".//{*}amendmentType") or "").strip().upper()
    if form.endswith("/A") and amendment not in {"RESTATEMENT", "NEW HOLDINGS"}:
        raise ValueError("13F 修订类型无法确认")
    holdings = []
    filenames = []
    for filename, table in documents["INFORMATION TABLE"]:
        if table.tag.rsplit("}", 1)[-1] != "informationTable":
            raise ValueError("持仓 XML 根节点无效")
        filenames.append(filename)
        for row in table.findall("./{*}infoTable"):
            cusip = (row.findtext("{*}cusip") or "").strip().upper()
            unit = (row.findtext("{*}shrsOrPrnAmt/{*}sshPrnamtType") or "").strip().upper()
            option = (row.findtext("{*}putCall") or "NONE").strip().upper()
            quantity = int(row.findtext("{*}shrsOrPrnAmt/{*}sshPrnamt") or "-1")
            value = int(row.findtext("{*}value") or "-1")
            if not cusip or unit not in {"SH", "PRN"} or option not in {"NONE", "PUT", "CALL"}:
                raise ValueError("持仓证券标识、数量单位或期权类型无效")
            if min(quantity, value) < 0:
                raise ValueError("持仓数量或市值无效")
            holdings.append(
                {
                    "cusip": cusip,
                    "issuer": (row.findtext("{*}nameOfIssuer") or "").strip(),
                    "unit": unit,
                    "option": option,
                    "quantity": quantity,
                    "value": value,
                    "other_manager": (row.findtext("{*}otherManager") or "").strip(),
                }
            )
    total_rows = cover.findtext(".//{*}tableEntryTotal")
    if total_rows is not None and int(total_rows) != len(holdings):
        raise ValueError("持仓明细行数与申报汇总不符")
    if not holdings and total_rows != "0":
        raise ValueError("13F 持仓表无可读记录")
    return {
        "amendment": amendment or None,
        "confidential_omitted": (
            (cover.findtext(".//{*}isConfidentialOmitted") or "").strip().lower() in {"true", "1"}
        ),
        "table_files": filenames,
        "holdings": holdings,
    }


def quarter_holdings(
    filings: list[dict[str, Any]], load: Callable[[dict[str, Any]], dict[str, Any]]
) -> dict[str, Any]:
    positions: dict[tuple[str, str, str], dict[str, Any]] = {}
    versions = []
    confidential = False
    has_base = False
    for filing in reversed(filings):
        doc = load(filing)
        amendment = doc["amendment"]
        if amendment != "NEW HOLDINGS":
            positions.clear()
            confidential = False
            has_base = True
        if not has_base:
            raise ValueError("追加申报缺少同一季度的原始申报")
        confidential |= doc["confidential_omitted"]
        versions.append({**filing, "amendment": amendment, "table_files": doc["table_files"]})
        for row in doc["holdings"]:
            key = (row["cusip"], row["option"], row["unit"])
            position = positions.setdefault(
                key, {**row, "quantity": 0, "value": 0, "other_managers": [], "sources": []}
            )
            position["quantity"] += row["quantity"]
            position["value"] += row["value"]
            if row["other_manager"] and row["other_manager"] not in position["other_managers"]:
                position["other_managers"].append(row["other_manager"])
            source = {
                "accessionNumber": filing["accessionNumber"],
                "filing_date": filing["filing_date"],
                "url": filing["url"],
            }
            if source not in position["sources"]:
                position["sources"].append(source)
    return {"positions": positions, "versions": versions, "confidential_omitted": confidential}


def _change(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    prior_confidential: bool,
    unit_changed: bool,
) -> tuple[str, str | None]:
    if after is None:
        return "NOT_DISCLOSED", None
    if unit_changed:
        return "REVIEW", "同一证券的数量单位发生变化"
    if before is None:
        if prior_confidential:
            return "REVIEW", "上期可能存在保密持仓，首次披露待核实"
        return "NEW", None
    old_q, new_q = before["quantity"], after["quantity"]
    old_v, new_v = before["value"], after["value"]
    if min(old_q, new_q, old_v, new_v) > 0:
        ratio = max(new_q, old_q) / min(new_q, old_q)
        near_split = ratio >= 1.9 and abs(ratio - round(ratio)) / round(ratio) <= 0.04
        stable_value = 0.75 <= new_v / old_v <= 1.25
        if near_split and stable_value:
            return "REVIEW", "股数接近整倍变化，市值相对稳定，疑似拆股或口径变动"
    direction = "INCREASE" if new_q > old_q else "REDUCE" if new_q < old_q else "HOLD"
    return direction, None


def compare_holdings(
    prior: dict[str, Any], current: dict[str, Any], issuers: dict[str, dict[str, str]]
) -> list[dict[str, Any]]:
    old, new = prior["positions"], current["positions"]
    total_old = sum(item["value"] for item in old.values())
    total_new = sum(item["value"] for item in new.values())
    rows = []
    for cusip, option, unit in sorted(old.keys() | new.keys()):
        before, after = old.get((cusip, option, unit)), new.get((cusip, option, unit))
        representative = after or before
        mapping = issuers.get(cusip, {})
        status, reason = _change(
            before,
            after,
            prior_confidential=prior["confidential_omitted"],
            unit_changed=(cusip, option, "PRN" if unit == "SH" else "SH") in old and before is None,
        )
        before_weight = (
            round(before["value"] / total_old * 100, 4) if before and total_old else None
        )
        after_weight = round(after["value"] / total_new * 100, 4) if after and total_new else None
        if before is None and status == "NEW" and total_old:
            before_weight = 0.0
        rows.append(
            {
                "cusip": cusip,
                "issuer": representative["issuer"],
                "ticker": mapping.get("ticker"),
                "sector": mapping.get("sector"),
                "option": option,
                "unit": unit,
                "status": status,
                "review_reason": reason,
                "previous_quantity": before["quantity"] if before else None,
                "quantity": after["quantity"] if after else None,
                "quantity_change": after["quantity"] - before["quantity"]
                if before and after and status != "REVIEW"
                else None,
                "previous_weight_pct": before_weight,
                "weight_pct": after_weight,
                "weight_change_pp": round(after_weight - before_weight, 4)
                if before_weight is not None and after_weight is not None and status != "REVIEW"
                else None,
                "other_managers": after["other_managers"] if after else [],
                "previous_other_managers": before["other_managers"] if before else [],
                "sources": after["sources"] if after else [],
                "previous_sources": before["sources"] if before else [],
            }
        )
    return rows
