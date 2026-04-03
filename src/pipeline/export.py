"""
流水线导出模块，负责把最终决策整理成稳定的结构化输出。

Pipeline Export — structured output assembly.

Explicitly separates trait-level dimension outputs from the optional
aggregate indicator output. Consumers (e.g., feedback assembler, evaluation
harness) should reference this module to access the canonical output format.

Design:
- build_pipeline_output() takes FinalDimensionDecision[] and an optional
  CompositeDecision, and returns a plain dict with three top-level keys:
    * "trait_scores"    — list of per-dimension score entries
    * "indicator_score" — aggregate indicator score payload, or None
    * "composite"       — compatibility alias of indicator_score, or None
- No business logic or aggregation formula lives here; this module only
  assembles the output structure from already-computed results.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.contracts.scoring import CompositeDecision, FinalDimensionDecision


def build_indicator_score_payload(
    composite: Optional[CompositeDecision],
    *,
    bundle_metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Serialize the aggregate score using indicator-centric naming.

    `composite` remains the internal contract type, but for engineering
    evaluation the user-facing meaning is "selected indicator score"
    (for example, the A4 aggregate score across its observation points).
    """
    if composite is None:
        return None

    payload = composite.to_dict()
    metadata = bundle_metadata if isinstance(bundle_metadata, dict) else {}
    raw_indicator_ids = metadata.get("selected_indicator_ids")
    indicator_ids = [
        str(item).strip()
        for item in (raw_indicator_ids if isinstance(raw_indicator_ids, list) else [])
        if str(item).strip()
    ]
    active_task_id = metadata.get("active_task_id")
    if len(indicator_ids) == 1:
        display_label = f"{indicator_ids[0]} 聚合分"
    else:
        display_label = "聚合指标得分"

    payload.update(
        {
            "score_kind": "indicator_score",
            "score_scope": "selected_indicator",
            "indicator_ids": indicator_ids,
            "display_label": display_label,
        }
    )
    if len(indicator_ids) == 1:
        payload["indicator_id"] = indicator_ids[0]
    if isinstance(active_task_id, str) and active_task_id.strip():
        payload["task_id"] = active_task_id.strip()
    return payload


def build_pipeline_output(
    decisions: List[FinalDimensionDecision],
    composite: Optional[CompositeDecision],
    *,
    bundle_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the final pipeline output dict.

    Args:
        decisions: Authoritative dimension-level decisions.
        composite: Optional aggregated composite score.

    Returns:
        Dict with:
            "trait_scores": list of per-dimension score dicts
            "indicator_score": indicator-centric aggregate payload or None
            "composite": compatibility alias for legacy readers
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
    indicator_score = build_indicator_score_payload(
        composite,
        bundle_metadata=bundle_metadata,
    )

    return {
        "trait_scores": trait_scores,
        "indicator_score": indicator_score,
        "composite": indicator_score,
    }
