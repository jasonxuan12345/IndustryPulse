"""用例编排层：API 与 Dashboard 只允许访问 service，禁止直接写经济指标计算或原始 SQL。"""

from .ingest import (
    BatchIngestReport,
    BatchItem,
    IngestError,
    IngestReport,
    IngestService,
    period_bounds,
)
from .pipeline import PipelineService
from .pit_report import PitComparison, compare_modes, format_table
from .snapshots import ForecastSnapshotStore, SnapshotView

__all__ = [
    "BatchIngestReport",
    "BatchItem",
    "ForecastSnapshotStore",
    "IngestError",
    "IngestReport",
    "IngestService",
    "PipelineService",
    "PitComparison",
    "SnapshotView",
    "compare_modes",
    "format_table",
    "period_bounds",
]
