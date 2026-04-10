"""
反馈组装 Agent，负责把最终裁决结果整理成稳定的用户反馈结构。

Feedback Assembler — unified feedback output for real LLM runs.

This is the canonical feedback entrypoint for the pipeline. It always returns
the same top-level structure and the same per-dimension fields.

Canonical output schema (Dict[str, Any]):
  {
    "dimensions": {
      "<dimension_id>": {
        "dimension_name": str,
        "score": int,
        "descriptor_refs": List[str],
        "evidence": List[Dict[str, str]],
        "feedback": str,
        "audit": {
          "uncertainty_note": str | None,
          "decision_confidence": float,
          "was_adjudicated": bool,
          "scoring_records": List[Dict[str, Any]],
        },
      },
      ...
    },
    "generated_at": str,
  }
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.agents.prompt_builders import build_explanation_prompt
from src.contracts.artifact_bundle import PolicySnapshot, RubricSnapshot
from src.contracts.evidence import (
    DimensionObservation,
    EvidenceSpan,
    ObservationConfidence,
)
from src.contracts.scoring import (
    FinalDimensionDecision,
    ScoreHypothesis,
)
from src.policies.explanation import (
    render_dimension_explanation,
)
from src.providers.base import BaseProvider, LLMRequest
from src.providers.prompt_loader import PromptTemplate


def _max_commentary_length(policy: PolicySnapshot) -> int:
    constraints = policy.explanation_policy.get("output_constraints", {})
    return int(constraints.get("max_commentary_length_per_dimension", 500))


def _render_commentary(
    decision: FinalDimensionDecision,
    observation: DimensionObservation,
    decision_spans: List[EvidenceSpan],
    rubric: RubricSnapshot,
    provider: BaseProvider,
    template: PromptTemplate,
    fallback_text: str,
    max_length: int,
    hypotheses: Optional[List[ScoreHypothesis]] = None,
    override_template: Optional[PromptTemplate] = None,
    evidence_focus: str = "",
    audience: str = "evaluator",
) -> str:
    """Render commentary through a configured real provider."""

    template_used = override_template or template
    prompt_text = build_explanation_prompt(
        decision=decision,
        evidence_spans=decision_spans,
        rubric=rubric,
        template=template,
        override_template=override_template,
        hypotheses=hypotheses,
        evidence_focus=evidence_focus,
        audience=audience,
    )
    response = provider.complete(
        LLMRequest(
            prompt=prompt_text,
            metadata={
                "node_id": "node_feedback",
                "stage_name": "feedback",
                "dimension_id": decision.dimension_id,
                "decision_id": decision.decision_id,
                "template_source": template_used.source_path,
                "template_version": template_used.metadata.get("template_version"),
                "used_override_template": override_template is not None,
                "evidence_span_count": len(decision_spans),
            },
        )
    )
    raw = response.content.strip()
    if not raw:
        return fallback_text
    # 剥掉 Markdown 代码块（```json ... ``` 或 ``` ... ```）
    if raw.startswith("```"):
        lines = raw.split("\n")
        inner = lines[1:]
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        raw = "\n".join(inner).strip()
    # 尝试解析 JSON {"feedback": "..."} 格式
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            fb = str(parsed.get("feedback", "")).strip()
            if fb:
                return fb[:max_length]
    except (json.JSONDecodeError, ValueError):
        pass
    return raw[:max_length]


def _fallback_observation(decision: FinalDimensionDecision) -> DimensionObservation:
    """Build a minimal observation when no observation is available for a dimension."""
    return DimensionObservation(
        observation_id=f"obs-feedback-fallback-{decision.dimension_id}",
        document_id="unknown",
        dimension_id=decision.dimension_id,
        supporting_span_ids=list(decision.evidence_span_ids),
        counter_span_ids=[],
        facet_findings=[],
        observation_confidence=ObservationConfidence.MEDIUM,
        uncertainty_notes=[],
    )


def _find_primary_hypothesis(
    decision: FinalDimensionDecision,
    hyp_by_id: Dict[str, ScoreHypothesis],
    hyps_by_dim: Dict[str, List[ScoreHypothesis]],
) -> Optional[ScoreHypothesis]:
    """Find the scorer hypothesis that best matches a final decision."""
    primary = hyp_by_id.get(decision.primary_hypothesis_id)
    if primary is not None:
        return primary
    dim_hyps = hyps_by_dim.get(decision.dimension_id, [])
    if not dim_hyps:
        return None
    return sorted(dim_hyps, key=lambda h: (h.rater_id, h.hypothesis_id))[0]


def _serialize_scoring_records(
    span_by_id: Dict[str, EvidenceSpan],
    dim_hyps: List[ScoreHypothesis],
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for hyp in sorted(dim_hyps, key=lambda h: (h.rater_id, h.hypothesis_id)):
        evidence = []
        for span_id in hyp.evidence_span_ids:
            span = span_by_id.get(span_id)
            evidence.append(
                {
                    "span_id": span_id,
                    "quote": ((span.text_quote or "").strip() if span is not None else ""),
                }
            )
        records.append(
            {
                "rater_id": hyp.rater_id,
                "score": hyp.score.canonical_score,
                "confidence": hyp.confidence,
                "descriptor_refs": list(hyp.descriptor_refs),
                "evidence": evidence,
                "rationale": hyp.rationale,
            }
        )
    return records


def run(
    decisions: List[FinalDimensionDecision],
    observations: List[DimensionObservation],
    spans: List[EvidenceSpan],
    hypotheses: Optional[List[ScoreHypothesis]] = None,
    rubric: Optional[RubricSnapshot] = None,
    policy: Optional[PolicySnapshot] = None,
    provider: BaseProvider | None = None,
    template: PromptTemplate | None = None,
    override_templates: Optional[Dict[str, PromptTemplate]] = None,
    evidence_focus: str = "",
    audience: str = "evaluator",
) -> Dict[str, Any]:
    """Assemble a unified feedback dict from final scoring decisions.

    Args:
        decisions: FinalDimensionDecision objects (one per dimension).
        observations: DimensionObservation objects for optional evidence counts.
        spans: EvidenceSpan objects for commentary and citation rendering.
        hypotheses: Optional ScoreHypothesis objects for scorer rationale lookup.
        rubric: RubricSnapshot for dimension metadata.
        policy: PolicySnapshot containing explanation policy config.
        provider: Real provider used to generate commentary text.
        template: Explanation prompt template paired with provider.
        override_templates: Optional per-dimension explanation override templates.
        evidence_focus: Optional task-level evidence focus string.
        audience: Feedback audience ("student" or "evaluator").

    Returns:
        Unified feedback dict with stable keys across deterministic and real runs.
    """
    # Backward compatibility:
    # legacy calls use run(decisions, observations, spans, rubric, policy, ...)
    if (
        isinstance(hypotheses, RubricSnapshot)
        and isinstance(rubric, PolicySnapshot)
        and policy is None
    ):
        resolved_hypotheses: List[ScoreHypothesis] = []
        resolved_rubric = hypotheses
        resolved_policy = rubric
    else:
        if rubric is None or policy is None:
            raise ValueError("feedback.run() requires rubric and policy")
        resolved_hypotheses = list(hypotheses or [])
        resolved_rubric = rubric
        resolved_policy = policy

    if provider is None or template is None:
        raise ValueError("feedback.run() requires real provider and explanation template")

    span_by_id: Dict[str, EvidenceSpan] = {s.span_id: s for s in spans}
    obs_by_dim: Dict[str, DimensionObservation] = {
        obs.dimension_id: obs for obs in observations
    }
    hyp_by_id: Dict[str, ScoreHypothesis] = {
        hyp.hypothesis_id: hyp for hyp in resolved_hypotheses
    }
    hyps_by_dim: Dict[str, List[ScoreHypothesis]] = {}
    for hyp in resolved_hypotheses:
        hyps_by_dim.setdefault(hyp.dimension_id, []).append(hyp)

    max_length = _max_commentary_length(resolved_policy)
    overrides = override_templates or {}

    explanations = []
    dimensions_out: Dict[str, Any] = {}

    for decision in decisions:
        dim_id = decision.dimension_id
        obs = obs_by_dim.get(dim_id) or _fallback_observation(decision)
        selected_hyp = _find_primary_hypothesis(decision, hyp_by_id, hyps_by_dim)
        scorer_rationale = (
            (selected_hyp.rationale or "").strip() if selected_hyp is not None else ""
        )
        dim_hyps = hyps_by_dim.get(dim_id, [])
        override_template = overrides.get(dim_id)

        decision_spans = [
            span_by_id[sid]
            for sid in decision.evidence_span_ids
            if sid in span_by_id
        ]

        explanation = render_dimension_explanation(
            decision=decision,
            spans=decision_spans,
            rubric=resolved_rubric,
            policy=resolved_policy,
            observation=obs,
            scorer_rationale=scorer_rationale or None,
        )
        feedback_text = _render_commentary(
            decision=decision,
            observation=obs,
            decision_spans=decision_spans,
            rubric=resolved_rubric,
            provider=provider,
            template=template,
            fallback_text=explanation.commentary,
            max_length=max_length,
            hypotheses=resolved_hypotheses,
            override_template=override_template,
            evidence_focus=evidence_focus,
            audience=audience,
        )

        explanations.append(explanation)

        rendered_evidence = []
        for span_id in explanation.evidence_span_ids:
            span = span_by_id.get(span_id)
            rendered_evidence.append(
                {
                    "span_id": span_id,
                    "quote": ((span.text_quote or "").strip() if span is not None else ""),
                }
            )

        dimensions_out[dim_id] = {
            "dimension_name": explanation.dimension_name,
            "score": explanation.canonical_score,
            "descriptor_refs": list(explanation.descriptor_refs),
            "evidence": rendered_evidence,
            "feedback": feedback_text,
            "audit": {
                "uncertainty_note": explanation.uncertainty_note,
                "decision_confidence": decision.decision_confidence,
                "was_adjudicated": decision.adjudication_id is not None,
                "scoring_records": _serialize_scoring_records(
                    span_by_id, dim_hyps
                ),
            },
        }

    return {
        "dimensions": dimensions_out,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
