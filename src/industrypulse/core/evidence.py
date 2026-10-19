"""证据分级规则：S 线确定性解析与 A 线抽取共用的唯一实现。

§6.2 要求"发布证据"与"数值证据"分别评级；STRICT 模式两者均需 >=B。
此前该规则只存在于 ``ai_research.classifier``，S 线摄取若要复用就得复制一份，
两份实现随时间的静默分叉正是本项目最要避免的失败模式。因此规则收敛到本模块，
A 线 classifier 委托到这里，行为保持不变（有对照测试守护）。

导入约束：本模块只依赖 contracts（lane 边界测试中 core/jobs 允许 contracts+core）。
"""

from __future__ import annotations

from industrypulse.contracts.observation import EvidenceClass, EvidenceGrade
from industrypulse.contracts.sources import TimestampPrecision

#: 秒/分钟级发布时点才算"载明发布时间"（§6.2 A 级）。
_PRECISE_PRECISIONS = frozenset({TimestampPrecision.SECOND, TimestampPrecision.MINUTE})


def release_grade(
    *,
    has_publication: bool,
    precision: TimestampPrecision,
    policy_date: bool = False,
) -> EvidenceGrade:
    """发布证据等级（§6.2 左半）。

    - 无发布时点：有可核验的发布日历依据 -> C，否则 D；
    - 秒/分钟精度 -> A；仅日期 -> B；月份/未知 -> C。

    ``policy_date`` 必须由调用方显式主张"该时点来自已核验的发布日历"，
    不得由"我们恰好知道月份"推出。
    """
    if not has_publication:
        return EvidenceGrade.C if policy_date else EvidenceGrade.D
    if precision in _PRECISE_PRECISIONS:
        return EvidenceGrade.A
    if precision == TimestampPrecision.DAY:
        return EvidenceGrade.B
    return EvidenceGrade.C


def value_grade(
    *,
    has_value: bool,
    is_search_snippet: bool = False,
    contemporary: bool = False,
    has_raw_hash: bool = False,
    has_locator: bool = False,
    trusted_copy: bool = False,
) -> EvidenceGrade:
    """数值证据等级（§6.2 右半）。

    ``contemporary``（"这是同期原件，不是今天抓到的修订值"）必须显式主张；
    默认 False 时最高只能给 C。这条默认值是本系统的核心纪律：**宁可低估，
    不得自动升格**，否则 STRICT 回测会静默变成"用未来修订值预测过去"。
    """
    if not has_value or is_search_snippet:
        return EvidenceGrade.D
    if contemporary and has_raw_hash and has_locator:
        return EvidenceGrade.B if trusted_copy else EvidenceGrade.A
    return EvidenceGrade.C


def grades_for_candidate(
    *,
    has_publication: bool,
    precision: TimestampPrecision,
    has_value: bool,
    evidence_class: EvidenceClass,
    has_raw_hash: bool,
    has_locator: bool,
    contemporary: bool = False,
    trusted_copy: bool = False,
    policy_date: bool = False,
) -> tuple[EvidenceGrade, EvidenceGrade]:
    """一次算出 (发布证据, 数值证据)，供 A 线 EvidenceCandidate 直接使用。"""
    return (
        release_grade(
            has_publication=has_publication, precision=precision, policy_date=policy_date
        ),
        value_grade(
            has_value=has_value,
            is_search_snippet=evidence_class == EvidenceClass.SEARCH_SNIPPET,
            contemporary=contemporary,
            has_raw_hash=has_raw_hash,
            has_locator=has_locator,
            trusted_copy=trusted_copy,
        ),
    )


__all__ = ["grades_for_candidate", "release_grade", "value_grade"]
