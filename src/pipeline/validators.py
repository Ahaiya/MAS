"""
流水线校验器，负责在关键阶段前后检查 contract 不变量。

Pipeline Validators — pre/post-stage invariant checks.

Each validator is a pure function that raises ValueError when an expected
invariant is violated. All checks are contract-driven (no hardcoded trait
names, dimension codes, or score thresholds).

Validators are called by the PipelineRunner between stages to catch
missing outputs early, before they silently corrupt downstream stages.
"""

from __future__ import annotations

from typing import List

from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.evidence import DimensionObservation
from src.contracts.request_models import CoveragePlan
from src.contracts.scoring import FinalDimensionDecision, ScoreHypothesis


def validate_coverage_plans(
    plans: List[CoveragePlan],
    rubric: RubricSnapshot,
) -> None:
    """Verify every rubric dimension has a corresponding CoveragePlan.

    Raises:
        ValueError: If any rubric dimension is missing from plans.
    """
    planned_dims = {p.dimension_id for p in plans}
    rubric_dims = {d["dimension_id"] for d in rubric.dimensions}
    missing = rubric_dims - planned_dims
    if missing:
        raise ValueError(
            f"Missing coverage plans for dimensions: {sorted(missing)}"
        )


def validate_observations(
    observations: List[DimensionObservation],
    plans: List[CoveragePlan],
) -> None:
    """Verify every CoveragePlan has a corresponding DimensionObservation.

    Raises:
        ValueError: If any planned dimension lacks an observation.
    """
    obs_dims = {o.dimension_id for o in observations}
    plan_dims = {p.dimension_id for p in plans}
    missing = plan_dims - obs_dims
    if missing:
        raise ValueError(
            f"Missing observations for dimensions: {sorted(missing)}"
        )


def validate_hypotheses(
    hypotheses: List[ScoreHypothesis],
    plans: List[CoveragePlan],
    rater_ids: List[str],
) -> None:
    """Verify every (dimension_id, rater_id) combination has a ScoreHypothesis.

    Raises:
        ValueError: If any required (dimension, rater) pair is absent.
    """
    plan_dims = {p.dimension_id for p in plans}
    scored_pairs = {(h.dimension_id, h.rater_id) for h in hypotheses}
    missing = [
        (dim_id, rater_id)
        for dim_id in sorted(plan_dims)
        for rater_id in rater_ids
        if (dim_id, rater_id) not in scored_pairs
    ]
    if missing:
        raise ValueError(
            f"Missing hypotheses for (dimension_id, rater_id) pairs: {missing}"
        )


def validate_final_decisions(
    decisions: List[FinalDimensionDecision],
    plans: List[CoveragePlan],
) -> None:
    """Verify every planned dimension has a FinalDimensionDecision.

    Raises:
        ValueError: If any planned dimension lacks a final decision.
    """
    dec_dims = {d.dimension_id for d in decisions}
    plan_dims = {p.dimension_id for p in plans}
    missing = plan_dims - dec_dims
    if missing:
        raise ValueError(
            f"Missing final decisions for dimensions: {sorted(missing)}"
        )


def terminal_validation(
    decisions: List[FinalDimensionDecision],
    plans: List[CoveragePlan],
    rubric: RubricSnapshot,
) -> bool:
    """Check that all final decisions exist and have scores within valid scale ranges.

    Scale ranges are read from RubricSnapshot (config-driven, zero-hardcoding).

    Returns:
        True if all dimensions have a valid in-range score; False otherwise.
    """
    plan_dims = {p.dimension_id for p in plans}
    dec_by_dim = {d.dimension_id: d for d in decisions}

    for dim_id in plan_dims:
        if dim_id not in dec_by_dim:
            return False
        decision = dec_by_dim[dim_id]
        if not rubric.validate_score(dim_id, decision.final_score.canonical_score):
            return False

    return True
