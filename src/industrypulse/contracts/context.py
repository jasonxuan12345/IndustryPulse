"""C-004 研究模式与查询上下文（§6.3 / §7.4）。

三种历史研究模式是 V2.0 的架构级变更（§0.2）：模式决定证据门槛与可用时点，
进而决定研究结论允许声称的强度（§6.5 允许边界表）。行为一致性由
tests/pit 与 tests/contracts 的 fixture 验证（C-004 验收）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field

from .common import AwareDatetime, ContractBaseModel, NonEmptyStr, UpperId
from .observation import EvidenceGrade


class ResearchMode(StrEnum):
    """历史研究模式（§6.3，枚举值冻结）。"""

    STRICT_PIT = "strict_pit"
    RECONSTRUCTED_PIT = "reconstructed_pit"
    EXPLORATORY = "exploratory"


#: 各模式的发布/数值证据门槛（§6.2/§6.3 伪码的合同化表达）。
#: STRICT：release、value 均 ≥B；RECONSTRUCTED：两者 ≥C；
#: EXPLORATORY：无门槛，但输出必须带"非 PIT 验证"水印（D-007）。
MODE_EVIDENCE_GATES: dict[
    ResearchMode, tuple[frozenset[EvidenceGrade], frozenset[EvidenceGrade]]
] = {
    ResearchMode.STRICT_PIT: (
        frozenset({EvidenceGrade.A, EvidenceGrade.B}),
        frozenset({EvidenceGrade.A, EvidenceGrade.B}),
    ),
    ResearchMode.RECONSTRUCTED_PIT: (
        frozenset({EvidenceGrade.A, EvidenceGrade.B, EvidenceGrade.C}),
        frozenset({EvidenceGrade.A, EvidenceGrade.B, EvidenceGrade.C}),
    ),
    ResearchMode.EXPLORATORY: (frozenset(set(EvidenceGrade)), frozenset(set(EvidenceGrade))),
}

#: RECONSTRUCTED 模式对 C 级发布证据施加的默认保守滞后自然日（§6.4：
#: 具体系列应取可验证历史发布时点的 p99+安全垫，此处仅是兜底默认值）。
DEFAULT_CONSERVATIVE_LAG_DAYS = 5


class AnalysisContext(ContractBaseModel):
    """分析上下文（§7.4）：一切查询/特征/预测的公共入参。

    ``as_of`` 是模拟决策时点（§6.1 decision_as_of），必须是时区感知时间；
    UI 边界统一转换为 Asia/Shanghai。
    """

    as_of: AwareDatetime
    research_mode: ResearchMode
    industry_id: UpperId
    dataset_snapshot_id: NonEmptyStr  # 已冻结输入快照（feature_snapshot.snapshot_id）
    mapping_version: NonEmptyStr  # industry_series_map 版本
    target_version: NonEmptyStr | None = None

    @property
    def is_pit_verified(self) -> bool:
        """只有 STRICT 模式的结果可声称"严格历史时点回测"（§6.5）。"""
        return self.research_mode == ResearchMode.STRICT_PIT


class DataQuality(ContractBaseModel):
    """统一响应体的数据质量摘要（§14.1）。覆盖率为 0 不等于经济恶化。"""

    coverage: Annotated[float, Field(ge=0.0, le=1.0)]
    vintage_verified_ratio: Annotated[float, Field(ge=0.0, le=1.0)]
