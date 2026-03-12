"""
Mock Evidence Extractor — deterministic EvidenceSpan generation.

Produces one GLOBAL-scope EvidenceSpan per required facet in the CoveragePlan.
Span IDs are derived from (plan_id + facet_id) hashes, ensuring that:
- The same plan always yields the same span IDs.
- Different plans yield different span IDs (disjoint sets).
No real NLP or LLM processing is performed.
"""

from __future__ import annotations

import hashlib
from typing import List

from src.contracts.evidence import EvidenceScope, EvidenceSpan
from src.contracts.request_models import CoveragePlan, NormalizedDocument


def _hid(seed: str, length: int = 12) -> str:
    return hashlib.md5(seed.encode()).hexdigest()[:length]


def run(plan: CoveragePlan, document: NormalizedDocument) -> List[EvidenceSpan]:
    """Extract one mock evidence span per required facet in the plan.

    Uses GLOBAL scope (no character offsets required) for simplicity.
    Each span references exactly one facet from plan.required_facets.

    Args:
        plan: The CoveragePlan specifying which facets must be covered.
        document: The NormalizedDocument (used for document_id reference).

    Returns:
        List of EvidenceSpan objects, one per required facet.
    """
    spans: List[EvidenceSpan] = []
    for facet_id in plan.required_facets:
        span_id = f"span-{_hid(f'{plan.plan_id}:{facet_id}')}"
        spans.append(
            EvidenceSpan(
                span_id=span_id,
                document_id=plan.document_id,
                unit_id=None,
                text_quote=None,
                start_offset=None,
                end_offset=None,
                scope=EvidenceScope.GLOBAL,
                dimension_id=plan.dimension_id,
                facet_ids=[facet_id],
                extraction_note="mock_deterministic",
            )
        )
    return spans
