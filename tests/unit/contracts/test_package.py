import dataclasses

import pytest

from src.contracts.package import DataPackage, Unit


def _unit(id: int = 1, type: str = "text") -> Unit:
    return Unit(
        id=id,
        markdown="hello",
        type=type,
        source_file="a.pdf",
        page=0,
    )


def test_unit_constructs_with_valid_fields() -> None:
    unit = _unit()
    assert unit.id == 1
    assert unit.type == "text"
    assert unit.markdown == "hello"
    assert unit.page == 0


def test_unit_is_immutable() -> None:
    unit = _unit()
    with pytest.raises(dataclasses.FrozenInstanceError):
        unit.markdown = "changed"  # type: ignore[misc]


def test_unit_accepts_any_type_value() -> None:
    """type 是 API 原值，本系统不维护取值表——白名单过滤发生在映射层。"""
    assert _unit(type="table_note").type == "table_note"


def test_unit_round_trips_through_dict() -> None:
    unit = Unit(id=3, markdown="## 标题", type="title", source_file="报告.pdf", page=2)
    assert Unit.from_dict(unit.to_dict()) == unit


def test_data_package_constructs_with_units() -> None:
    package = DataPackage(
        package_id="experiment/2025213223",
        units=[_unit(id=1), _unit(id=2)],
        provenance={"source_files": ["a.pdf"]},
    )
    assert package.package_id == "experiment/2025213223"
    assert len(package.units) == 2
    assert package.get_unit(2).id == 2
    assert package.get_unit(99) is None


def test_data_package_is_immutable() -> None:
    package = DataPackage(package_id="t/s", units=[], provenance={})
    with pytest.raises(dataclasses.FrozenInstanceError):
        package.package_id = "other"  # type: ignore[misc]


def test_data_package_rejects_duplicate_unit_ids() -> None:
    with pytest.raises(ValueError):
        DataPackage(package_id="t/s", units=[_unit(id=1), _unit(id=1)], provenance={})


def test_data_package_round_trips_through_dict() -> None:
    """from_dict 是把产物里的 unit_ids 解读回原文的唯一入口，字段必须无损往返。"""
    package = DataPackage(
        package_id="experiment/2025213223",
        units=[_unit(id=0), _unit(id=1, type="table")],
        provenance={"parsed_at": "2026-08-04T10:00:00", "excluded_layouts": {"foot": 3}},
    )
    assert DataPackage.from_dict(package.to_dict()) == package
