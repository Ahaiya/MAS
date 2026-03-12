"""
Mock Coverage Planner — deterministic CoveragePlan generation.

Produces one CoveragePlan per rubric dimension, targeting all available text
units. Required facets and scale info are read from the RubricSnapshot (config-
driven, zero-hardcoding). Plan IDs are derived from content hashes.
"""

from __future__ import annotations

import hashlib
from typing import List

from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.request_models import CoveragePlan, NormalizedDocument


def _hid(seed: str, length: int = 12) -> str:
    return hashlib.md5(seed.encode()).hexdigest()[:length]


def run(document: NormalizedDocument, rubric: RubricSnapshot) -> List[CoveragePlan]:
    """Generate one CoveragePlan per dimension in the rubric.

    Args:
        document: The segmented NormalizedDocument to plan coverage for.
        rubric: RubricSnapshot supplying dimension definitions and required facets.

    Returns:
        List of CoveragePlan objects, one per rubric dimension.
    """
    target_unit_ids = [u.unit_id for u in document.text_units]

    plans: List[CoveragePlan] = []
    for dim in rubric.dimensions:
        dim_id: str = dim["dimension_id"]
        required_facets: List[str] = list(
            dim.get("observation_schema", {}).get("required_facets", [])
        )
        minimum_evidence_units = max(1, len(required_facets))

        plan_id = f"plan-{_hid(f'{document.document_id}:{dim_id}')}"
        plans.append(
            CoveragePlan(
                plan_id=plan_id,
                document_id=document.document_id,
                dimension_id=dim_id,
                target_unit_ids=list(target_unit_ids),
                required_facets=required_facets,
                minimum_evidence_units=minimum_evidence_units,
                allowed_evidence_scopes=["span", "global"],
                coverage_strategy="full_scan",
            )
        )

    return plans
