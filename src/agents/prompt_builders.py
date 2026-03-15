"""
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

from typing import List

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
) -> str:
    """
    Build the evidence-extraction prompt for a single dimension.

    Context variables injected (matching evidence_extraction.yaml):
        dimension_name   : Human-readable dimension name from rubric.
        dimension_code   : Short code from rubric (e.g. "I").
        trait_description: One-line summary of the first rubric level, or empty.
        required_facets  : List[str] of observation facet IDs.
        essay_text       : Full source text of the document.

    Args:
        plan    : CoveragePlan defining which dimension and facets to cover.
        document: NormalizedDocument containing the essay text.
        rubric  : RubricSnapshot providing dimension metadata.
        template: Loaded PromptTemplate (should be evidence_extraction.yaml).

    Returns:
        Rendered prompt string ready to send to a provider.
    """
    dim = rubric.dimension_by_id.get(plan.dimension_id, {})
    essay_text = " ".join(u.text for u in document.text_units)
    trait_description = ""
    levels = dim.get("levels", [])
    if levels:
        trait_description = levels[0].get("summary", "")

    context = {
        "dimension_name": dim.get("name", plan.dimension_id),
        "dimension_code": dim.get("code", ""),
        "trait_description": trait_description,
        "required_facets": list(plan.required_facets),
        "essay_text": essay_text,
    }
    return render_template(template, context)


def build_scoring_prompt(
    observation: DimensionObservation,
    evidence_spans: List[EvidenceSpan],
    rubric: RubricSnapshot,
    document: NormalizedDocument,
    template: PromptTemplate,
) -> str:
    """
    Build the scoring prompt for a single dimension.

    Context variables injected (matching scoring.yaml):
        dimension_name  : Human-readable dimension name from rubric.
        dimension_code  : Short code from rubric.
        levels          : List of level dicts [{rank, summary, descriptors}].
        evidence_spans  : List of span dicts [{quote, facets}] from observation's spans.
        essay_text      : Full source text of the document.

    Args:
        observation   : DimensionObservation summarising extracted evidence.
        evidence_spans: Relevant EvidenceSpan objects supporting the observation.
        rubric        : RubricSnapshot for dimension/scale/level lookup.
        document      : NormalizedDocument for essay text.
        template      : Loaded PromptTemplate (should be scoring.yaml).

    Returns:
        Rendered prompt string ready to send to a provider.
    """
    dim = rubric.dimension_by_id.get(observation.dimension_id, {})
    levels = dim.get("levels", [])
    essay_text = " ".join(u.text for u in document.text_units)

    # Build evidence context — only spans referenced in this observation
    referenced_ids = set(observation.supporting_span_ids) | set(observation.counter_span_ids)
    span_contexts = [
        {
            "quote": s.text_quote or "",
            "facets": list(s.facet_ids),
        }
        for s in evidence_spans
        if s.span_id in referenced_ids
    ]

    context = {
        "dimension_name": dim.get("name", observation.dimension_id),
        "dimension_code": dim.get("code", ""),
        "levels": levels,
        "evidence_spans": span_contexts,
        "essay_text": essay_text,
    }
    return render_template(template, context)


def build_explanation_prompt(
    decision: FinalDimensionDecision,
    evidence_spans: List[EvidenceSpan],
    rubric: RubricSnapshot,
    template: PromptTemplate,
) -> str:
    """
    Build the explanation/feedback prompt for a finalised dimension decision.

    Context variables injected (matching explanation.yaml):
        dimension_name    : Human-readable dimension name from rubric.
        dimension_code    : Short code from rubric.
        canonical_score   : Integer canonical score from the final decision.
        display_annotation: Optional display annotation string (may be empty).
        descriptor_refs   : List[str] of descriptor strings cited in the decision.
        evidence_spans    : List of span dicts [{quote}] referenced in the decision.

    Args:
        decision      : FinalDimensionDecision containing score and descriptor refs.
        evidence_spans: EvidenceSpan objects referenced in the decision.
        rubric        : RubricSnapshot for dimension metadata.
        template      : Loaded PromptTemplate (should be explanation.yaml).

    Returns:
        Rendered prompt string ready to send to a provider.
    """
    dim = rubric.dimension_by_id.get(decision.dimension_id, {})
    referenced_ids = set(decision.evidence_span_ids)

    span_contexts = [
        {"quote": s.text_quote or ""}
        for s in evidence_spans
        if s.span_id in referenced_ids
    ]

    context = {
        "dimension_name": dim.get("name", decision.dimension_id),
        "dimension_code": dim.get("code", ""),
        "canonical_score": decision.final_score.canonical_score,
        "display_annotation": decision.final_score.display_annotation or "",
        "descriptor_refs": list(decision.descriptor_refs),
        "evidence_spans": span_contexts,
    }
    return render_template(template, context)
