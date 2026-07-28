"""量规结构校验：缺字段必须报错，不能静默降级。

这些用例守的是一个具体事故：量规缺 anchors 时，锚点会被过滤成空，模型在完全看不到
评分标准的情况下打分，跑完、出分、产物上一点痕迹都没有。所以每个用例都断言"抛错"
而不是"回落默认值"。
"""

from __future__ import annotations

import copy
from typing import Any, Dict

import pytest

from src.config.compiler import ConfigCompileError
from src.config.rubric_validation import validate_rubric

_VALID: Dict[str, Any] = {
    "dim_id": "f2",
    "dim_name": "F2 工程伦理与社会责任",
    "indicator_description": "在工程实践中，能够理解并遵守相关法律与伦理规范。",
    "scale": {
        "min": 1,
        "max": 4,
        "levels": {4: "优秀", 3: "良好", 2: "合格", 1: "待改进"},
    },
    "dimensions": [
        {
            "code": "F2-1",
            "name": "数据真实性与学术诚信",
            "weight": 0.4,
            "anchors": {4: "四档锚点", 3: "三档锚点", 2: "二档锚点", 1: "一档锚点"},
        },
        {
            "code": "F2-2",
            "name": "对AI伦理问题的认知",
            "weight": 0.6,
            "anchors": {4: "四档锚点", 3: "三档锚点", 2: "二档锚点", 1: "一档锚点"},
        },
    ],
}


def _rubric(**overrides: Any) -> Dict[str, Any]:
    data = copy.deepcopy(_VALID)
    data.update(overrides)
    return data


def _without(mapping: Dict[str, Any], key: str) -> Dict[str, Any]:
    out = copy.deepcopy(mapping)
    out.pop(key)
    return out


# ── 通过 ─────────────────────────────────────────────────────────────────────


def test_valid_rubric_passes() -> None:
    validate_rubric(_VALID, source="f2_rubric.yaml")


def test_all_shipped_rubrics_pass() -> None:
    """现役 7 份量规必须全部通过——否则这份校验就是把生产配置判死刑。"""
    import yaml

    from src.config.compiler import list_task_dimension_ids

    root = "configs"
    for dim_id in list_task_dimension_ids(root, "experiment"):
        path = f"{root}/tasks/experiment/dimension/{dim_id}_rubric.yaml"
        with open(path, encoding="utf-8") as handle:
            validate_rubric(yaml.safe_load(handle), source=path)


# ── 顶层字段 ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("field", ["dim_id", "dim_name", "indicator_description"])
def test_missing_top_level_field_raises(field: str) -> None:
    with pytest.raises(ConfigCompileError) as exc:
        validate_rubric(_without(_VALID, field), source="bad.yaml")
    assert field in str(exc.value)
    assert "bad.yaml" in str(exc.value)


@pytest.mark.parametrize("field", ["dim_id", "dim_name", "indicator_description"])
def test_blank_top_level_field_raises(field: str) -> None:
    with pytest.raises(ConfigCompileError):
        validate_rubric(_rubric(**{field: "   "}), source="bad.yaml")


def test_non_mapping_rubric_raises() -> None:
    with pytest.raises(ConfigCompileError):
        validate_rubric(["not", "a", "mapping"], source="bad.yaml")


# ── scale ────────────────────────────────────────────────────────────────────


def test_missing_scale_raises() -> None:
    with pytest.raises(ConfigCompileError) as exc:
        validate_rubric(_without(_VALID, "scale"), source="bad.yaml")
    assert "scale" in str(exc.value)


@pytest.mark.parametrize("field", ["min", "max", "levels"])
def test_missing_scale_field_raises(field: str) -> None:
    with pytest.raises(ConfigCompileError) as exc:
        validate_rubric(_rubric(scale=_without(_VALID["scale"], field)), source="bad.yaml")
    assert field in str(exc.value)


def test_scale_max_not_greater_than_min_raises() -> None:
    with pytest.raises(ConfigCompileError):
        validate_rubric(_rubric(scale={"min": 4, "max": 4, "levels": {4: "优秀"}}), source="bad.yaml")


def test_levels_missing_a_rank_raises() -> None:
    """档位标签要进锚点文本，缺一档就是缺一个标签。"""
    scale = copy.deepcopy(_VALID["scale"])
    del scale["levels"][2]
    with pytest.raises(ConfigCompileError) as exc:
        validate_rubric(_rubric(scale=scale), source="bad.yaml")
    assert "levels" in str(exc.value)
    assert "2" in str(exc.value)


# ── dimensions（观测点） ─────────────────────────────────────────────────────


def test_missing_dimensions_raises() -> None:
    with pytest.raises(ConfigCompileError):
        validate_rubric(_without(_VALID, "dimensions"), source="bad.yaml")


def test_empty_dimensions_raises() -> None:
    with pytest.raises(ConfigCompileError):
        validate_rubric(_rubric(dimensions=[]), source="bad.yaml")


@pytest.mark.parametrize("field", ["code", "name", "weight", "anchors"])
def test_missing_observation_point_field_raises(field: str) -> None:
    dims = copy.deepcopy(_VALID["dimensions"])
    dims[1].pop(field)
    with pytest.raises(ConfigCompileError) as exc:
        validate_rubric(_rubric(dimensions=dims), source="bad.yaml")
    assert field in str(exc.value)
    assert "F2-2" in str(exc.value) or "1" in str(exc.value)


def test_missing_anchor_rank_raises() -> None:
    """这是最要命的一条：缺锚点会让模型在没有量规的情况下打分。"""
    dims = copy.deepcopy(_VALID["dimensions"])
    del dims[0]["anchors"][3]
    with pytest.raises(ConfigCompileError) as exc:
        validate_rubric(_rubric(dimensions=dims), source="bad.yaml")
    assert "anchors" in str(exc.value)
    assert "F2-1" in str(exc.value)


def test_blank_anchor_text_raises() -> None:
    """空字符串锚点和缺字段等价——渲染时同样会被过滤掉。"""
    dims = copy.deepcopy(_VALID["dimensions"])
    dims[0]["anchors"][3] = "   "
    with pytest.raises(ConfigCompileError):
        validate_rubric(_rubric(dimensions=dims), source="bad.yaml")


def test_duplicate_observation_point_code_raises() -> None:
    """同 code 会在 dimension_by_id 里互相覆盖，静默丢掉一个观测点。"""
    dims = copy.deepcopy(_VALID["dimensions"])
    dims[1]["code"] = "F2-1"
    with pytest.raises(ConfigCompileError) as exc:
        validate_rubric(_rubric(dimensions=dims), source="bad.yaml")
    assert "F2-1" in str(exc.value)


# ── 权重 ─────────────────────────────────────────────────────────────────────


def test_weights_not_summing_to_one_raises() -> None:
    dims = copy.deepcopy(_VALID["dimensions"])
    dims[0]["weight"] = 0.5  # 0.5 + 0.6 = 1.1
    with pytest.raises(ConfigCompileError) as exc:
        validate_rubric(_rubric(dimensions=dims), source="bad.yaml")
    assert "weight" in str(exc.value)
    assert "1.1" in str(exc.value)


def test_weights_summing_to_one_with_float_noise_passes() -> None:
    """0.25+0.25+0.3+0.2 在浮点下不一定精确等于 1.0，不能用 == 判。"""
    dims = [
        {"code": f"X-{i}", "name": f"点{i}", "weight": w, "anchors": dict(_VALID["dimensions"][0]["anchors"])}
        for i, w in enumerate([0.25, 0.25, 0.3, 0.2], start=1)
    ]
    validate_rubric(_rubric(dimensions=dims), source="ok.yaml")


def test_non_numeric_weight_raises() -> None:
    dims = copy.deepcopy(_VALID["dimensions"])
    dims[0]["weight"] = "0.4"
    with pytest.raises(ConfigCompileError):
        validate_rubric(_rubric(dimensions=dims), source="bad.yaml")
