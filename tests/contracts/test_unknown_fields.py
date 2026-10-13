"""C-002/C-003 验收：未知字段拒收 + schema roundtrip + 模型覆盖完整性。"""

from __future__ import annotations

import pytest
from instances import canonical_instances
from pydantic import ValidationError

from industrypulse.contracts import ContractBaseModel
from industrypulse.contracts import __all__ as contracts_all


def _all_model_names() -> set[str]:
    """contracts 包导出的全部 ContractBaseModel 子类名（覆盖完整性基准）。"""
    import industrypulse.contracts as pkg

    names = set()
    for attr in contracts_all:
        obj = getattr(pkg, attr)
        if (
            isinstance(obj, type)
            and issubclass(obj, ContractBaseModel)
            and obj is not ContractBaseModel
        ):
            names.add(obj.__name__)
    return names


def test_instances_cover_all_models() -> None:
    """守护：新合同模型必须补标准实例，否则失去拒收/roundtrip 覆盖。"""
    missing = _all_model_names() - set(canonical_instances())
    assert not missing, f"缺少标准实例的合同模型：{sorted(missing)}"


@pytest.mark.parametrize("name,instance", sorted(canonical_instances().items()))
def test_unknown_field_rejected(name: str, instance: ContractBaseModel) -> None:
    """extra=forbid：任何未知字段必须 ValidationError（C-002 验收）。"""
    dumped = instance.model_dump()
    if not isinstance(dumped, dict):  # pragma: no cover - 防御
        pytest.skip("非 dict 形状")
    dumped["rogue_field_g0"] = 1
    with pytest.raises(ValidationError, match="rogue_field_g0"):
        type(instance).model_validate(dumped)


@pytest.mark.parametrize("name,instance", sorted(canonical_instances().items()))
def test_roundtrip_python(name: str, instance: ContractBaseModel) -> None:
    """model_dump → model_validate 逐字段等价（schema roundtrip 验收）。"""
    cls = type(instance)
    again = cls.model_validate(instance.model_dump())
    assert again == instance, f"{name} python roundtrip 不等价"


@pytest.mark.parametrize("name,instance", sorted(canonical_instances().items()))
def test_roundtrip_json(name: str, instance: ContractBaseModel) -> None:
    """JSON 序列化往返等价（合同工件的磁盘形态）。"""
    cls = type(instance)
    payload = instance.model_dump_json()
    again = cls.model_validate_json(payload)
    assert again == instance, f"{name} json roundtrip 不等价\npayload={payload[:400]}"


@pytest.mark.parametrize("name,instance", sorted(canonical_instances().items()))
def test_frozen(name: str, instance: ContractBaseModel) -> None:
    """合同实例不可变（§2.3 溯源纪律）。"""
    first_field = next(iter(type(instance).model_fields))
    with pytest.raises(ValidationError):
        setattr(instance, first_field, None)
