"""
Real Feedback Agent — calls a real LLM provider to generate evidence-grounded feedback.

Builds the explanation prompt via prompt_builders, calls the provider,
assembles the feedback dict in the same shape as mock_feedback.

Contract boundary: all domain values (dimension names, descriptor refs,
evidence IDs) flow from the FinalDimensionDecision and RubricSnapshot;
nothing is hardcoded here.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.agents.prompt_builders import build_explanation_prompt
from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.evidence import DimensionObservation, EvidenceSpan
from src.contracts.scoring import FinalDimensionDecision
from src.providers.base import BaseProvider, LLMRequest
from src.providers.prompt_loader import PromptTemplate


def run(
    decisions: List[FinalDimensionDecision],
    observations: List[DimensionObservation],
    rubric: RubricSnapshot,
    evidence_spans: List[EvidenceSpan],
    provider: BaseProvider,
    template: PromptTemplate,
) -> Dict[str, Any]:
    """
    Generate evidence-grounded feedback for all finalised dimension decisions.

    Calls the LLM once per dimension. Returns a feedback dict in the same
    shape as mock_feedback.run() so the pipeline runner can treat both
    interchangeably.

    Args:
        decisions     : Final scoring decisions (one per dimension).
        observations  : DimensionObservations (used for context).
        rubric        : RubricSnapshot for dimension metadata.
        evidence_spans: All EvidenceSpan objects from the run.
        provider      : Configured BaseProvider to call.
        template      : Loaded explanation prompt template.

    Returns:
        Feedback dict with keys: "dimensions", "summary", "provider".
    """
    span_by_id = {s.span_id: s for s in evidence_spans}
    obs_by_dim = {o.dimension_id: o for o in observations}

    dimensions: Dict[str, Any] = {}
    for decision in decisions:
        decision_spans = [
            span_by_id[sid]
            for sid in decision.evidence_span_ids
            if sid in span_by_id
        ]
        prompt_text = build_explanation_prompt(decision, decision_spans, rubric, template)
        request = LLMRequest(prompt=prompt_text)
        response = provider.complete(request)

        dim_id = decision.dimension_id
        dim_meta = rubric.dimension_by_id.get(dim_id, {})
        dimensions[dim_id] = {
            "dimension_name": dim_meta.get("name", dim_id),
            "canonical_score": decision.final_score.canonical_score,
            "display_score": decision.final_score.display_score,
            "display_annotation": decision.final_score.display_annotation,
            "descriptor_refs": list(decision.descriptor_refs),
            "evidence_span_ids": list(decision.evidence_span_ids),
            "feedback_text": response.content,
            "confidence": decision.decision_confidence,
        }

    return {
        "dimensions": dimensions,
        "summary": f"Real provider feedback for {len(decisions)} dimension(s).",
        "provider": provider.name,
    }
