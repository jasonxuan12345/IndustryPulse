"""合同层公共基座：版本号、时区、模型基类与通用标量类型。

所有合同模型继承 ``ContractBaseModel``：

- ``extra="forbid"``：未知字段一律拒收（C-002 验收）；
- ``frozen=True``：合同实例不可变，防止沿数据链路被静默改写（§2.3 溯源）；
- ``validate_assignment=True``：即使绕过构造器赋值也强制校验。

字段或语义的任何变更必须递增 :data:`CONTRACTS_VERSION` 并走 contracts PR（§3.3）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

#: 合同包版本（G0 冻结为 1.0.0）。破坏性变更提升主版本，新增可选字段提升次版本。
CONTRACTS_VERSION = "1.0.0"

#: 全系统观察时钟（§1.2）：中国交易所时区 Asia/Shanghai。
TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

#: 交易日"时点已知"的统一约定时刻（§6.4：只知日期时，次一交易日 09:30 后可用）。
TRADING_DAY_AVAILABLE_TIME = (9, 30)


def _require_aware(dt: datetime) -> datetime:
    """§6.1 六时间属性一律要求时区感知；裸时间在合同层即错误。"""
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("datetime 必须携带时区（合同 §6.1：禁止裸时间）")
    return dt


#: 带时区的 datetime 标量类型。
AwareDatetime = Annotated[datetime, AfterValidator(_require_aware)]

#: sha256 十六进制（原件指纹，§4.3）。
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

#: 快照/工件引用，形如 ``sha256:ab...``。
SnapshotRef = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{8,64}$")]

#: 非空字符串（显示名、单位等）。
NonEmptyStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]

#: URL：保留原始字符串以确保证据可复核（不做 pydantic URL 规范化改写）。
UrlStr = Annotated[str, Field(pattern=r"^https?://[^\s]+$")]

#: 标识符通用形状：小写字母/数字/点/下划线（series_id、source_id 等）。
LowerId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")]

#: 大写标识符（industry_id、asset_id 等）。
UpperId = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9._-]*$")]


class ContractBaseModel(BaseModel):
    """全部数据合同的基类：未知字段拒收、实例冻结、赋值即校验。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
        validate_default=True,
    )
