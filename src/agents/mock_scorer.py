"""
Mock Scorer — deterministic ScoreHypothesis generation.

Derives a canonical score from a hash of (observation_id + rater_id +
dimension_id), then maps it into the dimension's valid scale range
[min, max] (read from RubricSnapshot — zero-hardcoding).

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

    Scale range is read from the rubric config via RubricSnapshot; no scale
    values are hardcoded here.

    Args:
        observation: The DimensionObservation to score.
        rubric: RubricSnapshot used to look up the dimension's scale.
        rater_id: Identifies the scoring agent (e.g., "rater_1", "rater_2").

    Returns:
        A ScoreHypothesis with a score within the dimension's valid range.
    """
    dim = rubric.dimension_by_id.get(observation.dimension_id, {})
    scale_ref: str = dim.get("scale_ref", "unknown_scale")
    scale = rubric.scale_by_id.get(scale_ref, {})
    min_score: int = int(scale.get("min", 1))
    max_score: int = int(scale.get("max", 6))

    seed = f"{observation.observation_id}:{rater_id}:{observation.dimension_id}"
    score_val = _derive_score(seed, min_score, max_score)
    score = create_score_representation(score_val, scale_ref)

    hypothesis_id = f"hyp-{_hid(seed)}"
    evidence_span_ids: List[str] = list(observation.supporting_span_ids)

    return ScoreHypothesis(
        hypothesis_id=hypothesis_id,
        observation_id=observation.observation_id,
        dimension_id=observation.dimension_id,
        rater_id=rater_id,
        score=score,
        descriptor_refs=[f"level_{score_val}"],
        evidence_span_ids=evidence_span_ids,
        rationale=f"Mock score {score_val} for {observation.dimension_id} by {rater_id}",
        confidence=0.8,
    )
