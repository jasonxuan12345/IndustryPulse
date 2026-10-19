"""用例编排层：API 与 Dashboard 只允许访问 service，禁止直接写经济指标计算或原始 SQL。"""

from .api_handlers import build_handlers
from .as_published import (
    AsPublishedClaim,
    ClaimError,
    ClaimVerdict,
    evaluate_claim,
    parse_claim,
)
from .backtest_run import BacktestRequest, build_rule_signals, run_backtest
from .etf_ingest import EtfIngestError, EtfIngestReport, EtfIngestService
from .ingest import (
    BatchIngestReport,
    BatchItem,
    IngestError,
    IngestReport,
    IngestService,
    period_bounds,
)
from .monthly_backfill import BackfillOutcome, MonthlyBackfill
from .pipeline import PipelineService
from .pit_report import PitComparison, compare_modes, format_table
from .snapshots import ForecastSnapshotStore, SnapshotView

__all__ = [
    "AsPublishedClaim",
    "BacktestRequest",
    "build_handlers",
    "BackfillOutcome",
    "BatchIngestReport",
    "ClaimError",
    "EtfIngestError",
    "EtfIngestReport",
    "EtfIngestService",
    "ClaimVerdict",
    "BatchItem",
    "ForecastSnapshotStore",
    "IngestError",
    "IngestReport",
    "IngestService",
    "MonthlyBackfill",
    "PipelineService",
    "PitComparison",
    "SnapshotView",
    "compare_modes",
    "build_rule_signals",
    "evaluate_claim",
    "format_table",
    "parse_claim",
    "run_backtest",
    "period_bounds",
]
