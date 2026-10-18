"""Versioned taxonomy registry with explicit proxies and classification standards."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pydantic import Field, model_validator

from industrypulse.contracts.common import ContractBaseModel, NonEmptyStr
from industrypulse.contracts.domain import (
    AssetIndustryMap,
    AssetMaster,
    IndicatorSpec,
    IndustryNode,
    IndustryRelation,
    IndustrySeriesMap,
    IndustryStandard,
    SeriesRole,
)
from industrypulse.contracts.sources import SeriesSpec


class SecuritiesIndustryLink(ContractBaseModel):
    statistical_industry_id: NonEmptyStr
    securities_industry_id: NonEmptyStr
    source_ref: NonEmptyStr
    valid_from: date
    valid_to: date | None = None
    is_proxy: bool = True
    notes: str = ""
    asset_ids: list[str] = Field(default_factory=list)


class TaxonomyRegistry(ContractBaseModel):
    version: NonEmptyStr
    synthetic: bool = False
    nodes: list[IndustryNode]
    relations: list[IndustryRelation] = Field(default_factory=list)
    indicators: list[IndicatorSpec] = Field(default_factory=list)
    series: list[SeriesSpec] = Field(default_factory=list)
    series_maps: list[IndustrySeriesMap] = Field(default_factory=list)
    assets: list[AssetMaster] = Field(default_factory=list)
    asset_maps: list[AssetIndustryMap] = Field(default_factory=list)
    securities_links: list[SecuritiesIndustryLink] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_registry(self) -> TaxonomyRegistry:
        def unique(items: list[str], label: str) -> None:
            if len(set(items)) != len(items):
                raise ValueError(f"duplicate {label}")

        if not self.nodes:
            raise ValueError("industry nodes required")
        unique([n.industry_id for n in self.nodes], "industry_id")
        unique([i.indicator_id for i in self.indicators], "indicator_id")
        unique([s.series_id for s in self.series], "series_id")
        unique([a.asset_id for a in self.assets], "asset_id")
        nodes = {n.industry_id: n for n in self.nodes}
        series = {s.series_id: s for s in self.series}
        assets = {a.asset_id: a for a in self.assets}
        for asset in self.assets:
            if asset.tracking_index_id is not None:
                index = assets.get(asset.tracking_index_id)
                if index is None or index.instrument_type.value != "INDEX":
                    raise ValueError("tracking index must be registered as INDEX")
        for node in self.nodes:
            seen = {node.industry_id}
            parent = node.parent_id
            while parent:
                if parent not in nodes or parent in seen:
                    raise ValueError("dangling or cyclic industry parent")
                seen.add(parent)
                parent = nodes[parent].parent_id
        for relation in self.relations:
            if relation.upstream_id not in nodes or relation.downstream_id not in nodes:
                raise ValueError("dangling industry relation")
            if relation.upstream_id == relation.downstream_id:
                raise ValueError("self industry relation")
            if relation.valid_to and relation.valid_to < relation.valid_from:
                raise ValueError("invalid relation dates")
        for indicator in self.indicators:
            if not set(indicator.industry_ids) <= nodes.keys():
                raise ValueError("indicator references unknown industry")
            if not set(indicator.series_ids) <= series.keys():
                raise ValueError("indicator references unregistered series")
            units = {
                (series[s].unit, series[s].unit_scale, series[s].currency)
                for s in indicator.series_ids
            }
            if len(units) != 1:
                raise ValueError("indicator combines incompatible units")
            for sid in indicator.series_ids:
                if "macro_proxy" in series[sid].tags and indicator.role != SeriesRole.MACRO_PROXY:
                    raise ValueError("macro series must be explicitly MACRO_PROXY")
        unique(
            [f"{m.industry_id}/{m.series_id}/{m.valid_from}" for m in self.series_maps],
            "series mapping",
        )
        for mapping in self.series_maps:
            if mapping.industry_id not in nodes or mapping.series_id not in series:
                raise ValueError("dangling industry-series mapping")
            if mapping.valid_to and mapping.valid_to < mapping.valid_from:
                raise ValueError("invalid series mapping dates")
            if "macro_proxy" in series[mapping.series_id].tags:
                if mapping.role != SeriesRole.MACRO_PROXY:
                    raise ValueError("macro mapping must be explicitly MACRO_PROXY")
        for asset_map in self.asset_maps:
            if asset_map.asset_id not in assets or asset_map.industry_id not in nodes:
                raise ValueError("dangling asset mapping")
            if asset_map.valid_to and asset_map.valid_to < asset_map.valid_from:
                raise ValueError("invalid asset mapping dates")
            if asset_map.valid_from < assets[asset_map.asset_id].list_at.date():
                raise ValueError("asset mapping predates listing")
        unique(
            [f"{m.asset_id}/{m.industry_id}/{m.valid_from}" for m in self.asset_maps],
            "asset mapping",
        )
        for link in self.securities_links:
            statistical = nodes.get(link.statistical_industry_id)
            securities = nodes.get(link.securities_industry_id)
            if statistical is None or securities is None:
                raise ValueError("dangling cross-standard mapping")
            if statistical.standard == securities.standard:
                raise ValueError("cross-standard mapping requires different standards")
            if securities.standard != IndustryStandard.CSI_INDUSTRY:
                raise ValueError("securities node must declare CSI_INDUSTRY")
            if link.valid_to and link.valid_to < link.valid_from:
                raise ValueError("invalid cross-standard mapping dates")
            for asset_id in link.asset_ids:
                if asset_id not in assets or not any(
                    m.asset_id == asset_id and m.industry_id == link.statistical_industry_id
                    for m in self.asset_maps
                ):
                    raise ValueError("cross-standard asset must have statistical mapping")
        return self

    @classmethod
    def load(cls, path: str | Path) -> TaxonomyRegistry:
        path = Path(path)
        content = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            data = json.loads(content)
        else:
            import yaml  # type: ignore[import-untyped]

            data = yaml.safe_load(content)
        return cls.model_validate(data)

    def industry(self, industry_id: str, as_of: date | None = None) -> IndustryNode:
        for node in self.nodes:
            if node.industry_id == industry_id:
                if as_of is not None and (
                    as_of < node.valid_from or (node.valid_to is not None and as_of > node.valid_to)
                ):
                    raise KeyError(f"industry not effective: {industry_id}")
                return node
        raise KeyError(industry_id)

    def indicators_for(self, industry_id: str, as_of: date | None = None) -> list[IndicatorSpec]:
        self.industry(industry_id, as_of)
        return [
            i
            for i in self.indicators
            if industry_id in i.industry_ids and (as_of is None or i.valid_from <= as_of)
        ]

    def assets_for(self, industry_id: str, as_of: date) -> list[AssetMaster]:
        self.industry(industry_id, as_of)
        ids = {
            m.asset_id
            for m in self.asset_maps
            if m.industry_id == industry_id
            and m.valid_from <= as_of
            and (m.valid_to is None or as_of <= m.valid_to)
        }
        return [a for a in self.assets if a.asset_id in ids and a.is_listed_on(as_of)]


def load_steel_taxonomy(path: str | Path | None = None) -> TaxonomyRegistry:
    """Load the explicit G0-derived synthetic demonstration registry."""
    default = Path(__file__).resolve().parents[3] / "config/industries/steel.yaml"
    return TaxonomyRegistry.load(path or default)
