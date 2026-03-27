"""
Scorer — calls a configured provider to produce a ScoreHypothesis.

Builds the scoring prompt via prompt_builders, calls the provider,
parses structured JSON output into a ScoreHypothesis contract.

The valid score range is resolved from the RubricSnapshot; no hardcoded
score values or dimension codes appear here.
"""

from __future__ import annotations

import uuid
from typing import List

from src.agents.prompt_builders import build_scoring_prompt
from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.evidence import DimensionObservation, EvidenceSpan
from src.contracts.request_models import NormalizedDocument
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import ScoreHypothesis
from src.policies.rubric_core import get_descriptor_refs_for_score, get_scale_range, get_scale_ref
from src.providers.base import BaseProvider, LLMRequest
from src.providers.prompt_loader import PromptTemplate
from src.providers.structured_output import normalize_structured_output

_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["proposed_score"],
    "properties": {
        "proposed_score": {"type": "integer"},
        "descriptor_refs": {"type": "array", "items": {"type": "string"}},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
        "justification": {"type": "string"},
    },
}


def run(
    observation: DimensionObservation,
    evidence_spans: List[EvidenceSpan],
    rubric: RubricSnapshot,
    document: NormalizedDocument,
    provider: BaseProvider,
    template: PromptTemplate,
    rater_id: str,
) -> ScoreHypothesis:
    """
    Score one dimension using a configured provider.

    Args:
        observation  : DimensionObservation for the dimension.
        evidence_spans: Evidence spans referenced in the observation.
        rubric       : RubricSnapshot for scale/descriptor lookup.
        document     : NormalizedDocument for essay text in the prompt.
        provider     : Configured BaseProvider to call.
        template     : Loaded scoring prompt template.
        rater_id     : Rater identifier (e.g. "rater_1").

    Returns:
        ScoreHypothesis with score clamped to the valid rubric scale range.
    """
    prompt_text = build_scoring_prompt(observation, evidence_spans, rubric, document, template)
    request = LLMRequest(prompt=prompt_text, output_schema=_OUTPUT_SCHEMA)
    response = provider.complete(request)

    if response.structured_data is not None:
        data = response.structured_data
    else:
        data = normalize_structured_output(response.content, schema=_OUTPUT_SCHEMA)

    dim_id = observation.dimension_id
    scale_min, scale_max = get_scale_range(rubric, dim_id)
    scale_ref = get_scale_ref(rubric, dim_id)

    raw_score = int(data.get("proposed_score", scale_min))
    score_val = max(scale_min, min(scale_max, raw_score))
    score = create_score_representation(score_val, scale_ref)

    descriptor_refs: List[str] = list(data.get("descriptor_refs") or [])
    if not descriptor_refs:
        descriptor_refs = get_descriptor_refs_for_score(rubric, dim_id, score_val) or [f"level_{score_val}"]

    evidence_span_ids: List[str] = list(observation.supporting_span_ids)
    confidence = float(data.get("confidence", 0.7))
    justification = str(data.get("justification", ""))

    return ScoreHypothesis(
        hypothesis_id=f"hyp-score-{uuid.uuid4().hex[:12]}",
        observation_id=observation.observation_id,
        dimension_id=dim_id,
        rater_id=rater_id,
        score=score,
        descriptor_refs=descriptor_refs,
        evidence_span_ids=evidence_span_ids,
        rationale=justification,
        confidence=confidence,
    )
