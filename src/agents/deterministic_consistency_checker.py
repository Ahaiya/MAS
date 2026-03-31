"""
确定性一致性检查 Agent，在 mock 模式下复用同一套冲突触发策略。

Deterministic Consistency Checker — policy-aware conflict detection for mock mode.

The mock pipeline should exercise the same adjudication trigger logic as the
real pipeline. This wrapper therefore reuses the shared config-driven trigger
evaluator instead of carrying a simplified non-adjacent-only implementation.
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
    """Detect scoring conflicts using the full adjudication policy."""
    return evaluate_all_triggers(hypotheses, policy)
