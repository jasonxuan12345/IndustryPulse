"""每个合同模型的一个"标准实例"：未知字段拒收/roundtrip 参数化数据源。

实例尽量取自 tests/fixtures/g0（与业务场景同源）；无 fixture 的模型用
字面量构造。新增合同模型时必须在此补实例——漏掉的模型不会被
test_unknown_fields / test_roundtrip 覆盖（有 completeness 测试守护）。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from industrypulse.contracts import (
    AnalysisContext,
    ApiWarning,
    AssetIndustryMap,
    AssetMaster,
    BacktestEvent,
    CorporateAction,
    DataQuality,
    ErrorPayload,
    EvidenceCandidate,
    ExperimentSpec,
    FeatureFrame,
    FeatureRow,
    IndicatorSpec,
    IndustryNode,
    IndustryRelation,
    JobStatus,
    LabeledFeatureFrame,
    MarketBar,
    ModelArtifact,
    ObservationFrame,
    ObservationVintage,
    Prediction,
    PredictionFrame,
    RawArtifact,
    ResearchMode,
    ResearchQuery,
    RouteSpec,
    SeriesSpec,
    SkippedVintage,
    SkipReason,
    SourceDescriptor,
    SplitSpec,
    TargetFrame,
    TargetRow,
    TargetSpec,
    sample_industry_state_payload,
    select_vintages_as_of,
)

_FIX = Path(__file__).parents[1] / "fixtures" / "g0"


def _load(name: str) -> dict:
    return json.loads((_FIX / f"{name}.json").read_text(encoding="utf-8"))


def _profit_vintages() -> list[ObservationVintage]:
    return [
        ObservationVintage.model_validate(r) for r in _load("steel_profit_vintages")["vintages"]
    ]


def canonical_instances() -> dict[str, object]:
    """name → 合同实例（dict 保序，测试报告可读）。"""
    inst: dict[str, object] = {}

    # —— sources ——
    from industrypulse.contracts import ReleasePolicy

    policy = ReleasePolicy(
        policy_version="nbs-profit-release-v1",
        description="规模以上工业企业利润：所属期次月27日前后发布",
        release_lag_days=30,
        safety_days=5,
        intraday_time=None,
    )
    inst["SourceDescriptor"] = SourceDescriptor(
        source_id="src.nbs.profit",
        owner="S",
        url="https://www.stats.gov.cn/sj/zxfb/",
        data_fields=["profit_ytd", "revenue_ytd", "cost_ytd", "profit_yoy_ytd"],
        frequency="M",
        legal_status="public_official",
        revision_behavior="revised_later",
        release_policy=policy,
        priority="S0",
        status="candidate",
        last_smoke_test=datetime.fromisoformat("2026-10-01T09:00:00+08:00"),
    )
    inst["ReleasePolicy"] = policy
    inst["SeriesSpec"] = SeriesSpec(
        series_id="cn.nbs.industry_c31.profit_ytd",
        source_id="src.nbs.profit",
        unit="亿元",
        frequency="M",
        is_ytd=True,
        statistical_population="规模以上工业企业",
        revision_policy="revised_later",
        release_policy=policy,
        timestamp_precision="day",
        description="黑色金属冶炼和压延加工业利润总额累计值",
    )

    # —— observation（fixture 驱动）——
    vintages = _profit_vintages()
    inst["ObservationVintage"] = vintages[0]
    frame = ObservationFrame(
        series_ids=["cn.nbs.industry_c31.profit_ytd"],
        as_of=datetime.fromisoformat("2025-08-31T17:00:00+08:00"),
        research_mode=ResearchMode.STRICT_PIT.value,
        frequency_hint="M",
        vintages=vintages[:2],
    )
    inst["ObservationFrame"] = frame
    inst["RawArtifact"] = RawArtifact(
        artifact_id="artifact-demo-1",
        source_id="src.nbs.profit",
        uri="data/bronze/nbs/2025/08/t20250827.html",
        sha256="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
        mime="text/html",
        byte_size=102400,
        retrieved_at=datetime.fromisoformat("2025-08-27T09:35:00+08:00"),
        source_url="https://www.stats.gov.cn/sj/zxfb/202508/t20250827_demo1.html",
    )
    inst["ResearchQuery"] = ResearchQuery.model_validate(_load("evidence_candidates")["queries"][0])
    inst["EvidenceCandidate"] = EvidenceCandidate.model_validate(
        _load("evidence_candidates")["candidates"][0]
    )

    # —— context / pit ——
    ctx = AnalysisContext(
        as_of=datetime.fromisoformat("2025-09-30T17:00:00+08:00"),
        research_mode=ResearchMode.RECONSTRUCTED_PIT,
        industry_id="CN_NBS_C31",
        dataset_snapshot_id="sha256:demo1234",
        mapping_version="steel-map-v1",
        target_version="T2@1",
    )
    inst["AnalysisContext"] = ctx
    inst["DataQuality"] = DataQuality(coverage=0.82, vintage_verified_ratio=0.54)
    sel = select_vintages_as_of(vintages, as_of=ctx.as_of, mode=ResearchMode.STRICT_PIT)
    inst["AsOfSelection"] = sel
    inst["SkippedVintage"] = SkippedVintage(
        series_id="cn.nbs.industry_c31.profit_ytd",
        observation_start=date(2025, 10, 1),
        vintage_id=1,
        reason=SkipReason.FUTURE_RELEASE,
        detail="effective_available_at=2025-11-27T09:30:00+08:00",
    )

    # —— domain（fixture 驱动）——
    tax = _load("steel_taxonomy")
    inst["IndustryNode"] = IndustryNode.model_validate(tax["nodes"][0])
    inst["IndustryRelation"] = IndustryRelation.model_validate(tax["relations"][0])
    inst["IndicatorSpec"] = IndicatorSpec.model_validate(tax["indicators"][0])
    uni = _load("etf_universe")
    inst["AssetMaster"] = AssetMaster.model_validate(uni["assets"][0])
    inst["AssetIndustryMap"] = AssetIndustryMap.model_validate(uni["industry_map"][0])
    from industrypulse.contracts import IndustrySeriesMap

    inst["IndustrySeriesMap"] = IndustrySeriesMap(
        industry_id="CN_NBS_C31",
        series_id="cn.nbs.industry_c31.profit_ytd",
        role="label",
        valid_from=date(2017, 10, 1),
    )
    inst["MarketBar"] = MarketBar.model_validate(_load("market_bars")["bars"][0])
    inst["CorporateAction"] = CorporateAction.model_validate(
        _load("corporate_actions")["actions"][0]
    )

    # —— model ——
    def _ts(day: str) -> datetime:
        return datetime.fromisoformat(f"2025-{day}T17:00:00+08:00")

    rows = [
        FeatureRow(
            decision_as_of=_ts(day),
            industry_id="CN_NBS_C31",
            features={"profit_margin_latest": 0.021, "rb_ret_20d": 0.035, "coverage_ratio": 0.9},
            missing_features=[],
            max_ancestor_available_at=datetime.fromisoformat("2025-06-27T09:30:00+08:00"),
            latest_source_release_at=datetime.fromisoformat("2025-06-27T09:30:00+08:00"),
        )
        for day in ("06-30", "07-31", "08-31")
    ]
    fframe = FeatureFrame(
        industry_id="CN_NBS_C31",
        snapshot_id="sha256:featdemo1",
        feature_spec_version="fspec-v1",
        schema_hash="sha256:schemademo",
        research_mode=ResearchMode.STRICT_PIT,
        built_at=datetime.fromisoformat("2025-09-30T17:05:00+08:00"),
        rows=rows,
    )
    inst["FeatureRow"] = rows[0]
    inst["FeatureFrame"] = fframe

    tspec = TargetSpec(
        target_id="T2_MARGIN_IMPROVE",
        definition_version="t2@1",
        industry_id="CN_NBS_C31",
        horizon_months=3,
        label_kind="margin_direction_up",
        formula=(
            "margin_ytd(future_release) - margin_ytd(latest_release_as_of) > delta"
            "（同年累计口径内）"
        ),
        missing_policy="跨年重置期改用 same_month_yoy_margin 或剔除并报告比例",
        baselines=["M00_LAST", "M01_SEASONAL"],
        description="未来第3次有效月度公告的行业累计利润率改善方向（§10.1 T2）",
    )
    trows = [
        TargetRow(
            industry_id="CN_NBS_C31",
            decision_as_of=r.decision_as_of,
            target_period="2025-09",
            value=1.0,
            label_available_at=datetime.fromisoformat("2025-10-28T09:30:00+08:00"),
        )
        for r in rows
    ]
    tframe = TargetFrame(spec=tspec, rows=trows)
    inst["TargetSpec"] = tspec
    inst["TargetRow"] = trows[0]
    inst["TargetFrame"] = tframe

    labeled = LabeledFeatureFrame(
        features=fframe,
        labels=[1.0, 0.0, None],
        label_available_at=[
            datetime.fromisoformat("2025-09-28T09:30:00+08:00"),
            datetime.fromisoformat("2025-10-28T09:30:00+08:00"),
            None,
        ],
    )
    inst["LabeledFeatureFrame"] = labeled

    prediction = Prediction(
        prediction_id="pred-demo-1",
        model_id="M03_LOGIT",
        model_version="m03@1",
        industry_id="CN_NBS_C31",
        target_id="T2_MARGIN_IMPROVE",
        as_of=datetime.fromisoformat("2025-09-30T17:00:00+08:00"),
        horizon_months=3,
        research_mode=ResearchMode.STRICT_PIT,
        predicted_probability=0.61,
        point_estimate=None,
        prediction_interval=None,
        baseline_probability=0.5,
        calibration_status="uncalibrated",
        top_features=["profit_margin_latest", "rb_ret_20d"],
        driver_evidence_ids=["cand-profit-202507-verified"],
        missing_features=["shfe_inventory_z"],
        model_status="ok",
        oos_metrics_by_year={"2024": {"brier": 0.213}},
        expiry_at=datetime.fromisoformat("2025-10-31T17:00:00+08:00"),
        run_id="run-demo-1",
    )
    inst["Prediction"] = prediction
    inst["PredictionFrame"] = PredictionFrame(run_id="run-demo-1", predictions=[prediction])
    inst["ModelArtifact"] = ModelArtifact(
        model_id="M03_LOGIT",
        model_version="m03@1",
        artifact_uri="artifacts/models/m03-demo.pkl",
        sha256="c0ffee0000000000000000000000000000000000000000000000000000000000",
        trained_at=datetime.fromisoformat("2025-09-30T17:10:00+08:00"),
        feature_spec_version="fspec-v1",
        target_id="T2_MARGIN_IMPROVE",
        training_rows=36,
    )

    # —— research ——
    split = SplitSpec(
        split_id="fold-2024",
        kind="expanding",
        train_start=date(2019, 1, 1),
        train_end=date(2023, 12, 31),
        test_start=date(2024, 1, 1),
        test_end=date(2024, 12, 31),
        purge_months=3,
        embargo_months=1,
    )
    inst["SplitSpec"] = split
    inst["ExperimentSpec"] = ExperimentSpec(
        experiment_id="exp-demo-1",
        code_sha="4399abb",
        dependencies_hash="sha256:depsdemo",
        dataset_manifest_hash="sha256:datademo",
        industry_map_version="steel-map-v1",
        pit_mode=ResearchMode.STRICT_PIT,
        train_cutoff=datetime.fromisoformat("2023-12-31T23:59:00+08:00"),
        outer_splits=[split],
        inner_splits=[],
        feature_list=["profit_margin_latest", "rb_ret_20d"],
        model_grid={"M03_LOGIT": {"C": [0.01, 0.1, 1.0, 10.0]}},
        random_seed=20261013,
        target_definition="T2_MARGIN_IMPROVE@1",
        baselines=["M00_LAST"],
        primary_metric="brier_score",
        secondary_metrics=["log_loss", "roc_auc"],
        approval_state="frozen",
        created_at=datetime.fromisoformat("2025-09-30T18:00:00+08:00"),
    )
    inst["BacktestEvent"] = BacktestEvent(
        event_id="bt-demo-1",
        experiment_id="exp-demo-1",
        asset_id="ETF_SH_STEEL_A",
        signal_at=datetime.fromisoformat("2025-10-31T17:00:00+08:00"),
        order_at=datetime.fromisoformat("2025-11-03T09:30:00+08:00"),
        fill_at=datetime.fromisoformat("2025-11-03T09:30:00+08:00"),
        side="buy",
        quantity="1000",
        price="1.075",
        fees="5.00",
        pnl=None,
        prediction_id="pred-demo-1",
    )
    inst["JobStatus"] = JobStatus(
        job_id="job-demo-1",
        job_name="monthly_release_check",
        state="succeeded",
        start_at=datetime.fromisoformat("2025-10-31T17:00:00+08:00"),
        end_at=datetime.fromisoformat("2025-10-31T17:04:00+08:00"),
        retry_count=0,
        idempotency_key="src.nbs.profit|2025-09|sha256:x|parser-v1",
    )

    # —— http ——
    inst["ApiWarning"] = ApiWarning(code="PARTIAL_VINTAGE", message="部分指标为重建版本")
    inst["ApiResponse"] = sample_industry_state_payload()
    inst["ErrorPayload"] = ErrorPayload.build(
        request_id="req-demo-err", code="PIT_NOT_ELIGIBLE", message="demo"
    )
    inst["RouteSpec"] = RouteSpec(
        method="GET", path="/healthz", params=[], result_kind="Health", owner_lane="O"
    )
    return inst
