"""基金分红送配解析：天天基金 F10 页 -> CorporateAction。

来源：``https://fund.eastmoney.com/f10/FHSP_{code}.html``（第三方聚合，C 级证据）。
已与「前复权/不复权比值反推」做过交叉验证：515210 的 2024-12-24、2025-12-24
两次除息在两套独立方法下一致。

页面上「每10份派现金0.6000元」需折算为每份 0.06 元，写入 ``distribution_per_unit``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from industrypulse.contracts.domain import CorporateAction

#: 权益登记日 / 除息日 / 每10份派现金X元 / 分红发放日
_ROW = re.compile(r"(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})\s+每10份派现金\s*([\d.]+)\s*元")


class DividendParseError(ValueError):
    """页面结构不符合预期；拒绝猜测。"""


@dataclass(frozen=True)
class DividendRecord:
    ex_date: date
    per_unit: Decimal


def parse_dividend_table(html: str) -> list[DividendRecord]:
    """解析分红送配表；按除息日升序去重返回。"""
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"\s+", " ", text)
    records: dict[date, Decimal] = {}
    for _record_date, ex_text, amount_text in _ROW.findall(text):
        try:
            ex_date = date.fromisoformat(ex_text)
            per_ten = Decimal(amount_text)
        except (ValueError, InvalidOperation) as exc:
            raise DividendParseError(f"分红行无法解析：{exc}") from exc
        if per_ten <= 0:
            raise DividendParseError("每10份分红必须为正")
        per_unit = per_ten / Decimal(10)
        previous = records.get(ex_date)
        if previous is not None and previous != per_unit:
            raise DividendParseError(f"{ex_date} 出现冲突的分红金额")
        records[ex_date] = per_unit
    return [DividendRecord(ex_date=day, per_unit=amount) for day, amount in sorted(records.items())]


def to_corporate_actions(
    records: list[DividendRecord], *, asset_id: str, source_note: str
) -> list[CorporateAction]:
    return [
        CorporateAction(
            asset_id=asset_id,
            ex_date=record.ex_date,
            action_type="cash_dividend",
            distribution_per_unit=record.per_unit,
            notes=source_note,
        )
        for record in records
    ]


__all__ = [
    "DividendParseError",
    "DividendRecord",
    "parse_dividend_table",
    "to_corporate_actions",
]
