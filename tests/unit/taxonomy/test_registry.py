from datetime import date

import pytest

from industrypulse.taxonomy import TaxonomyRegistry, load_steel_taxonomy


def test_steel_registry_and_explicit_proxies() -> None:
    registry = load_steel_taxonomy()
    assert registry.synthetic
    assert registry.industry("CN_NBS_C31").code == "31"
    assert registry.securities_links[0].is_proxy
    assert (
        registry.industry(registry.securities_links[0].securities_industry_id).standard.value
        == "CSI_INDUSTRY"
    )
    macro = [i for i in registry.indicators_for("CN_NBS_C31") if i.role.value == "macro_proxy"]
    assert macro[0].indicator_id == "macro_pmi_new_orders"
    assert {a.asset_id for a in registry.assets_for("CN_NBS_C31", date(2025, 1, 1))} == {
        "ETF_SH_STEEL_A"
    }


@pytest.mark.parametrize("field", ["nodes", "indicators", "series", "assets"])
def test_duplicate_identity_rejected(field: str) -> None:
    data = load_steel_taxonomy().model_dump(mode="json")
    data[field].append(data[field][0])
    with pytest.raises(ValueError, match="duplicate"):
        TaxonomyRegistry.model_validate(data)


def test_macro_cannot_be_relabelled_and_unit_conflict() -> None:
    data = load_steel_taxonomy().model_dump(mode="json")
    data["indicators"][1]["role"] = "feature"
    with pytest.raises(ValueError, match="MACRO_PROXY"):
        TaxonomyRegistry.model_validate(data)
    data = load_steel_taxonomy().model_dump(mode="json")
    data["indicators"][0]["series_ids"].append(data["series"][1]["series_id"])
    with pytest.raises(ValueError, match="units"):
        TaxonomyRegistry.model_validate(data)
