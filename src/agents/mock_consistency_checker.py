"""
Mock Consistency Checker — contract-driven conflict detection.

Reads the non-adjacent threshold from PolicySnapshot.adjudication_policy
(zero-hardcoding: no threshold value is hardcoded here). For each dimension
where two or more ScoreHypotheses exist, checks whether any pair's absolute
score difference exceeds the threshold. If so, emits a ConflictRecord.

Only the first score_distance trigger is evaluated (sufficient for MVP mock).
"""

from __future__ import annotations

import hashlib
from typing import Dict, List

from src.contracts.artifact_bundle import PolicySnapshot
from src.contracts.scoring import (
    ConflictRecord,
    ConflictType,
    ResolutionPath,
    ScoreHypothesis,
)


def _hid(seed: str, length: int = 12) -> str:
    return hashlib.md5(seed.encode()).hexdigest()[:length]


def _get_non_adjacent_threshold(policy: PolicySnapshot) -> int:
    """Read the non-adjacent score threshold from the first score_distance trigger."""
    triggers = policy.adjudication_policy.get("triggers", [])
    for trigger in triggers:
        if trigger.get("type") == "score_distance":
            return int(trigger.get("threshold", {}).get("value", 1))
    return 1  # safe default if no score_distance trigger is configured


def _get_trigger_rule_id(policy: PolicySnapshot) -> str:
    """Return the rule_id of the first score_distance trigger."""
    triggers = policy.adjudication_policy.get("triggers", [])
    for trigger in triggers:
        if trigger.get("type") == "score_distance":
            return trigger.get("trigger_id", "non_adjacent_trigger")
    return "non_adjacent_trigger"


def run(
    hypotheses: List[ScoreHypothesis],
    policy: PolicySnapshot,
) -> List[ConflictRecord]:
    """Detect scoring conflicts between raters for each dimension.

    A conflict is detected when the absolute score difference between any two
    hypotheses for the same dimension exceeds the policy threshold.

    Args:
        hypotheses: All ScoreHypotheses produced by the scoring stage.
        policy: PolicySnapshot containing adjudication trigger definitions.

    Returns:
        List of ConflictRecord objects (empty if no conflicts detected).
    """
    threshold = _get_non_adjacent_threshold(policy)
    rule_id = _get_trigger_rule_id(policy)

    # Group hypotheses by dimension_id
    by_dim: Dict[str, List[ScoreHypothesis]] = {}
    for h in hypotheses:
        by_dim.setdefault(h.dimension_id, []).append(h)

    conflicts: List[ConflictRecord] = []
    for dim_id, dim_hyps in by_dim.items():
        # Sort by hypothesis_id for deterministic pair ordering
        dim_hyps = sorted(dim_hyps, key=lambda h: h.hypothesis_id)
        for i in range(len(dim_hyps)):
            for j in range(i + 1, len(dim_hyps)):
                h1, h2 = dim_hyps[i], dim_hyps[j]
                diff = abs(
                    h1.score.canonical_score - h2.score.canonical_score
                )
                if diff > threshold:
                    conflict_id = f"conflict-{_hid(f'{h1.hypothesis_id}:{h2.hypothesis_id}')}"
                    conflicts.append(
                        ConflictRecord(
                            conflict_id=conflict_id,
                            dimension_id=dim_id,
                            hypothesis_ids=[h1.hypothesis_id, h2.hypothesis_id],
                            conflict_type=ConflictType.NON_ADJACENT,
                            trigger_rule_id=rule_id,
                            conflict_detail=(
                                f"Score diff {diff} > threshold {threshold} "
                                f"({h1.rater_id}:{h1.score.canonical_score} vs "
                                f"{h2.rater_id}:{h2.score.canonical_score})"
                            ),
                            recommended_path=ResolutionPath.THIRD_RATER,
                        )
                    )

    return conflicts
