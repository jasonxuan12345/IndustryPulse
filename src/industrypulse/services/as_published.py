"""同期原件证据：把"这是当期原件"从人工开关变成可核验记录。

背景：此前摄取层有一个 ``--contemporary`` 布尔开关，等于让操作者口头保证
"这份文件是当期原件"。这与本项目"不许自欺"的纪律直接冲突——任何人都能让
STRICT 从 0% 变成 100%，而审计里看不到任何证据。

本模块把该声明替换为**带时间窗的证据**：

- ``capture``：本系统在官方发布后 48 小时内抓取并归档原件。
  修订窗口尚未开启，抓到的必然是当期版本；捕获时点与原件哈希都可核验。

超过窗口、无发布时间、捕获早于发布——一律不升格，保持 C 级并记录原因。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

#: 允许的最大捕获窗口（小时）。超过即视为"抓的是现行修订值"。
MAX_CAPTURE_WINDOW_HOURS = 48.0

#: 支持的证据方法（后续可扩展 trusted_copy / archive_snapshot）。
SUPPORTED_METHODS = ("capture",)


class ClaimError(ValueError):
    """证据声明无法解析或自相矛盾。"""


@dataclass(frozen=True)
class AsPublishedClaim:
    """ "该原件为同期发布版本"的证据声明。

    ``captured_at`` 为 None 表示使用抓取时点（``RawArtifact.retrieved_at``）。
    """

    method: str
    captured_at: datetime | None = None


@dataclass(frozen=True)
class ClaimVerdict:
    """证据评估结果；``ok`` 为 False 时数值证据必须停在 C。"""

    ok: bool
    reason: str
    window_hours: float | None = None

    def describe(self, method: str = "capture") -> str:
        if self.ok:
            return f"as_published={method} window={self.window_hours:.1f}h"
        return f"as_published rejected: {self.reason}"


def parse_claim(raw: str | None) -> AsPublishedClaim | None:
    """解析 CLI 取值：``auto`` 或 ``capture:<ISO8601>``。"""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if text == "auto":
        return AsPublishedClaim(method="capture", captured_at=None)
    method, _, value = text.partition(":")
    if method not in SUPPORTED_METHODS:
        raise ClaimError(f"未支持的证据方法：{method!r}（可选 {SUPPORTED_METHODS}）")
    if not value:
        raise ClaimError(f"{method} 需要显式时点，或使用 auto")
    try:
        captured = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ClaimError(f"捕获时点不是合法 ISO8601：{value!r}") from exc
    if captured.tzinfo is None:
        raise ClaimError("捕获时点必须带时区")
    return AsPublishedClaim(method=method, captured_at=captured)


def evaluate_claim(
    claim: AsPublishedClaim | None,
    *,
    publication_at: datetime | None,
    fallback_captured_at: datetime,
    max_window_hours: float = MAX_CAPTURE_WINDOW_HOURS,
) -> ClaimVerdict:
    """判断该条观测能否主张"同期原件"。默认一律拒绝。"""
    if claim is None:
        return ClaimVerdict(ok=False, reason="未提供同期原件证据")
    if publication_at is None:
        return ClaimVerdict(ok=False, reason="无可核验发布时间，无法计算捕获窗口")
    captured = claim.captured_at or fallback_captured_at
    if captured.tzinfo is None:
        return ClaimVerdict(ok=False, reason="捕获时点缺少时区")
    if captured < publication_at:
        return ClaimVerdict(ok=False, reason="捕获时点早于发布时间，证据自相矛盾")
    window = (captured - publication_at).total_seconds() / 3600
    if window > max_window_hours:
        return ClaimVerdict(
            ok=False,
            reason=f"距发布 {window:.1f}h 超出 {max_window_hours:.0f}h 窗口",
            window_hours=window,
        )
    return ClaimVerdict(ok=True, reason="窗口内捕获", window_hours=window)


__all__ = [
    "MAX_CAPTURE_WINDOW_HOURS",
    "SUPPORTED_METHODS",
    "AsPublishedClaim",
    "ClaimError",
    "ClaimVerdict",
    "evaluate_claim",
    "parse_claim",
]
