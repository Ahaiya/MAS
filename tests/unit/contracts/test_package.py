import dataclasses

import pytest

from src.contracts.package import DataPackage, Unit


def _unit(id: int = 1, kind: str = "prose", speaker=None) -> Unit:
    return Unit(
        id=id,
        kind=kind,
        text="hello",
        source_file="a.md",
        char_range=(0, 5),
        speaker=speaker,
    )


def test_unit_constructs_with_valid_fields() -> None:
    unit = _unit()
    assert unit.id == 1
    assert unit.kind == "prose"
    assert unit.char_range == (0, 5)
    assert unit.speaker is None


def test_unit_is_immutable() -> None:
    unit = _unit()
    with pytest.raises(dataclasses.FrozenInstanceError):
        unit.text = "changed"  # type: ignore[misc]


def test_unit_rejects_invalid_kind() -> None:
    with pytest.raises(ValueError):
        _unit(kind="paragraph")


def test_unit_rejects_empty_char_range() -> None:
    with pytest.raises(ValueError):
        Unit(id=1, kind="prose", text="x", source_file="a.md", char_range=(5, 5), speaker=None)


def test_data_package_constructs_with_units() -> None:
    package = DataPackage(
        package_id="pkg-1",
        units=[_unit(id=1), _unit(id=2)],
        metadata={"student_id": "s1"},
    )
    assert package.package_id == "pkg-1"
    assert len(package.units) == 2
    assert package.get_unit(2).id == 2
    assert package.get_unit(99) is None


def test_data_package_is_immutable() -> None:
    package = DataPackage(package_id="pkg-1", units=[], metadata={})
    with pytest.raises(dataclasses.FrozenInstanceError):
        package.package_id = "other"  # type: ignore[misc]


def test_data_package_rejects_duplicate_unit_ids() -> None:
    with pytest.raises(ValueError):
        DataPackage(package_id="pkg-1", units=[_unit(id=1), _unit(id=1)], metadata={})


def test_data_package_allows_non_contiguous_ids() -> None:
    """超预算丢弃单元后，剩余单元编号允许出现空洞。"""
    package = DataPackage(package_id="pkg-1", units=[_unit(id=1), _unit(id=5)], metadata={})
    assert [u.id for u in package.units] == [1, 5]
