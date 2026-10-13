"""C-005 领域合同：行业节点 / 指标规格 / 资产主档（I 线与 V 线的共同边界）。

核心纪律（§7.1）：industry_id / asset_id 是稳定主键，绝不因中文显示名变更
而漂移；所有映射经 config registry 注册，禁止临时按名称匹配。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import Field, model_validator

from .common import AwareDatetime, ContractBaseModel, LowerId, NonEmptyStr, UpperId
from .sources import Frequency


class IndustryStandard(StrEnum):
    """行业分类标准（§7.2 industry_taxonomy.standard）。"""

    GB_T_4754_2017 = "GB/T 4754-2017"
    GB_T_4754_2011 = "GB/T 4754-2011"
    CSI_INDUSTRY = "CSI_INDUSTRY"  # 中证行业分类（证券口径，与统计口径显式区分）
    PROJECT_INTERNAL = "PROJECT_INTERNAL"  # 项目内映射层


class EconomicDimension(StrEnum):
    """六个经济维度（§8.1）：缺失维度显示 coverage，不得强行算 0 分。"""

    DEMAND = "demand"
    SUPPLY = "supply"
    INVENTORY = "inventory"
    PRICE = "price"
    PROFITABILITY = "profitability"
    LIQUIDITY = "liquidity"


class EconomicDirection(StrEnum):
    """指标经济方向（§9.2）：POSITIVE=值高对行业景气为正贡献；UNKNOWN 只展示不聚合。"""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    UNKNOWN = "unknown"


class IndicatorLevel(StrEnum):
    INDICATOR_CORE = "CORE"
    INDICATOR_EXTENDED = "EXTENDED"


class SeriesRole(StrEnum):
    """序列在行业中的角色（§7.2 industry_series_map.role）。"""

    LABEL = "label"  # 当期经济评价主标签（如行业利润）
    FEATURE = "feature"
    CONTEXT = "context"  # 宏观代理等背景序列
    MACRO_PROXY = "macro_proxy"  # §20.2：制造业总量数据必须打此标


class RelationType(StrEnum):
    """产业链关系方向（§7.2 industry_relation.relation_type）。"""

    SUPPLIES_INPUT = "supplies_input"  # upstream 向 downstream 供应投入品
    DEMANDS_OUTPUT = "demands_output"  # upstream 消费 downstream 的产出
    SHARED_DRIVER = "shared_driver"  # 共享成本/需求驱动


class Exchange(StrEnum):
    SSE = "SSE"
    SZSE = "SZSE"
    CFFEX = "CFFEX"
    SHFE = "SHFE"
    DCE = "DCE"
    CZCE = "CZCE"
    GFEX = "GFEX"
    OTC = "OTC"


class InstrumentType(StrEnum):
    ETF = "ETF"
    INDEX = "INDEX"  # 指数不可直接交易：回测必须显式区分（V-009）
    FUTURE = "FUTURE"
    SPOT = "SPOT"


class IndustryNode(ContractBaseModel):
    """行业节点：identity = industry_id；names 仅是展示层，可随时改（C-005 验收）。"""

    industry_id: UpperId  # 如 CN_NBS_C31
    standard: IndustryStandard
    code: NonEmptyStr  # 如 "31"（黑色金属冶炼和压延加工业）
    names: dict[str, NonEmptyStr]  # lang → 显示名；zh/en；不影响身份
    valid_from: date
    valid_to: date | None = None
    parent_id: UpperId | None = None
    notes: str = ""

    @model_validator(mode="after")
    def _validity(self) -> IndustryNode:
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to 不得早于 valid_from")
        return self


class IndustryRelation(ContractBaseModel):
    """产业链上下游关系（I-002：关系必须有来源与有效期）。"""

    upstream_id: UpperId
    downstream_id: UpperId
    relation_type: RelationType
    valid_from: date
    valid_to: date | None = None
    weight_policy: NonEmptyStr | None = None  # 版本化权重策略引用
    source_ref: NonEmptyStr  # 关系依据（公开资料出处），禁止拍脑袋连边
    notes: str = ""


class IndicatorSpec(ContractBaseModel):
    """行业指标规格：序列→维度→方向的登记（E-001 输入；§8.1 指标字典行）。"""

    indicator_id: NonEmptyStr
    series_ids: Annotated[list[LowerId], Field(min_length=1)]
    dimension: EconomicDimension
    economic_direction: EconomicDirection
    level: IndicatorLevel
    industry_ids: Annotated[list[UpperId], Field(min_length=1)]
    role: SeriesRole = SeriesRole.FEATURE
    transform: NonEmptyStr = "none"
    valid_from: date
    description: NonEmptyStr
    notes: str = ""

    @model_validator(mode="after")
    def _unknown_direction_rule(self) -> IndicatorSpec:
        # §9.2：unknown 方向只可展示，禁止进入综合分数——由配置层校验提示
        if self.economic_direction == EconomicDirection.UNKNOWN and self.role == SeriesRole.LABEL:
            raise ValueError("LABEL 序列不得为 unknown 方向（当期评价主标签必须有方向定义）")
        return self


class AssetMaster(ContractBaseModel):
    """金融资产主档（§7.2 asset_master / §13.1）：防未来函数的第一道闸。

    历史任一交易日只允许使用当时已上市且未退市的资产（PIT-008）。
    ``tracking_index_id`` 仅为基准参照，INDEX 型资产不可直接交易。
    """

    asset_id: UpperId
    exchange: Exchange
    instrument_type: InstrumentType
    name_zh: NonEmptyStr  # 展示用，不影响身份
    list_at: AwareDatetime
    delist_at: AwareDatetime | None = None  # None = 仍在市
    fund_type: NonEmptyStr | None = None
    tracking_index_id: UpperId | None = None
    notes: str = ""

    @model_validator(mode="after")
    def _lifespan(self) -> AssetMaster:
        if self.delist_at is not None and self.delist_at <= self.list_at:
            raise ValueError("delist_at 必须晚于 list_at")
        return self

    def is_listed_on(self, d: date) -> bool:
        """d 当日是否在市（含上市当日、不含退市当日）。PIT-008 的判定核心。"""
        listed = self.list_at.date() <= d
        delisted = self.delist_at is not None and self.delist_at.date() <= d
        return listed and not delisted


class AssetIndustryMap(ContractBaseModel):
    """实业→金融标的映射（§7.2 asset_industry_map，版本化）。"""

    asset_id: UpperId
    industry_id: UpperId
    weight: Annotated[float, Field(gt=0.0, le=1.0)]
    valid_from: date
    valid_to: date | None = None


class IndustrySeriesMap(ContractBaseModel):
    """序列→行业映射（§7.2 industry_series_map）。"""

    industry_id: UpperId
    series_id: LowerId
    role: SeriesRole
    weight_policy: NonEmptyStr | None = None
    valid_from: date
    valid_to: date | None = None


class MarketBar(ContractBaseModel):
    """市场行情bar（§7.2 market_bar）：复权方法必须显式声明。"""

    asset_id: UpperId
    trade_date: date  # 交易所交易日历上的日期
    open: Annotated[Decimal, Field(gt=0)]
    high: Annotated[Decimal, Field(gt=0)]
    low: Annotated[Decimal, Field(gt=0)]
    close: Annotated[Decimal, Field(gt=0)]
    volume: Annotated[Decimal, Field(ge=0)]
    amount: Annotated[Decimal, Field(ge=0)] | None = None
    adj_method: NonEmptyStr = "none"  # none/forward/backward——分红处理见 §13.5
    frequency: Frequency = Frequency.DAILY

    @model_validator(mode="after")
    def _ohlc(self) -> MarketBar:
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError("OHLC 违反 low<=open/close<=high")
        return self


class CorporateAction(ContractBaseModel):
    """公司行动（§7.2 corporate_action）：分红/份额变动，回测必须处理（INVEST-002）。"""

    asset_id: UpperId
    ex_date: date  # 除权除息日
    action_type: NonEmptyStr  # cash_dividend / share_split / ...
    distribution_per_unit: Annotated[Decimal, Field(ge=0)]
    currency: NonEmptyStr = "CNY"
    notes: str = ""
