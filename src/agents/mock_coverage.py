"""
Mock Coverage Planner — deterministic CoveragePlan generation.

Produces one CoveragePlan per rubric dimension, targeting all available text
units. Dimension traversal is delegated to ``src.policies.rubric_core``
(config-driven, zero-hardcoding). Plan IDs are derived from content hashes.
"""

from __future__ import annotations

import hashlib
from typing import List

from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.request_models import CoveragePlan, NormalizedDocument
from src.policies.rubric_core import build_dimension_traversal


def _hid(seed: str, length: int = 12) -> str:
    return hashlib.md5(seed.encode()).hexdigest()[:length]


def run(document: NormalizedDocument, rubric: RubricSnapshot) -> List[CoveragePlan]:
    """Generate one CoveragePlan per dimension in the rubric.

    Dimension iteration and facet resolution are handled by
    ``rubric_core.build_dimension_traversal`` — no raw dict access here.

    Args:
        document: The segmented NormalizedDocument to plan coverage for.
        rubric: RubricSnapshot supplying dimension definitions and required facets.

    Returns:
        List of CoveragePlan objects, one per rubric dimension.
    """
    target_unit_ids = [u.unit_id for u in document.text_units]
    traversals = build_dimension_traversal(rubric)

    plans: List[CoveragePlan] = []
    for trav in traversals:
        plan_id = f"plan-{_hid(f'{document.document_id}:{trav.dimension_id}')}"
        plans.append(
            CoveragePlan(
                plan_id=plan_id,
                document_id=document.document_id,
                dimension_id=trav.dimension_id,
                target_unit_ids=list(target_unit_ids),
                required_facets=list(trav.required_facets),
                minimum_evidence_units=max(1, len(trav.required_facets)),
                allowed_evidence_scopes=["span", "global"],
                coverage_strategy="full_scan",
            )
        )

    return plans
