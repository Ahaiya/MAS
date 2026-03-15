"""
Rubric Core Policy — config-driven dimension traversal.

Provides traversal utilities for rubric dimensions, ensuring all agents
iterate dimensions, read scale ranges, and look up descriptors through
this policy layer rather than accessing raw config dicts directly.

No trait names, dimension codes, score values, or scale ranges are
hardcoded here — all data comes from RubricSnapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from src.contracts.artifact_bundle import RubricSnapshot


@dataclass(frozen=True)
class DimensionTraversal:
    """Pre-resolved dimension metadata for worker consumption.

    Workers receive DimensionTraversal objects instead of raw dicts,
    ensuring all scale ranges, facets, and evidence requirements are
    resolved from config before any worker runs.
    """

    dimension_id: str
    code: str
    name: str
    scale_ref: str
    scale_min: int
    scale_max: int
    required_facets: List[str]
    evidence_requirements: Dict[str, Any]


def list_dimension_ids(rubric: RubricSnapshot) -> List[str]:
    """Return ordered list of dimension IDs from the rubric config."""
    return [d["dimension_id"] for d in rubric.dimensions]


def get_scale_range(rubric: RubricSnapshot, dimension_id: str) -> Tuple[int, int]:
    """Return (min, max) score range for a dimension's scale.

    Raises ValueError if dimension_id or its scale_ref is not found.
    """
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
    """Return the scale_ref string for a dimension.

    Raises ValueError if dimension_id is not found or has no scale_ref.
    """
    dim = rubric.dimension_by_id.get(dimension_id)
    if dim is None:
        raise ValueError(f"Dimension '{dimension_id}' not found in rubric")
    scale_ref = dim.get("scale_ref")
    if not scale_ref:
        raise ValueError(f"Dimension '{dimension_id}' has no scale_ref")
    return scale_ref


def get_required_facets(rubric: RubricSnapshot, dimension_id: str) -> List[str]:
    """Return required observation facets for a dimension.

    Raises ValueError if dimension_id is not found.
    """
    dim = rubric.dimension_by_id.get(dimension_id)
    if dim is None:
        raise ValueError(f"Dimension '{dimension_id}' not found in rubric")
    return list(dim.get("observation_schema", {}).get("required_facets", []))


def get_evidence_requirements(
    rubric: RubricSnapshot, dimension_id: str
) -> Dict[str, Any]:
    """Return evidence requirements config dict for a dimension.

    Raises ValueError if dimension_id is not found.
    """
    dim = rubric.dimension_by_id.get(dimension_id)
    if dim is None:
        raise ValueError(f"Dimension '{dimension_id}' not found in rubric")
    return dict(dim.get("evidence_requirements", {}))


def get_descriptor_refs_for_score(
    rubric: RubricSnapshot, dimension_id: str, score: int
) -> List[str]:
    """Return descriptor strings for a given score level in a dimension.

    Looks up the dimension's levels list and finds the level whose rank
    matches the given score. Returns the descriptors list, or an empty
    list if the level is not found or levels are not defined.

    Raises ValueError if dimension_id is not found.
    """
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
    """Check if score is within the valid scale range for a dimension.

    Returns False (rather than raising) if dimension or scale is missing.
    """
    try:
        scale_min, scale_max = get_scale_range(rubric, dimension_id)
        return scale_min <= score <= scale_max
    except ValueError:
        return False


def build_dimension_traversal(rubric: RubricSnapshot) -> List[DimensionTraversal]:
    """Build ordered list of DimensionTraversal for all rubric dimensions.

    This is the primary entry point for agents that need to iterate
    over all rubric dimensions with pre-resolved metadata.

    Raises ValueError if any dimension has an unresolvable scale_ref.
    """
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
