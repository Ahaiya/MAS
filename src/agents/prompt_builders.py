"""
Prompt 构造器，负责把 typed contract 映射为各阶段模板可渲染的上下文。

Node prompt builders — map typed contracts to Jinja2 context dicts.

Each builder function:
1. Receives typed contract objects (plan, observation, decision, spans, rubric).
2. Extracts the Jinja2 context variables expected by the corresponding template.
3. Calls render_template() and returns the rendered string.

No rubric trait names, dimension codes, score values, or policy thresholds are
hardcoded here.  All domain values flow from the contract objects and the
RubricSnapshot that was resolved from configs/ at runtime.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.evidence import DimensionObservation, EvidenceSpan
from src.contracts.request_models import CoveragePlan, NormalizedDocument
from src.contracts.scoring import FinalDimensionDecision, ScoreHypothesis
from src.providers.prompt_loader import PromptTemplate, render_template


def _level_anchor_text(level: Dict[str, Any]) -> str:
    """Prefer full descriptor text over coarse scale labels."""
    descriptors = [
        str(item).strip()
        for item in (level.get("descriptors") or [])
        if str(item).strip()
    ]
    if descriptors:
        return "\n".join(descriptors)
    return str(level.get("summary", "")).strip()


def _dimension_anchor_entries(dim: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return only the current dimension's anchors, ordered high-to-low."""
    levels = sorted(
        dim.get("levels", []) or [],
        key=lambda item: int(item.get("rank", 0)),
        reverse=True,
    )
    anchors: List[Dict[str, Any]] = []
    for level in levels:
        anchor_text = _level_anchor_text(level)
        if not anchor_text:
            continue
        anchors.append(
            {
                "rank": int(level.get("rank", 0)),
                "text": anchor_text,
            }
        )
    return anchors


def build_extraction_prompt(
    plan: CoveragePlan,
    document: NormalizedDocument,
    rubric: RubricSnapshot,
    template: PromptTemplate,
    override_template: Optional[PromptTemplate] = None,
    evidence_focus: str = "",
    extraction_hints: str = "",
) -> str:
    """
    Build the evidence-extraction prompt for a single dimension.

    Context variables injected (matching evidence_extraction.yaml v2):
        dimension_name    : Human-readable dimension name from rubric.
        dimension_anchors : Current-dimension anchors only [{rank, text}].
        evidence_focus    : Task-level guidance on what to look for.
        chunks            : Candidate chunks [{id, title, text}].

    Args:
        plan    : CoveragePlan defining which dimension and facets to cover.
        document: NormalizedDocument containing the essay text.
        rubric  : RubricSnapshot providing dimension metadata.
        template         : Loaded default PromptTemplate.
        override_template: Optional per-dimension override PromptTemplate.
        evidence_focus   : Optional task-level evidence focus string.

    Returns:
        Rendered prompt string ready to send to a provider.
    """
    dim: Dict[str, Any] = rubric.dimension_by_id.get(plan.dimension_id, {})
    units_by_id = {u.unit_id: u for u in document.text_units}

    if plan.coverage_strategy != "full_scan" and plan.target_unit_ids:
        selected_units = [
            units_by_id[uid]
            for uid in plan.target_unit_ids
            if uid in units_by_id
        ]
    else:
        selected_units = list(document.text_units)

    selected_units = sorted(selected_units, key=lambda unit: unit.sequence_index)
    chunks = [
        {
            "id": unit.unit_id,
            "title": unit.chunk_title or "",
            "text": unit.text,
            "source_type": unit.source_type,
            "source_label": unit.source_label or "",
        }
        for unit in selected_units
    ]

    context = {
        "dimension_name": dim.get("name", plan.dimension_id),
        "dimension_anchors": _dimension_anchor_entries(dim),
        "evidence_focus": evidence_focus,
        "extraction_hints": extraction_hints,
        "chunks": chunks,
    }
    chosen_template = override_template or template
    return render_template(chosen_template, context)


def build_scoring_prompt(
    observation: DimensionObservation,
    evidence_spans: List[EvidenceSpan],
    rubric: RubricSnapshot,
    template: PromptTemplate,
    scoring_context: Optional[dict] = None,
    override_template: Optional[PromptTemplate] = None,
    prior_hypotheses: Optional[List] = None,
    evidence_focus: str = "",
) -> str:
    """
    Build the scoring prompt for a single dimension.

    Context variables injected (matching scoring.yaml v2):
        dimension_name      : Human-readable dimension name from rubric.
        dimension_anchors   : Current-dimension anchors only [{rank, text}].
        evidence_focus      : Task-level guidance on what to look for.
        evidence_spans      : Flat list [{span_id, chunk_id, quote, support_type}].
        score_anchors       : Anchor examples from scoring_context.
        calibration_notes   : Per-dimension or global calibration reminders.
        prior_rater_context : Prior rater scores for adjudication path.

    Args:
        observation   : DimensionObservation summarising extracted evidence.
        evidence_spans: Relevant EvidenceSpan objects supporting the observation.
        rubric        : RubricSnapshot for dimension/scale/level lookup.
        template      : Loaded PromptTemplate (should be scoring.yaml).
        scoring_context: Optional task-level scoring context (full file dict).
        override_template: Optional per-dimension override PromptTemplate.
        evidence_focus   : Optional task-level evidence focus string.

    Returns:
        Rendered prompt string ready to send to a provider.
    """
    dim = rubric.dimension_by_id.get(observation.dimension_id, {})
    dim_code = str(dim.get("code", ""))

    # Build flat evidence_spans list from all facet findings
    span_by_id = {span.span_id: span for span in evidence_spans}
    seen_ids: set = set()
    flat_spans = []
    for finding in observation.facet_findings:
        for span_id in list(finding.supporting_span_ids) + list(finding.counter_span_ids):
            if span_id in seen_ids:
                continue
            seen_ids.add(span_id)
            span = span_by_id.get(span_id)
            if span is None:
                continue
            flat_spans.append({
                "span_id": span_id,
                "chunk_id": span.unit_id or "",
                "quote": span.text_quote or "",
                "support_type": span.support_type or "supporting",
            })

    # calibration_notes: per-dimension lookup (from task context list), then global fallback
    raw_ctx = scoring_context if isinstance(scoring_context, dict) else {}
    calibration_notes = ""
    per_dim_list = raw_ctx.get("scoring_context") or []
    if isinstance(per_dim_list, list):
        for entry in per_dim_list:
            if isinstance(entry, dict) and str(entry.get("code", "")) == dim_code:
                calibration_notes = str(entry.get("calibration_notes", ""))
                break
    if not calibration_notes:
        calibration_notes = str(raw_ctx.get("calibration_notes") or "")

    score_anchors = list(raw_ctx.get("score_anchors") or [])

    prior_rater_context = [
        {
            "rater_id": hyp.rater_id,
            "score": hyp.score.canonical_score,
            "justification": hyp.rationale or "",
        }
        for hyp in (prior_hypotheses or [])
    ]

    context = {
        "dimension_name": dim.get("name", observation.dimension_id),
        "dimension_anchors": _dimension_anchor_entries(dim),
        "evidence_focus": evidence_focus,
        "evidence_spans": flat_spans,
        "score_anchors": score_anchors,
        "calibration_notes": calibration_notes,
        "prior_rater_context": prior_rater_context,
    }

    chosen_template = override_template or template
    return render_template(chosen_template, context)


def build_explanation_prompt(
    decision: FinalDimensionDecision,
    evidence_spans: List[EvidenceSpan],
    rubric: RubricSnapshot,
    template: PromptTemplate,
    override_template: Optional[PromptTemplate] = None,
    hypotheses: Optional[List[ScoreHypothesis]] = None,
    evidence_focus: str = "",
    audience: str = "evaluator",
    feedback_hints: str = "",
) -> str:
    """
    Build the explanation/feedback prompt for a finalised dimension decision.

    Context variables injected (matching explanation.yaml v2):
        dimension_name   : Human-readable dimension name from rubric.
        final_score      : Integer canonical score from the final decision.
        was_adjudicated  : Whether decision was adjudicated.
        justification_1  : Adjudicator rationale (adjudicated) or rater_1 rationale.
        justification_2  : Rater_2 rationale (non-adjudicated path only).
        evidence_spans   : Flat list [{span_id, quote, support_type}].
        evidence_focus   : Task-level guidance on what to look for.
        audience         : "student" for learner-facing, "evaluator" for professional.

    Args:
        decision        : FinalDimensionDecision containing score and evidence refs.
        evidence_spans  : EvidenceSpan objects available to this decision.
        rubric          : RubricSnapshot for dimension metadata.
        template        : Loaded global explanation PromptTemplate.
        override_template: Optional per-dimension override PromptTemplate.
        hypotheses      : ScoreHypothesis list for extracting rater justifications.
        evidence_focus  : Optional task-level evidence focus string.
        audience        : Feedback audience ("student" or "evaluator").

    Returns:
        Rendered prompt string ready to send to a provider.
    """
    dim = rubric.dimension_by_id.get(decision.dimension_id, {})
    was_adjudicated = decision.adjudication_id is not None

    # Extract rater justifications from hypotheses
    dim_hyps = [h for h in (hypotheses or []) if h.dimension_id == decision.dimension_id]
    hyps_by_rater = {h.rater_id: h for h in dim_hyps}

    if was_adjudicated:
        adj_hyp = hyps_by_rater.get("rater_3")
        justification_1 = (adj_hyp.rationale or "") if adj_hyp else ""
        justification_2 = ""
    else:
        r1_hyp = hyps_by_rater.get("rater_1")
        r2_hyp = hyps_by_rater.get("rater_2")
        justification_1 = (r1_hyp.rationale or "") if r1_hyp else ""
        justification_2 = (r2_hyp.rationale or "") if r2_hyp else ""

    # Build flat evidence_spans from decision.evidence_span_ids
    span_by_id = {span.span_id: span for span in evidence_spans}
    flat_spans = []
    for span_id in decision.evidence_span_ids:
        span = span_by_id.get(span_id)
        if span is None or not (span.text_quote or "").strip():
            continue
        flat_spans.append({
            "span_id": span_id,
            "quote": span.text_quote or "",
            "support_type": span.support_type or "supporting",
        })

    context = {
        "dimension_name": dim.get("name", decision.dimension_id),
        "final_score": decision.final_score.canonical_score,
        "was_adjudicated": was_adjudicated,
        "justification_1": justification_1,
        "justification_2": justification_2,
        "evidence_spans": flat_spans,
        "evidence_focus": evidence_focus,
        "audience": audience,
        "feedback_hints": feedback_hints,
    }

    chosen_template = override_template or template
    return render_template(chosen_template, context)
