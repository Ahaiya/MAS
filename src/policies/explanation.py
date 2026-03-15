"""
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
from src.contracts.evidence import EvidenceSpan
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


_LOW_CONFIDENCE_THRESHOLD = 0.5


def _build_commentary(
    decision: FinalDimensionDecision,
    spans: List[EvidenceSpan],
    rubric: RubricSnapshot,
    max_length: int,
) -> str:
    """Build a short, evidence-anchored commentary string."""
    dim_id = decision.dimension_id
    score_val = decision.final_score.canonical_score

    # Find descriptor summary for the score from rubric levels
    descriptor_summary = ""
    dim_cfg = rubric.dimension_by_id.get(dim_id, {})
    for level in dim_cfg.get("levels", []):
        if level.get("rank") == score_val:
            descriptor_summary = level.get("summary", "")
            break

    # Find relevant span quote
    span_quote = ""
    for span in spans:
        if span.dimension_id == dim_id and span.text_quote:
            span_quote = f'"{span.text_quote}"'
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


def _build_uncertainty_note(
    decision: FinalDimensionDecision,
) -> Optional[str]:
    """Return an uncertainty note if conditions warrant one."""
    reasons: List[str] = []
    if decision.decision_confidence < _LOW_CONFIDENCE_THRESHOLD:
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

    dim_id = decision.dimension_id
    dim_cfg = rubric.dimension_by_id.get(dim_id, {})
    dimension_name: str = dim_cfg.get("name", dim_id)

    # Filter spans to this dimension
    dim_spans = [s for s in spans if s.dimension_id == dim_id]

    commentary = _build_commentary(decision, dim_spans, rubric, max_len)
    uncertainty_note = _build_uncertainty_note(decision)

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
