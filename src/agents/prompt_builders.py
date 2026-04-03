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
from src.contracts.scoring import FinalDimensionDecision
from src.providers.prompt_loader import PromptTemplate, render_template


def build_extraction_prompt(
    plan: CoveragePlan,
    document: NormalizedDocument,
    rubric: RubricSnapshot,
    template: PromptTemplate,
    override_template: Optional[PromptTemplate] = None,
) -> str:
    """
    Build the evidence-extraction prompt for a single dimension.

    Context variables injected (matching evidence_extraction.yaml v2):
        dimension_name         : Human-readable dimension name from rubric.
        dimension_code         : Short code from rubric (e.g. "I").
        chunks                 : Candidate chunks [{id, title, text}].
        levels                 : Full level descriptors [{rank, summary, descriptors}].
        facet_descriptions     : Required facets [{facet_id, description}].
        minimum_evidence_units : Minimum evidence requirement from the plan.

    Args:
        plan    : CoveragePlan defining which dimension and facets to cover.
        document: NormalizedDocument containing the essay text.
        rubric  : RubricSnapshot providing dimension metadata.
        template         : Loaded default PromptTemplate.
        override_template: Optional per-dimension override PromptTemplate.

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

    levels = []
    for level in dim.get("levels", []) or []:
        levels.append(
            {
                "rank": level.get("rank"),
                "summary": level.get("summary", ""),
                "descriptors": list(level.get("descriptors") or []),
            }
        )

    # Current rubric usually has facet IDs only. This keeps compatibility with
    # future richer facet description structures without hardcoding facet names.
    facet_desc_map: Dict[str, str] = {}
    observation_schema = dim.get("observation_schema", {}) or {}
    raw_facet_desc = observation_schema.get("facet_descriptions")
    if isinstance(raw_facet_desc, dict):
        facet_desc_map = {str(k): str(v) for k, v in raw_facet_desc.items()}
    elif isinstance(raw_facet_desc, list):
        for item in raw_facet_desc:
            if not isinstance(item, dict):
                continue
            facet_id = item.get("facet_id")
            if facet_id is None:
                continue
            facet_desc_map[str(facet_id)] = str(item.get("description", ""))

    facet_descriptions = [
        {
            "facet_id": facet_id,
            "description": facet_desc_map.get(facet_id, ""),
        }
        for facet_id in plan.required_facets
    ]

    context = {
        "dimension_name": dim.get("name", plan.dimension_id),
        "dimension_code": dim.get("code", ""),
        "chunks": chunks,
        "levels": levels,
        "facet_descriptions": facet_descriptions,
        "minimum_evidence_units": plan.minimum_evidence_units,
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
) -> str:
    """
    Build the scoring prompt for a single dimension.

    Context variables injected (matching scoring.yaml v3):
        role_description       : Dataset/global scorer role description.
        dimension_name         : Human-readable dimension name from rubric.
        dimension_code         : Short code from rubric.
        levels                 : List of level dicts [{rank, summary, descriptors}].
        facet_evidence         : Per-facet evidence buckets with supporting/counter quotes.
        observation_confidence : HIGH/MEDIUM/LOW.
        uncertainty_notes      : Observation uncertainty notes.
        score_anchors          : Dimension-filtered anchor examples from scoring_context.
        dataset_notes          : Dataset-level scoring notes.
        calibration_notes      : Calibration reminders.
        dimension_override_notes: Rendered per-dimension override text.

    Args:
        observation   : DimensionObservation summarising extracted evidence.
        evidence_spans: Relevant EvidenceSpan objects supporting the observation.
        rubric        : RubricSnapshot for dimension/scale/level lookup.
        template      : Loaded PromptTemplate (should be scoring.yaml).
        scoring_context: Optional dataset-level scoring context configuration.
        override_template: Optional per-dimension override PromptTemplate.

    Returns:
        Rendered prompt string ready to send to a provider.
    """
    dim = rubric.dimension_by_id.get(observation.dimension_id, {})

    levels = []
    for level in dim.get("levels", []) or []:
        levels.append(
            {
                "rank": level.get("rank"),
                "summary": level.get("summary", ""),
                "descriptors": list(level.get("descriptors") or []),
            }
        )

    span_by_id = {span.span_id: span for span in evidence_spans}
    facet_evidence = []
    for finding in observation.facet_findings:
        supporting = []
        for span_id in finding.supporting_span_ids:
            span = span_by_id.get(span_id)
            if span is None or not (span.text_quote or "").strip():
                continue
            supporting.append(
                {
                    "span_id": span_id,
                    "quote": span.text_quote or "",
                    "source_type": span.source_type,
                    "source_label": span.source_label or "",
                }
            )

        counter = []
        for span_id in finding.counter_span_ids:
            span = span_by_id.get(span_id)
            if span is None or not (span.text_quote or "").strip():
                continue
            counter.append(
                {
                    "span_id": span_id,
                    "quote": span.text_quote or "",
                    "source_type": span.source_type,
                    "source_label": span.source_label or "",
                }
            )

        facet_evidence.append(
            {
                "facet_id": finding.facet_id,
                "supporting": supporting,
                "counter": counter,
                "finding_note": finding.finding_note or "",
            }
        )

    raw_ctx = scoring_context if isinstance(scoring_context, dict) else {}
    role_description = str(
        raw_ctx.get("role_description")
        or "You are a trained writing scorer evaluating student writing based on a rubric."
    )
    dataset_notes = str(raw_ctx.get("dataset_notes") or "")
    calibration_notes = str(raw_ctx.get("calibration_notes") or "")

    score_anchors = []
    for raw_anchor in raw_ctx.get("score_anchors") or []:
        if not isinstance(raw_anchor, dict):
            continue
        per_dimension = raw_anchor.get("per_dimension")
        if not isinstance(per_dimension, dict):
            continue
        dim_anchor = per_dimension.get(observation.dimension_id)
        if not isinstance(dim_anchor, dict):
            continue
        score_anchors.append(
            {
                "anchor_id": str(raw_anchor.get("anchor_id") or ""),
                "title": str(raw_anchor.get("title") or "Untitled anchor"),
                "target_score": int(raw_anchor.get("target_score") or 0),
                "dimension_score": int(dim_anchor.get("score") or 0),
                "note": str(dim_anchor.get("note") or ""),
            }
        )

    prior_rater_context = [
        {
            "rater_id": hyp.rater_id,
            "score": hyp.score.canonical_score,
            "justification": hyp.rationale or "",
        }
        for hyp in (prior_hypotheses or [])
    ]

    context = {
        "role_description": role_description,
        "dimension_name": dim.get("name", observation.dimension_id),
        "dimension_code": dim.get("code", ""),
        "levels": levels,
        "facet_evidence": facet_evidence,
        "observation_confidence": observation.observation_confidence.value.upper(),
        "uncertainty_notes": list(observation.uncertainty_notes),
        "score_anchors": score_anchors,
        "dataset_notes": dataset_notes,
        "calibration_notes": calibration_notes,
        "dimension_override_notes": "",
        "prior_rater_context": prior_rater_context,
    }

    if override_template is not None:
        context["dimension_override_notes"] = render_template(override_template, context)

    return render_template(template, context)


def build_explanation_prompt(
    decision: FinalDimensionDecision,
    observation: DimensionObservation,
    evidence_spans: List[EvidenceSpan],
    rubric: RubricSnapshot,
    template: PromptTemplate,
    scorer_rationale: Optional[str] = None,
    override_template: Optional[PromptTemplate] = None,
) -> str:
    """
    Build the explanation/feedback prompt for a finalised dimension decision.

    Context variables injected (matching explanation.yaml v2):
        dimension_name          : Human-readable dimension name from rubric.
        dimension_code          : Short code from rubric.
        canonical_score         : Integer canonical score from the final decision.
        display_annotation      : Optional display annotation string.
        descriptor_refs         : List[str] of descriptor strings cited in the decision.
        facet_evidence          : Per-facet evidence buckets (supporting/counter).
        observation_confidence  : HIGH/MEDIUM/LOW.
        uncertainty_notes       : List[str] from observation.
        scorer_rationale        : Scorer justification text (seed material).
        decision_note           : Reconciliation/adjudication context note.
        was_adjudicated         : Whether decision was adjudicated.
        dimension_override_notes: Rendered override guidance text.

    For backward compatibility, `evidence_spans` remains in context as a flat
    list of quoted spans.

    Args:
        decision        : FinalDimensionDecision containing score and descriptor refs.
        observation     : DimensionObservation for structured facet evidence.
        evidence_spans  : EvidenceSpan objects available to this decision.
        rubric          : RubricSnapshot for dimension metadata.
        template        : Loaded global explanation PromptTemplate.
        scorer_rationale: Optional scorer justification text.
        override_template: Optional per-dimension override PromptTemplate.

    Returns:
        Rendered prompt string ready to send to a provider.
    """
    dim = rubric.dimension_by_id.get(decision.dimension_id, {})
    referenced_ids = set(decision.evidence_span_ids)
    span_by_id: Dict[str, EvidenceSpan] = {
        span.span_id: span
        for span in evidence_spans
        if span.span_id in referenced_ids
    }

    facet_evidence = []
    for finding in observation.facet_findings:
        supporting = []
        for span_id in finding.supporting_span_ids:
            span = span_by_id.get(span_id)
            if span is None or not (span.text_quote or "").strip():
                continue
            supporting.append(
                {
                    "span_id": span_id,
                    "quote": span.text_quote or "",
                    "source_type": span.source_type,
                    "source_label": span.source_label or "",
                }
            )

        counter = []
        for span_id in finding.counter_span_ids:
            span = span_by_id.get(span_id)
            if span is None or not (span.text_quote or "").strip():
                continue
            counter.append(
                {
                    "span_id": span_id,
                    "quote": span.text_quote or "",
                    "source_type": span.source_type,
                    "source_label": span.source_label or "",
                }
            )

        facet_evidence.append(
            {
                "facet_id": finding.facet_id,
                "supporting": supporting,
                "counter": counter,
                "finding_note": finding.finding_note or "",
            }
        )

    span_contexts = [
        {
            "span_id": span_id,
            "quote": span_by_id[span_id].text_quote or "",
            "source_type": span_by_id[span_id].source_type,
            "source_label": span_by_id[span_id].source_label or "",
        }
        for span_id in decision.evidence_span_ids
        if span_id in span_by_id and (span_by_id[span_id].text_quote or "").strip()
    ]

    context = {
        "dimension_name": dim.get("name", decision.dimension_id),
        "dimension_code": dim.get("code", ""),
        "canonical_score": decision.final_score.canonical_score,
        "display_annotation": decision.final_score.display_annotation or "",
        "descriptor_refs": list(decision.descriptor_refs),
        "facet_evidence": facet_evidence,
        "observation_confidence": observation.observation_confidence.value.upper(),
        "uncertainty_notes": list(observation.uncertainty_notes),
        "scorer_rationale": scorer_rationale or "",
        "decision_note": decision.decision_note or "",
        "was_adjudicated": decision.adjudication_id is not None,
        "dimension_override_notes": "",
        # Backward compatibility for existing templates.
        "evidence_spans": span_contexts,
    }
    if override_template is not None:
        context["dimension_override_notes"] = render_template(override_template, context)

    chosen_template = override_template or template
    return render_template(chosen_template, context)
