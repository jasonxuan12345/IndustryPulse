"""ETF / 指数行情仓储：不可变 bar、资产主档、公司行动。

与 ``store.py`` 的观测仓储分开：行情是 (asset_id, trade_date) 的稠密面板，
而观测是 (series_id, period, vintage_id) 的版本化标量序列，二者语义不同。

纪律与观测仓储一致：
- bar 一旦入库不可改写；同键不同内容 -> 拒绝（修订应另立资产/版本，不允许静默覆盖）。
- 资产主档不可变；重复登记必须字节一致。
- 公司行动按 (asset_id, ex_date, action_type) 唯一。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from industrypulse.contracts.domain import AssetMaster, CorporateAction, MarketBar

from .database import Database


class MarketDataError(ValueError):
    """行情写入被拒绝（冲突/非法输入）；已入库内容保持不变。"""


class DuckDBMarketRepository:
    def __init__(self, database: Database | str | Path = ":memory:") -> None:
        self.database = database if isinstance(database, Database) else Database(database)

    # —— 资产主档 ——
    def register_asset(self, asset: AssetMaster) -> None:
        payload = asset.model_dump_json()
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT payload FROM asset_master WHERE asset_id=?", [asset.asset_id]
            ).fetchone()
            if row is not None and row[0] != payload:
                raise MarketDataError(f"资产主档不可改写：{asset.asset_id}")
            conn.execute(
                "INSERT INTO asset_master VALUES (?,?) ON CONFLICT DO NOTHING",
                [asset.asset_id, payload],
            )

    def asset(self, asset_id: str) -> AssetMaster:
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT payload FROM asset_master WHERE asset_id=?", [asset_id]
            ).fetchone()
        if row is None:
            raise MarketDataError(f"未登记资产：{asset_id}")
        return AssetMaster.model_validate_json(row[0])

    def assets(self) -> list[AssetMaster]:
        with self.database.transaction() as conn:
            rows = conn.execute("SELECT payload FROM asset_master ORDER BY asset_id").fetchall()
        return [AssetMaster.model_validate_json(row[0]) for row in rows]

    # —— 行情 ——
    def publish_bars(self, bars: Sequence[MarketBar]) -> int:
        """批量写入当日 bar；返回实际写入条数。同键不同内容一律拒绝。"""
        if not bars:
            raise MarketDataError("空的 bar 批次")
        inserted = 0
        with self.database.transaction() as conn:
            for bar in bars:
                payload = bar.model_dump_json()
                row = conn.execute(
                    "SELECT payload FROM market_bar WHERE asset_id=? AND trade_date=?",
                    [bar.asset_id, bar.trade_date],
                ).fetchone()
                if row is not None:
                    if row[0] != payload:
                        raise MarketDataError(
                            f"bar 不可改写：{bar.asset_id} {bar.trade_date.isoformat()}"
                        )
                    continue
                conn.execute(
                    "INSERT INTO market_bar VALUES (?,?,?)",
                    [bar.asset_id, bar.trade_date, payload],
                )
                inserted += 1
        return inserted

    def bars(
        self, asset_id: str, *, start: date | None = None, end: date | None = None
    ) -> tuple[MarketBar, ...]:
        query = "SELECT payload FROM market_bar WHERE asset_id=?"
        params: list[Any] = [asset_id]
        if start is not None:
            query += " AND trade_date >= ?"
            params.append(start)
        if end is not None:
            query += " AND trade_date <= ?"
            params.append(end)
        query += " ORDER BY trade_date"
        with self.database.transaction() as conn:
            rows = conn.execute(query, params).fetchall()
        return tuple(MarketBar.model_validate_json(row[0]) for row in rows)

    def bar_count(self) -> int:
        with self.database.transaction() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM market_bar").fetchone()[0])

    # —— 公司行动 ——
    def register_action(self, action: CorporateAction) -> None:
        payload = action.model_dump_json()
        key = [action.asset_id, action.ex_date, action.action_type]
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT payload FROM corporate_action WHERE asset_id=? AND ex_date=? "
                "AND action_type=?",
                key,
            ).fetchone()
            if row is not None and row[0] != payload:
                raise MarketDataError(f"公司行动不可改写：{key}")
            conn.execute(
                "INSERT INTO corporate_action VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                [*key, payload],
            )

    def actions(self, asset_id: str) -> tuple[CorporateAction, ...]:
        with self.database.transaction() as conn:
            rows = conn.execute(
                "SELECT payload FROM corporate_action WHERE asset_id=? ORDER BY ex_date",
                [asset_id],
            ).fetchall()
        return tuple(CorporateAction.model_validate_json(row[0]) for row in rows)


__all__ = ["DuckDBMarketRepository", "MarketDataError"]
