"""
Feedback Assembler — policy-enforced explanation generation.

Policy-aware version of the feedback agent (Phase 4). Uses explanation policy
from PolicySnapshot to render DimensionExplanations, enforce citation chains,
and assemble the final feedback dict.

Compared to mock_feedback.py (Phase 3):
  - Accepts EvidenceSpan[] and PolicySnapshot as additional inputs.
  - Each dimension entry now includes descriptor_refs, evidence_span_ids,
    commentary, and uncertainty_note.
  - All policy violations are surfaced in the output "violations" key.

Output schema (Dict[str, Any]):
  {
    "dimensions": {
      "<dimension_id>": {
        "dimension_name":    str,       # from rubric config
        "canonical_score":   int,
        "display_score":     str,
        "descriptor_refs":   List[str], # from decision + policy enforcement
        "evidence_span_ids": List[str],
        "commentary":        str,
        "uncertainty_note":  str | None,
        "decision_confidence": float,
      },
      ...
    },
    "violations": List[Dict],   # serialised ExplanationViolation dicts
    "generated_at": str,        # ISO 8601 UTC timestamp
  }
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from src.contracts.artifact_bundle import PolicySnapshot, RubricSnapshot
from src.contracts.evidence import DimensionObservation, EvidenceSpan
from src.contracts.scoring import FinalDimensionDecision
from src.policies.explanation import (
    enforce_explanation_policy,
    render_dimension_explanation,
)


def run(
    decisions: List[FinalDimensionDecision],
    observations: List[DimensionObservation],
    spans: List[EvidenceSpan],
    rubric: RubricSnapshot,
    policy: PolicySnapshot,
) -> Dict[str, Any]:
    """Assemble policy-enforced feedback from final scoring decisions.

    Args:
        decisions: FinalDimensionDecision objects (one per dimension).
        observations: DimensionObservation objects (for context, currently unused
                      directly — may be used for uncertainty logic in future).
        spans: EvidenceSpan objects for citation rendering.
        rubric: RubricSnapshot for dimension names and descriptor lookup.
        policy: PolicySnapshot containing explanation policy config.

    Returns:
        Feedback dict with "dimensions", "violations", and "generated_at".
    """
    # Render per-dimension explanations
    explanations = [
        render_dimension_explanation(decision, spans, rubric, policy)
        for decision in decisions
    ]

    # Enforce citation chain policy
    violations = enforce_explanation_policy(explanations, policy)

    # Assemble output
    dimensions_out: Dict[str, Any] = {}
    for exp in explanations:
        dimensions_out[exp.dimension_id] = {
            "dimension_name": exp.dimension_name,
            "canonical_score": exp.canonical_score,
            "display_score": exp.display_score,
            "descriptor_refs": list(exp.descriptor_refs),
            "evidence_span_ids": list(exp.evidence_span_ids),
            "commentary": exp.commentary,
            "uncertainty_note": exp.uncertainty_note,
            "decision_confidence": next(
                (d.decision_confidence for d in decisions if d.dimension_id == exp.dimension_id),
                0.0,
            ),
        }

    return {
        "dimensions": dimensions_out,
        "violations": [
            {
                "dimension_id": v.dimension_id,
                "violation_type": v.violation_type,
                "detail": v.detail,
            }
            for v in violations
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
