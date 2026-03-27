"""
Consistency Checker — policy-driven conflict detection.

Evaluates all configured adjudication triggers from PolicySnapshot
against scoring hypotheses. This is the policy-aware version that
delegates to ``src.policies.adjudication.evaluate_all_triggers``.

The deterministic_consistency_checker (Phase 3) only evaluated the first
score_distance trigger. This module evaluates ALL configured triggers,
including pattern_match (cusp) rules.
"""

from __future__ import annotations

from typing import List

from src.contracts.artifact_bundle import PolicySnapshot
from src.contracts.scoring import ConflictRecord, ScoreHypothesis
from src.policies.adjudication import evaluate_all_triggers


def run(
    hypotheses: List[ScoreHypothesis],
    policy: PolicySnapshot,
) -> List[ConflictRecord]:
    """Detect scoring conflicts using all configured adjudication triggers.

    Args:
        hypotheses: All ScoreHypotheses produced by the scoring stage.
        policy: PolicySnapshot containing adjudication trigger definitions.

    Returns:
        List of ConflictRecord objects (empty if no conflicts detected).
    """
    return evaluate_all_triggers(hypotheses, policy)
