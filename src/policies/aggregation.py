"""
聚合策略模块，负责按配置计算 composite 总分。

Aggregation Policy — config-driven composite score computation.

Evaluates the aggregation formula from PolicySnapshot configuration.
Supports aggregation methods:

- average_per_trait_then_weighted_sum: For each dimension, average the scores
  from the configured source_raters, then multiply by the configured weight.
  Used when no third-rater resolution occurred.
- average_per_trait_then_weighted_average: Same as above, but divide by the
  total participating weight so the final score stays on the original scale.

- direct_weighted_sum: Use FinalDimensionDecision.final_score directly for
  each dimension, multiplied by the configured weight.
  Used when third-rater resolution was applied.
- direct_weighted_average: Same as above, but normalized by total
  participating weight.

Variant selection:
  - If any AdjudicationRecord has is_resolved=True → "resolution_used" variant.
  - Otherwise → "resolution_not_used" variant.

All dimension weights, rater IDs, and variant conditions are read from
PolicySnapshot.aggregation_policy — nothing is hardcoded here.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from src.contracts.artifact_bundle import PolicySnapshot
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import (
    AdjudicationRecord,
    CompositeDecision,
    FinalDimensionDecision,
    ScoreHypothesis,
)


def _hid(seed: str, length: int = 12) -> str:
    return hashlib.md5(seed.encode()).hexdigest()[:length]


def _round_half_up(value: float) -> int:
    return int(value + 0.5)


def _is_resolution_used(adjudications: List[AdjudicationRecord]) -> bool:
    """Return True if any adjudication record was successfully resolved."""
    return any(a.is_resolved for a in adjudications)


def _select_variant(
    variants: List[Dict[str, Any]], resolution_used: bool
) -> Optional[Dict[str, Any]]:
    """Select the appropriate formula variant based on resolution status."""
    applies_when = "resolution_used" if resolution_used else "resolution_not_used"
    for v in variants:
        if v.get("applies_when") == applies_when:
            return v
    return None


def _compute_average_then_weighted(
    hypotheses: List[ScoreHypothesis],
    source_raters: List[str],
    weights: Dict[str, int],
) -> tuple[float, List[str], float]:
    """Compute weighted sum using per-rater averages per dimension.

    Returns (weighted_total, contributing_dim_ids, contributing_weight_total).
    """
    by_dim: Dict[str, Dict[str, int]] = {}
    for h in hypotheses:
        if h.rater_id in source_raters:
            by_dim.setdefault(h.dimension_id, {})[h.rater_id] = h.score.canonical_score

    total = 0.0
    contributing: List[str] = []
    contributing_weight_total = 0.0

    for dim_id, rater_scores in sorted(by_dim.items()):
        w = weights.get(dim_id, 0)
        if w == 0:
            continue
        scores = [rater_scores[r] for r in source_raters if r in rater_scores]
        if not scores:
            continue
        avg = sum(scores) / len(scores)
        total += avg * w
        contributing.append(dim_id)
        contributing_weight_total += w

    return total, contributing, contributing_weight_total


def _compute_direct_weighted(
    decisions: List[FinalDimensionDecision],
    weights: Dict[str, int],
) -> tuple[float, List[str], float]:
    """Compute weighted sum using final_score.canonical_score directly.

    Returns (weighted_total, contributing_dim_ids, contributing_weight_total).
    """
    total = 0.0
    contributing: List[str] = []
    contributing_weight_total = 0.0

    for decision in sorted(decisions, key=lambda d: d.dimension_id):
        w = weights.get(decision.dimension_id, 0)
        if w == 0:
            continue
        total += decision.final_score.canonical_score * w
        contributing.append(decision.dimension_id)
        contributing_weight_total += w

    return total, contributing, contributing_weight_total


def compute_composite(
    decisions: List[FinalDimensionDecision],
    hypotheses: List[ScoreHypothesis],
    adjudications: List[AdjudicationRecord],
    policy: PolicySnapshot,
    policy_ref: str = "",
) -> Optional[CompositeDecision]:
    """Compute an optional CompositeDecision from config-driven formula.

    Reads the composite_formula from policy.aggregation_policy, selects the
    appropriate variant based on whether resolution was used, computes the
    weighted aggregate score, and returns a CompositeDecision.

    Returns None if no composite_formula is defined in the policy.

    Args:
        decisions: FinalDimensionDecision list (one per dimension).
        hypotheses: ScoreHypothesis list (all raters, all dimensions).
        adjudications: AdjudicationRecord list for resolution status check.
        policy: PolicySnapshot containing aggregation policy config.
        policy_ref: Optional URI reference to the aggregation policy artifact.

    Returns:
        CompositeDecision, or None if aggregation is not configured.
    """
    agg = policy.aggregation_policy
    variants = agg.get("composite_formula", [])
    if not variants:
        return None

    resolution_used = _is_resolution_used(adjudications)
    variant = _select_variant(variants, resolution_used)
    if variant is None:
        return None

    method = variant.get("aggregation_method", "")
    source_raters: List[str] = variant.get("source_raters", [])

    raw_weights = variant.get("weights", {})
    if raw_weights == "auto_equal":
        # Derive equal weights at runtime from the dimensions actually present in
        # the data, so the policy file doesn't need to enumerate dimension codes.
        if "direct" in method:
            auto_dim_ids: List[str] = [d.dimension_id for d in decisions]
        else:
            auto_dim_ids = list({
                h.dimension_id for h in hypotheses if h.rater_id in source_raters
            })
        weights: Dict[str, int] = {dim_id: 1 for dim_id in auto_dim_ids}
    else:
        weights = {k: int(v) for k, v in raw_weights.items()}

    if method == "average_per_trait_then_weighted_sum":
        total, contributing_dims, weight_total = _compute_average_then_weighted(
            hypotheses, source_raters, weights
        )
        composite_score_val = _round_half_up(total)
    elif method == "average_per_trait_then_weighted_average":
        total, contributing_dims, weight_total = _compute_average_then_weighted(
            hypotheses, source_raters, weights
        )
        if weight_total <= 0:
            return None
        composite_score_val = _round_half_up(total / weight_total)
    elif method == "direct_weighted_sum":
        total, contributing_dims, weight_total = _compute_direct_weighted(decisions, weights)
        composite_score_val = _round_half_up(total)
    elif method == "direct_weighted_average":
        total, contributing_dims, weight_total = _compute_direct_weighted(decisions, weights)
        if weight_total <= 0:
            return None
        composite_score_val = _round_half_up(total / weight_total)
    else:
        return None

    # Determine contributing decision IDs from contributing dimensions
    decision_by_dim = {d.dimension_id: d for d in decisions}
    contributing_decision_ids = [
        decision_by_dim[dim_id].decision_id
        for dim_id in contributing_dims
        if dim_id in decision_by_dim
    ]

    # Build a scale_ref from the policy; fallback to policy_id
    policy_id = agg.get("policy_id", "aggregation")
    composite_id = f"composite-{_hid(policy_id + str(composite_score_val))}"

    # Use a synthetic composite scale ref (not a rubric scale — composite has its own range)
    composite_score = create_score_representation(
        canonical_score=composite_score_val,
        scale_ref=f"composite:{policy_id}",
    )

    return CompositeDecision(
        composite_id=composite_id,
        aggregation_policy_ref=policy_ref,
        contributing_decision_ids=contributing_decision_ids,
        composite_score=composite_score,
        aggregation_detail={
            "variant_id": variant.get("variant_id", ""),
            "aggregation_method": method,
            "source_raters": source_raters,
            "weights": weights,
            "weight_total": weight_total,
            "resolution_used": resolution_used,
        },
        composite_note=None,
    )
