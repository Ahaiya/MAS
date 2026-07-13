"""
Rubric 核心策略模块，负责维度遍历、量尺读取与描述符检索。

Rubric Core Policy — config 驱动的维度遍历。

提供 rubric 维度的遍历工具，确保所有 agent
通过此策略层迭代维度、读取量尺范围并查找描述符，
而不是直接访问原始的 config dict。

此处不硬编码任何 trait 名称、维度代码、分数值或量尺范围 ——
所有数据均来自 RubricSnapshot。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from src.contracts.artifact_bundle import RubricSnapshot


@dataclass(frozen=True)
class DimensionTraversal:
    """供 worker 使用的预先解析的维度元数据。
    
        Worker 接收 DimensionTraversal 对象而不是原始的 dict，
        确保在任何 worker 运行前，所有量尺范围、facet 和 evidence requirement 都已从 config 中解析完毕。"""

    dimension_id: str
    code: str
    name: str
    scale_ref: str
    scale_min: int
    scale_max: int
    required_facets: List[str]
    evidence_requirements: Dict[str, Any]


def list_dimension_ids(rubric: RubricSnapshot) -> List[str]:
    """返回来自 rubric config 的有序维度 ID 列表。"""
    return [d["dimension_id"] for d in rubric.dimensions]


def get_scale_range(rubric: RubricSnapshot, dimension_id: str) -> Tuple[int, int]:
    """返回维度量尺的 (min, max) 分数范围。
    
        如果找不到 dimension_id 或其 scale_ref，则引发 ValueError。"""
    dim = rubric.dimension_by_id.get(dimension_id)
    if dim is None:
        raise ValueError(f"Dimension '{dimension_id}' not found in rubric")
    scale_ref = dim.get("scale_ref")
    if not scale_ref:
        raise ValueError(f"Dimension '{dimension_id}' has no scale_ref")
    scale = rubric.scale_by_id.get(scale_ref)
    if scale is None:
        raise ValueError(f"Scale '{scale_ref}' not found in rubric")
    return int(scale["min"]), int(scale["max"])


def get_scale_ref(rubric: RubricSnapshot, dimension_id: str) -> str:
    """返回维度的 scale_ref 字符串。
    
        如果找不到 dimension_id 或其没有 scale_ref，则引发 ValueError。"""
    dim = rubric.dimension_by_id.get(dimension_id)
    if dim is None:
        raise ValueError(f"Dimension '{dimension_id}' not found in rubric")
    scale_ref = dim.get("scale_ref")
    if not scale_ref:
        raise ValueError(f"Dimension '{dimension_id}' has no scale_ref")
    return scale_ref


def get_required_facets(rubric: RubricSnapshot, dimension_id: str) -> List[str]:
    """返回维度所需的 observation facet。
    
        如果找不到 dimension_id，则引发 ValueError。"""
    dim = rubric.dimension_by_id.get(dimension_id)
    if dim is None:
        raise ValueError(f"Dimension '{dimension_id}' not found in rubric")
    return list(dim.get("observation_schema", {}).get("required_facets", []))


def get_evidence_requirements(
    rubric: RubricSnapshot, dimension_id: str
) -> Dict[str, Any]:
    """返回维度的 evidence requirement config dict。
    
        如果找不到 dimension_id，则引发 ValueError。"""
    dim = rubric.dimension_by_id.get(dimension_id)
    if dim is None:
        raise ValueError(f"Dimension '{dimension_id}' not found in rubric")
    return dict(dim.get("evidence_requirements", {}))


def get_descriptor_refs_for_score(
    rubric: RubricSnapshot, dimension_id: str, score: int
) -> List[str]:
    """返回维度中给定分数等级的描述符字符串。
    
        查找维度的 levels 列表，并找到 rank 与给定分数匹配的等级。
        返回 descriptors 列表；如果找不到该等级或未定义 levels，
        则返回空列表。
    
        如果找不到 dimension_id，则引发 ValueError。"""
    dim = rubric.dimension_by_id.get(dimension_id)
    if dim is None:
        raise ValueError(f"Dimension '{dimension_id}' not found in rubric")
    for level in dim.get("levels", []):
        if level.get("rank") == score:
            return list(level.get("descriptors", []))
    return []


def validate_score_in_range(
    rubric: RubricSnapshot, dimension_id: str, score: int
) -> bool:
    """检查分数是否在维度的有效量尺范围内。
    
        如果缺少维度或量尺，则返回 False（而不是引发异常）。"""
    try:
        scale_min, scale_max = get_scale_range(rubric, dimension_id)
        return scale_min <= score <= scale_max
    except ValueError:
        return False


def build_dimension_traversal(rubric: RubricSnapshot) -> List[DimensionTraversal]:
    """为所有 rubric 维度构建有序的 DimensionTraversal 列表。
    
        这是需要使用预先解析的元数据迭代所有 rubric 维度的
        agent 的主要入口点。
    
        如果任何维度具有无法解析的 scale_ref，则引发 ValueError。"""
    result: List[DimensionTraversal] = []
    for dim in rubric.dimensions:
        dim_id = dim["dimension_id"]
        scale_min, scale_max = get_scale_range(rubric, dim_id)
        result.append(
            DimensionTraversal(
                dimension_id=dim_id,
                code=dim.get("code", ""),
                name=dim.get("name", ""),
                scale_ref=dim.get("scale_ref", ""),
                scale_min=scale_min,
                scale_max=scale_max,
                required_facets=list(
                    dim.get("observation_schema", {}).get("required_facets", [])
                ),
                evidence_requirements=dict(dim.get("evidence_requirements", {})),
            )
        )
    return result
