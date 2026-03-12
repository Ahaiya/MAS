"""
Mock Observation Builder — deterministic DimensionObservation assembly.

Groups EvidenceSpans by their facet_ids and constructs one FacetFinding per
required facet from the CoveragePlan. Observation confidence is derived from
the fraction of required facets that have at least one supporting span:
  - All facets covered  → HIGH
  - Some facets covered → MEDIUM
  - No facets covered   → LOW

Observation ID is derived from the plan ID and span IDs (deterministic).
"""

from __future__ import annotations

import hashlib
from typing import Dict, List

from src.contracts.evidence import (
    DimensionObservation,
    EvidenceSpan,
    FacetFinding,
    ObservationConfidence,
)
from src.contracts.request_models import CoveragePlan


def _hid(seed: str, length: int = 12) -> str:
    return hashlib.md5(seed.encode()).hexdigest()[:length]


def run(spans: List[EvidenceSpan], plan: CoveragePlan) -> DimensionObservation:
    """Build a DimensionObservation from evidence spans and coverage plan.

    Args:
        spans: EvidenceSpans extracted for this dimension.
        plan: The CoveragePlan that defines required facets.

    Returns:
        A DimensionObservation with FacetFindings for every required facet.
    """
    # Map facet_id -> span_ids
    facet_to_span_ids: Dict[str, List[str]] = {}
    for span in spans:
        for facet_id in span.facet_ids:
            facet_to_span_ids.setdefault(facet_id, []).append(span.span_id)

    # Build one FacetFinding per required facet
    facet_findings: List[FacetFinding] = []
    for facet_id in plan.required_facets:
        facet_findings.append(
            FacetFinding(
                facet_id=facet_id,
                supporting_span_ids=facet_to_span_ids.get(facet_id, []),
                counter_span_ids=[],
                finding_note="mock",
            )
        )

    # Determine confidence from facet coverage
    covered = sum(1 for ff in facet_findings if ff.supporting_span_ids)
    total = len(plan.required_facets)
    if total == 0 or covered == total:
        confidence = ObservationConfidence.HIGH
    elif covered > 0:
        confidence = ObservationConfidence.MEDIUM
    else:
        confidence = ObservationConfidence.LOW

    # Deterministic observation_id from plan + sorted span IDs
    span_seed = ":".join(sorted(s.span_id for s in spans))
    observation_id = f"obs-{_hid(f'{plan.plan_id}:{span_seed}')}"

    all_span_ids = [s.span_id for s in spans]

    return DimensionObservation(
        observation_id=observation_id,
        document_id=plan.document_id,
        dimension_id=plan.dimension_id,
        supporting_span_ids=all_span_ids,
        counter_span_ids=[],
        facet_findings=facet_findings,
        observation_confidence=confidence,
        uncertainty_notes=[],
    )
