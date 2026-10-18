"""用例编排层：API 与 Dashboard 只允许访问 service，禁止直接写经济指标计算或原始 SQL。"""

from .pipeline import PipelineService
from .snapshots import ForecastSnapshotStore, SnapshotView

__all__ = ["ForecastSnapshotStore", "SnapshotView", "PipelineService"]
