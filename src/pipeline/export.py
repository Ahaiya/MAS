"""
流水线导出模块，负责把最终决策整理成稳定的结构化输出。

Pipeline Export — structured output assembly.

Explicitly separates trait-level dimension outputs from the optional
composite output. Consumers (e.g., feedback assembler, evaluation harness)
should reference this module to access the canonical output format.

Design:
- build_pipeline_output() takes FinalDimensionDecision[] and an optional
  CompositeDecision, and returns a plain dict with two top-level keys:
    * "trait_scores" — list of per-dimension score entries
    * "composite"    — CompositeDecision serialized dict, or None
- No business logic or aggregation formula lives here; this module only
  assembles the output structure from already-computed results.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.contracts.scoring import CompositeDecision, FinalDimensionDecision


def build_pipeline_output(
    decisions: List[FinalDimensionDecision],
    composite: Optional[CompositeDecision],
) -> Dict[str, Any]:
    """Assemble the final pipeline output dict.

    Args:
        decisions: Authoritative dimension-level decisions.
        composite: Optional aggregated composite score.

    Returns:
        Dict with:
            "trait_scores": list of per-dimension score dicts
            "composite": serialized CompositeDecision or None
    """
    trait_scores = [
        {
            "dimension_id": d.dimension_id,
            "decision_id": d.decision_id,
            "canonical_score": d.final_score.canonical_score,
            "display_score": d.final_score.display_score,
            "scale_ref": d.final_score.scale_ref,
            "adjudication_id": d.adjudication_id,
            "decision_confidence": d.decision_confidence,
            "descriptor_refs": list(d.descriptor_refs),
            "evidence_span_ids": list(d.evidence_span_ids),
        }
        for d in decisions
    ]

    return {
        "trait_scores": trait_scores,
        "composite": composite.to_dict() if composite is not None else None,
    }
