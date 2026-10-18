from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from industrypulse.repositories import convert_value
from industrypulse.taxonomy import load_steel_taxonomy


@given(
    st.decimals(
        min_value="-100000000",
        max_value="100000000",
        allow_nan=False,
        allow_infinity=False,
        places=4,
    )
)
def test_yi_wan_unit_conversion_preserves_negative_zero_and_positive(value: Decimal) -> None:
    standard_from_yi = convert_value(value, "亿元", "元")
    wan = convert_value(value, "亿元", "万元")
    assert convert_value(wan, "万元", "元") == standard_from_yi
    assert standard_from_yi is not None
    assert (standard_from_yi < 0) == (value < 0)


def test_missing_and_dimension_conflict() -> None:
    assert convert_value(None, "亿元", "万元") is None
    assert convert_value(Decimal("0"), "亿元", "万元") == Decimal("0")
    with pytest.raises(ValueError, match="dimensions"):
        convert_value(Decimal("3"), "亿元", "吨")


@pytest.mark.parametrize("scale", ["0", "-1", "-10000"])
def test_invalid_unit_scale_rejected(scale: str) -> None:
    series = load_steel_taxonomy().series[0]
    with pytest.raises(ValueError, match="unit_scale"):
        type(series).model_validate({**series.model_dump(), "unit_scale": scale})
