"""Explicit Decimal unit conversion; missing observations remain missing."""

from decimal import Decimal

_UNITS = {
    "元": ("currency", Decimal("1")),
    "万元": ("currency", Decimal("10000")),
    "亿元": ("currency", Decimal("100000000")),
    "吨": ("mass", Decimal("1")),
    "万吨": ("mass", Decimal("10000")),
    "元/吨": ("price", Decimal("1")),
    "百分比": ("percentage", Decimal("1")),
    "%": ("percentage", Decimal("1")),
    "点": ("index", Decimal("1")),
}


def convert_value(value: Decimal | None, from_unit: str, to_unit: str) -> Decimal | None:
    """Convert explicitly declared units, never infer units or substitute zero."""
    if from_unit not in _UNITS or to_unit not in _UNITS:
        raise ValueError("unregistered unit")
    source_dimension, source_scale = _UNITS[from_unit]
    target_dimension, target_scale = _UNITS[to_unit]
    if source_dimension != target_dimension:
        raise ValueError("incompatible unit dimensions")
    if value is None:
        return None
    if not value.is_finite():
        raise ValueError("value must be finite")
    return value * source_scale / target_scale
