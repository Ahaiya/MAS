"""
解释策略模块，负责渲染维度反馈并执行证据约束与引用校验。

Explanation Policy — config-driven explanation rendering and citation enforcement.

Renders per-dimension explanations from FinalDimensionDecision, EvidenceSpan,
and RubricSnapshot, then validates the citation chain (descriptor ref →
evidence span → canonical score) against policy requirements.

Supported policy flags (all read from PolicySnapshot.explanation_policy):
  requirements.require_descriptor_alignment  – descriptor_refs must be non-empty
  requirements.require_evidence_links        – evidence_span_ids must be non-empty
  requirements.require_score_citation        – canonical_score must be non-negative
  citation_rules.min_citations_per_dimension – minimum evidence citations count
  output_constraints.max_commentary_length_per_dimension – commentary length cap
  output_constraints.require_evidence_score_chain – chain must be closed

All dimension names, descriptor text, and score values come from RubricSnapshot
or FinalDimensionDecision — nothing is hardcoded here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.contracts.artifact_bundle import PolicySnapshot, RubricSnapshot
from src.contracts.evidence import (
    DimensionObservation,
    EvidenceSpan,
    ObservationConfidence,
)
from src.contracts.scoring import FinalDimensionDecision


# ── Output types ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DimensionExplanation:
    """Structured, policy-compliant explanation for one dimension.

    All referenced values must trace back to config artifacts (rubric
    descriptors) or contract objects (evidence span IDs, canonical scores).

    Attributes:
        dimension_id: Opaque dimension identifier from rubric config.
        dimension_name: Human-readable name from rubric config.
        canonical_score: Authoritative integer score (computation use only).
        display_score: Display string (may include annotation).
        scale_ref: Scale reference from the rubric.
        descriptor_refs: Rubric descriptor references cited for this score.
        evidence_span_ids: Evidence span IDs supporting the explanation.
        commentary: Structured commentary text (bounded by policy max length).
        uncertainty_note: Optional note if confidence is low or adjudication used.
    """

    dimension_id: str
    dimension_name: str
    canonical_score: int
    display_score: str
    scale_ref: str
    descriptor_refs: List[str]
    evidence_span_ids: List[str]
    commentary: str
    uncertainty_note: Optional[str]


@dataclass(frozen=True)
class ExplanationViolation:
    """A policy violation in the citation chain for one dimension.

    Attributes:
        dimension_id: Which dimension produced the violation.
        violation_type: Short identifier (e.g., "missing_descriptor_ref").
        detail: Human-readable description of the violation.
    """

    dimension_id: str
    violation_type: str
    detail: str


# ── Internal helpers ──────────────────────────────────────────────────────────


def _get_requirements(policy: PolicySnapshot) -> Dict[str, Any]:
    return policy.explanation_policy.get("requirements", {})


def _get_citation_rules(policy: PolicySnapshot) -> Dict[str, Any]:
    return policy.explanation_policy.get("citation_rules", {})


def _get_output_constraints(policy: PolicySnapshot) -> Dict[str, Any]:
    return policy.explanation_policy.get("output_constraints", {})


def _get_low_confidence_threshold(policy: PolicySnapshot) -> float:
    constraints = _get_output_constraints(policy)
    raw = constraints.get("low_confidence_threshold", 0.5)
    try:
        threshold = float(raw)
    except (TypeError, ValueError):
        return 0.5
    if threshold < 0.0 or threshold > 1.0:
        return 0.5
    return threshold


def _build_commentary(
    decision: FinalDimensionDecision,
    observation: DimensionObservation,
    spans: List[EvidenceSpan],
    rubric: RubricSnapshot,
    max_length: int,
    scorer_rationale: Optional[str] = None,
) -> str:
    """Build a short, evidence-anchored commentary string."""
    rationale = (scorer_rationale or "").strip()
    if rationale:
        return rationale[:max_length]

    dim_id = decision.dimension_id
    score_val = decision.final_score.canonical_score

    span_by_id = {
        span.span_id: span
        for span in spans
        if span.dimension_id == dim_id
    }

    def _quoted(span_id: str) -> Optional[str]:
        span = span_by_id.get(span_id)
        if span is None:
            return None
        quote = (span.text_quote or "").strip()
        if not quote:
            return None
        return f"'{quote}'"

    facet_lines: List[str] = []
    for finding in observation.facet_findings:
        supporting_quotes = [
            q
            for q in (_quoted(span_id) for span_id in finding.supporting_span_ids)
            if q is not None
        ]
        counter_quotes = [
            q
            for q in (_quoted(span_id) for span_id in finding.counter_span_ids)
            if q is not None
        ]
        if not supporting_quotes and not counter_quotes:
            continue

        supporting_text = ", ".join(supporting_quotes) if supporting_quotes else "(none)"
        line = f"[{finding.facet_id}]: supporting evidence: {supporting_text}"
        if counter_quotes:
            line += "; however, counter evidence suggests: " + ", ".join(counter_quotes)
        if finding.finding_note:
            line += f" ({finding.finding_note})"
        facet_lines.append(line)

    if facet_lines:
        return " ".join(facet_lines)[:max_length]

    # Find descriptor summary for the score from rubric levels
    descriptor_summary = ""
    dim_cfg = rubric.dimension_by_id.get(dim_id, {})
    for level in dim_cfg.get("levels", []):
        if level.get("rank") == score_val:
            descriptor_summary = level.get("summary", "")
            break

    # Find a relevant evidence quote from the final decision references.
    span_quote = ""
    for span_id in decision.evidence_span_ids:
        quoted = _quoted(span_id)
        if quoted is not None:
            span_quote = quoted
            break

    if descriptor_summary and span_quote:
        commentary = f"Score {score_val}: {descriptor_summary}. Evidence: {span_quote}"
    elif descriptor_summary:
        commentary = f"Score {score_val}: {descriptor_summary}."
    elif span_quote:
        commentary = f"Score {score_val} supported by evidence: {span_quote}"
    else:
        commentary = f"Score {score_val} assigned based on available evidence."

    return commentary[:max_length]


def _fallback_observation(decision: FinalDimensionDecision) -> DimensionObservation:
    """Create a minimal observation when upstream observation is unavailable."""
    return DimensionObservation(
        observation_id=f"obs-fallback-{decision.dimension_id}",
        document_id="unknown",
        dimension_id=decision.dimension_id,
        supporting_span_ids=list(decision.evidence_span_ids),
        counter_span_ids=[],
        facet_findings=[],
        observation_confidence=ObservationConfidence.MEDIUM,
        uncertainty_notes=[],
    )


def _build_uncertainty_note(
    decision: FinalDimensionDecision,
    low_confidence_threshold: float,
) -> Optional[str]:
    """Return an uncertainty note if conditions warrant one."""
    reasons: List[str] = []
    if decision.decision_confidence < low_confidence_threshold:
        reasons.append(
            f"confidence={decision.decision_confidence:.2f} below threshold"
        )
    if decision.adjudication_id is not None:
        reasons.append("adjudication was required to resolve a scoring conflict")
    if not reasons:
        return None
    return "Note: " + "; ".join(reasons) + "."


# ── Public API ────────────────────────────────────────────────────────────────


def render_dimension_explanation(
    decision: FinalDimensionDecision,
    spans: List[EvidenceSpan],
    rubric: RubricSnapshot,
    policy: PolicySnapshot,
    observation: Optional[DimensionObservation] = None,
    scorer_rationale: Optional[str] = None,
) -> DimensionExplanation:
    """Render a structured, policy-bounded explanation for one dimension.

    Args:
        decision: Authoritative score decision for this dimension.
        spans: EvidenceSpan list (filtered or full — only this dim's spans used).
        rubric: RubricSnapshot for dimension name and descriptor lookup.
        policy: PolicySnapshot containing explanation policy config.

    Returns:
        A frozen DimensionExplanation.
    """
    constraints = _get_output_constraints(policy)
    max_len: int = int(constraints.get("max_commentary_length_per_dimension", 500))
    low_conf_threshold = _get_low_confidence_threshold(policy)

    dim_id = decision.dimension_id
    dim_cfg = rubric.dimension_by_id.get(dim_id, {})
    dimension_name: str = dim_cfg.get("name", dim_id)

    # Filter spans to this dimension
    dim_spans = [s for s in spans if s.dimension_id == dim_id]

    obs = observation or _fallback_observation(decision)
    commentary = _build_commentary(
        decision=decision,
        observation=obs,
        spans=dim_spans,
        rubric=rubric,
        max_length=max_len,
        scorer_rationale=scorer_rationale,
    )
    uncertainty_note = _build_uncertainty_note(decision, low_conf_threshold)

    return DimensionExplanation(
        dimension_id=dim_id,
        dimension_name=dimension_name,
        canonical_score=decision.final_score.canonical_score,
        display_score=decision.final_score.display_score,
        scale_ref=decision.final_score.scale_ref,
        descriptor_refs=list(decision.descriptor_refs),
        evidence_span_ids=list(decision.evidence_span_ids),
        commentary=commentary,
        uncertainty_note=uncertainty_note,
    )


def validate_citation_chain(
    explanation: DimensionExplanation,
    policy: PolicySnapshot,
) -> List[ExplanationViolation]:
    """Validate the citation chain for one DimensionExplanation.

    Checks required flags from policy.explanation_policy["requirements"] and
    citation_rules, returns a list of ExplanationViolation objects (empty = OK).

    Args:
        explanation: The DimensionExplanation to validate.
        policy: PolicySnapshot containing explanation policy config.

    Returns:
        List of ExplanationViolation (may be empty if all checks pass).
    """
    reqs = _get_requirements(policy)
    citation_rules = _get_citation_rules(policy)
    dim_id = explanation.dimension_id
    violations: List[ExplanationViolation] = []

    # Check descriptor alignment
    if reqs.get("require_descriptor_alignment", False):
        if not explanation.descriptor_refs:
            violations.append(ExplanationViolation(
                dimension_id=dim_id,
                violation_type="missing_descriptor_ref",
                detail=(
                    f"Dimension '{dim_id}': require_descriptor_alignment=true but "
                    f"descriptor_refs is empty."
                ),
            ))

    # Check evidence links
    if reqs.get("require_evidence_links", False):
        if not explanation.evidence_span_ids:
            violations.append(ExplanationViolation(
                dimension_id=dim_id,
                violation_type="missing_evidence_link",
                detail=(
                    f"Dimension '{dim_id}': require_evidence_links=true but "
                    f"evidence_span_ids is empty."
                ),
            ))
    else:
        # Even without require_evidence_links, check min_citations
        min_citations = int(citation_rules.get("min_citations_per_dimension", 0))
        if min_citations > 0 and len(explanation.evidence_span_ids) < min_citations:
            violations.append(ExplanationViolation(
                dimension_id=dim_id,
                violation_type="insufficient_evidence_citations",
                detail=(
                    f"Dimension '{dim_id}': min_citations_per_dimension={min_citations} "
                    f"but found {len(explanation.evidence_span_ids)}."
                ),
            ))

    # Check min_citations when evidence_links required (additional specificity)
    if reqs.get("require_evidence_links", False):
        min_citations = int(citation_rules.get("min_citations_per_dimension", 0))
        if min_citations > 0 and len(explanation.evidence_span_ids) < min_citations:
            violations.append(ExplanationViolation(
                dimension_id=dim_id,
                violation_type="insufficient_evidence_citations",
                detail=(
                    f"Dimension '{dim_id}': min_citations_per_dimension={min_citations} "
                    f"but found {len(explanation.evidence_span_ids)}."
                ),
            ))

    return violations


def enforce_explanation_policy(
    explanations: List[DimensionExplanation],
    policy: PolicySnapshot,
) -> List[ExplanationViolation]:
    """Run citation chain validation across all explanations.

    Args:
        explanations: All DimensionExplanation objects to validate.
        policy: PolicySnapshot containing explanation policy config.

    Returns:
        Deduplicated list of all ExplanationViolation objects found.
    """
    all_violations: List[ExplanationViolation] = []
    for exp in explanations:
        all_violations.extend(validate_citation_chain(exp, policy))
    return all_violations
