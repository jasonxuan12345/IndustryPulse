"""阶段 4 模型目标：T3 帧、市场特征 PIT、高级模型门槛、验收汇总。"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from industrypulse.contracts import TZ_SHANGHAI
from industrypulse.economy.price_check import PriceLine
from industrypulse.services.model_targets import (
    advanced_gate,
    build_relative_return_frames,
    grid_for,
    market_feature_view,
    summarize_report,
    t3_target_spec,
)


def _line(days: tuple, closes: tuple) -> PriceLine:
    return PriceLine(days, closes, ())


DAYS = tuple(date(2025, 1, 1) + __import__("datetime").timedelta(days=i) for i in range(80))


class TestMarketFeatures:
    def test_relative_and_vol_from_common_days_only(self) -> None:
        asset = _line(DAYS, tuple(100.0 + i for i in range(80)))
        # 基准斜率比例更低（0.4/50 < 1/100）：资产相对强弱为正。
        benchmark = _line(DAYS, tuple(50.0 + 0.4 * i for i in range(80)))
        view = market_feature_view(asset, benchmark, DAYS[79])
        assert view["etf_rel_ret_20d"] is not None
        assert view["etf_rel_ret_20d"] > 0
        assert view["etf_vol_20d"] is not None and view["etf_vol_20d"] >= 0

    def test_insufficient_history_yields_none_not_zero(self) -> None:
        asset = _line(DAYS[:10], tuple(100.0 + i for i in range(10)))
        benchmark = _line(DAYS[:10], tuple(50.0 + i for i in range(10)))
        view = market_feature_view(asset, benchmark, DAYS[9])
        assert view == {
            "etf_rel_ret_20d": None,
            "etf_rel_ret_60d": None,
            "etf_vol_20d": None,
        }

    def test_no_future_days_leak_into_features(self) -> None:
        asset = _line(DAYS, tuple(100.0 + (i % 9) for i in range(80)))
        benchmark = _line(DAYS, tuple(50.0 + (i % 5) for i in range(80)))
        early = market_feature_view(asset, benchmark, DAYS[30])
        late = market_feature_view(asset, benchmark, DAYS[79])
        assert early["etf_rel_ret_20d"] != late["etf_rel_ret_20d"]


class TestAdvancedGate:
    def test_gate_requires_volume_and_balance(self) -> None:
        from industrypulse.contracts.model import FeatureFrame, FeatureRow, TargetFrame, TargetRow

        def build(values: list[float | None]) -> tuple:
            rows = [
                FeatureRow(
                    decision_as_of=datetime(2025, 1, 1, 17, tzinfo=TZ_SHANGHAI),
                    industry_id="CN_NBS_C31",
                    features={"f": 1.0},
                    max_ancestor_available_at=datetime(2025, 1, 1, 17, tzinfo=TZ_SHANGHAI),
                )
            ]
            targets = [
                TargetRow(
                    industry_id="CN_NBS_C31",
                    decision_as_of=datetime(2025, 1, 1, 17, tzinfo=TZ_SHANGHAI),
                    target_period="2025-01",
                    value=value,
                    label_available_at=datetime(2025, 2, 1, tzinfo=TZ_SHANGHAI),
                )
                for value in values
                if value is not None
            ]
            unscorable = [
                TargetRow(
                    industry_id="CN_NBS_C31",
                    decision_as_of=datetime(2025, 1, 1, 17, tzinfo=TZ_SHANGHAI),
                    target_period="2025-01",
                    value=None,
                    label_available_at=None,
                    unscorable_reason="FORWARD_WINDOW_IMMATURE",
                )
                for value in values
                if value is None
            ]
            frame = FeatureFrame(
                industry_id="CN_NBS_C31",
                snapshot_id="s",
                feature_spec_version="v",
                schema_hash="h",
                research_mode=__import__(
                    "industrypulse.contracts.context", fromlist=["ResearchMode"]
                ).ResearchMode.RECONSTRUCTED_PIT,
                built_at=datetime(2025, 1, 1, tzinfo=TZ_SHANGHAI),
                rows=rows,
            )
            targets_frame = TargetFrame(
                spec=t3_target_spec("CN_NBS_C31"), rows=targets + unscorable
            )
            return frame, targets_frame

        # 61 个样本但全是同一类：门槛拒绝。
        frame, targets = build([1.0] * 61)
        gate = advanced_gate(SimpleNamespace(features=frame, targets=targets))
        assert gate["eligible"] is False
        # 60 样本、每类 30：门槛通过。
        frame, targets = build([1.0, 0.0] * 30)
        gate = advanced_gate(SimpleNamespace(features=frame, targets=targets))
        assert gate["eligible"] is True

    def test_grid_excludes_advanced_until_gate_passes(self) -> None:
        built = SimpleNamespace(targets=SimpleNamespace(rows=[]))
        grid = grid_for(built, include_advanced=True)
        assert "M06_LIGHTGBM" not in grid and "M03_LOGIT" in grid


class TestT3Frames:
    def _market(self, asset_days, asset_closes, bench_days=None, bench_closes=None):
        asset = _line(asset_days, asset_closes)
        benchmark = _line(bench_days or asset_days, bench_closes or asset_closes)

        class Market:
            def bars(self, asset_id, **_):
                line = asset if asset_id == "ETF_T" else benchmark
                return [
                    SimpleNamespace(trade_date=day, close=str(close))
                    for day, close in zip(line.days, line.closes, strict=True)
                ]

            def actions(self, asset_id):
                return ()

        return Market()

    def test_t3_requires_both_price_lines(self) -> None:
        market = self._market(DAYS, tuple(100.0 + i for i in range(80)))
        repository = SimpleNamespace(
            select=lambda *a, **k: SimpleNamespace(selected=[], skipped=[])
        )
        with pytest.raises(ValueError):
            build_relative_return_frames(
                repository,
                market,
                snapshot_id=None,
                industry_id="CN_NBS_C31",
                asset_id="ETF_MISSING",
                profit_series="p",
                revenue_series="r",
                start=date(2025, 1, 1),
                end=date(2025, 3, 1),
            )

    def test_t3_labels_wait_for_window_maturity(self) -> None:
        from datetime import timedelta as _td

        days = tuple(date(2025, 1, 1) + _td(days=i) for i in range(140))
        closes = tuple(100.0 + (i % 7) for i in range(len(days)))
        market = self._market(days, closes)
        row = SimpleNamespace(
            series_id="p",
            observation_start=date(2025, 1, 1),
            value="1.0",
            available_at=datetime(2025, 1, 15, tzinfo=TZ_SHANGHAI),
            publication_at=None,
            source_url=None,
            vintage_id=1,
        )
        repository = SimpleNamespace(
            select=lambda *a, **k: SimpleNamespace(selected=[row], skipped=[]),
            all_vintages=lambda ids: [row],
        )
        built = build_relative_return_frames(
            repository,
            market,
            snapshot_id=None,
            industry_id="CN_NBS_C31",
            asset_id="ETF_T",
            profit_series="p",
            revenue_series="r",
            start=date(2025, 1, 1),
            end=date(2025, 4, 1),
        )
        matured = [row for row in built.targets.rows if row.value is not None]
        immature = [row for row in built.targets.rows if row.value is None]
        assert immature, "临近数据末端的决策必须标记窗口未成熟"
        assert all(row.unscorable_reason == "FORWARD_WINDOW_IMMATURE" for row in immature)
        assert matured, "已走完 63 个交易日的窗口必须可评分"


class TestSummary:
    def test_summary_records_question_benchmark_and_meaning(self) -> None:
        report = SimpleNamespace(
            failures=[{"split_id": "outer-1", "model_id": "M02_AR", "reason": "AR requires 60"}],
            comparison={
                "status": "INSUFFICIENT_EVIDENCE",
                "common_oos_count": 5,
                "models": {
                    "M00_LAST": {
                        "overall": {"brier": 0.5, "log_loss": 17.0, "roc_auc": 0.5, "n": 5}
                    }
                },
            },
        )
        gate = {"eligible": False, "scorable_rows": 34, "class_one": 15, "class_zero": 19}
        payload = summarize_report("T2", "盈利改善", "领先 1 个公布期", report, gate)
        assert payload["question"] == "盈利改善"
        assert payload["benchmark"].startswith("M00_LAST")
        assert payload["failures"][0]["model_id"] == "M02_AR"
        assert "不是股票上涨概率" in payload["probability_meaning"]
        assert payload["models"][0]["brier"] == 0.5
        assert t3_target_spec("CN_NBS_C31").target_id == "T3_RELATIVE_RETURN_DIRECTION"
