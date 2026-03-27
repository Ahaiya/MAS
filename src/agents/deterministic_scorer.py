"""
Deterministic Scorer — deterministic ScoreHypothesis generation.

Derives a canonical score from a hash of (observation_id + rater_id +
dimension_id), then maps it into the dimension's valid scale range
[min, max]. Scale range and descriptor lookup are delegated to
``src.policies.rubric_core`` (zero-hardcoding).

Different rater_ids produce different hypothesis_ids (and typically different
scores) because the seed string includes the rater_id.
"""

from __future__ import annotations

import hashlib
from typing import List

from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.evidence import DimensionObservation
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import ScoreHypothesis
from src.policies.rubric_core import (
    get_descriptor_refs_for_score,
    get_scale_range,
    get_scale_ref,
)


def _hid(seed: str, length: int = 12) -> str:
    return hashlib.md5(seed.encode()).hexdigest()[:length]


def _derive_score(seed: str, min_score: int, max_score: int) -> int:
    """Map a seed string to an integer in [min_score, max_score] deterministically."""
    val = int(hashlib.md5(seed.encode()).hexdigest()[:4], 16)
    return val % (max_score - min_score + 1) + min_score


def run(
    observation: DimensionObservation,
    rubric: RubricSnapshot,
    rater_id: str,
) -> ScoreHypothesis:
    """Generate a deterministic score hypothesis for one rater.

    Scale range and descriptors are resolved through rubric_core policy
    functions — no raw dict access or hardcoded values here.

    Args:
        observation: The DimensionObservation to score.
        rubric: RubricSnapshot used to look up the dimension's scale.
        rater_id: Identifies the scoring agent (e.g., "rater_1", "rater_2").

    Returns:
        A ScoreHypothesis with a score within the dimension's valid range.
    """
    dim_id = observation.dimension_id
    min_score, max_score = get_scale_range(rubric, dim_id)
    scale_ref = get_scale_ref(rubric, dim_id)

    seed = f"{observation.observation_id}:{rater_id}:{dim_id}"
    score_val = _derive_score(seed, min_score, max_score)
    score = create_score_representation(score_val, scale_ref)

    descriptor_refs = get_descriptor_refs_for_score(rubric, dim_id, score_val)
    if not descriptor_refs:
        descriptor_refs = [f"level_{score_val}"]

    hypothesis_id = f"hyp-{_hid(seed)}"
    evidence_span_ids: List[str] = list(observation.supporting_span_ids)

    return ScoreHypothesis(
        hypothesis_id=hypothesis_id,
        observation_id=observation.observation_id,
        dimension_id=dim_id,
        rater_id=rater_id,
        score=score,
        descriptor_refs=descriptor_refs,
        evidence_span_ids=evidence_span_ids,
        rationale=f"Deterministic score {score_val} for {dim_id} by {rater_id}",
        confidence=0.8,
    )
