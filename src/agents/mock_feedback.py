"""
Mock Feedback Assembler — deterministic feedback dict generation.

Assembles a feedback dictionary from FinalDimensionDecisions, DimensionObservations,
and the RubricSnapshot. Dimension names are read from the rubric config (zero-
hardcoding: no trait names are embedded in this module).

Output schema (Dict[str, Any]):
  {
    "dimensions": {
      "<dimension_id>": {
        "dimension_name": str,        # from rubric config
        "final_score":    int,        # canonical_score
        "display_score":  str,        # display representation
        "confidence":     float,
        "evidence_count": int,
        "rationale":      str,
      },
      ...
    },
    "generated_at": str,              # ISO 8601 UTC timestamp
  }

No contract for feedback output yet (Phase 4 will add one). For now, Dict is
the agreed return type for the mock.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.evidence import DimensionObservation
from src.contracts.scoring import FinalDimensionDecision


def run(
    decisions: List[FinalDimensionDecision],
    observations: List[DimensionObservation],
    rubric: RubricSnapshot,
) -> Dict[str, Any]:
    """Assemble a feedback dict from final scoring decisions.

    Args:
        decisions: FinalDimensionDecision objects (one per dimension).
        observations: DimensionObservation objects for optional context.
        rubric: RubricSnapshot used to look up dimension names (from config).

    Returns:
        A plain dict with a "dimensions" map and a "generated_at" timestamp.
    """
    obs_by_dim: Dict[str, DimensionObservation] = {
        o.dimension_id: o for o in observations
    }

    dimensions_out: Dict[str, Any] = {}
    for decision in decisions:
        dim_id = decision.dimension_id
        dim_config = rubric.dimension_by_id.get(dim_id, {})
        dimension_name: str = dim_config.get("name", dim_id)

        obs = obs_by_dim.get(dim_id)
        evidence_count = len(decision.evidence_span_ids)
        if obs is not None:
            evidence_count = max(evidence_count, len(obs.supporting_span_ids))

        dimensions_out[dim_id] = {
            "dimension_name": dimension_name,
            "final_score": decision.final_score.canonical_score,
            "display_score": decision.final_score.display_score,
            "confidence": decision.decision_confidence,
            "evidence_count": evidence_count,
            "rationale": decision.decision_note or "",
        }

    return {
        "dimensions": dimensions_out,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
