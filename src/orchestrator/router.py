"""
Router — contract-driven routing decisions for the state machine.

Router functions inspect contract field values (ConflictRecord.recommended_path,
AdjudicationRecord.is_resolved, etc.) to determine the next PipelineState.

Design invariants:
- Routing logic reads contract fields ONLY. No hardcoded trait names,
  adjudication thresholds, composite formulas, or dimension-specific rules.
- The router does NOT call workers or modify state. It returns a PipelineState
  that the orchestrator passes to StateGraph.advance().
- When multiple conflicts/records exist, the most severe recommended_path wins.
  Severity order (highest first): HUMAN_REVIEW > RE_EXTRACT > RE_SCORE >
  THIRD_RATER / POLICY_AVERAGE (both route to ADJUDICATED).
"""

from __future__ import annotations

from typing import List

from src.orchestrator.states import PipelineState
from src.contracts.scoring import (
    ConflictRecord,
    AdjudicationRecord,
    ResolutionPath,
)

# ── Severity ranking for ResolutionPath ────────────────────────────────────────
# Higher number = more severe = takes priority when multiple conflicts exist.

_PATH_SEVERITY = {
    ResolutionPath.POLICY_AVERAGE: 0,
    ResolutionPath.THIRD_RATER: 0,
    ResolutionPath.RE_SCORE: 1,
    ResolutionPath.RE_EXTRACT: 2,
    ResolutionPath.HUMAN_REVIEW: 3,
}

# Map ResolutionPath to the PipelineState it routes to.
_PATH_TO_STATE = {
    ResolutionPath.THIRD_RATER: PipelineState.ADJUDICATED,
    ResolutionPath.POLICY_AVERAGE: PipelineState.ADJUDICATED,
    ResolutionPath.RE_SCORE: PipelineState.RE_SCORE,
    ResolutionPath.RE_EXTRACT: PipelineState.RE_EXTRACT,
    ResolutionPath.HUMAN_REVIEW: PipelineState.HUMAN_REVIEW,
}


def route_after_consistency_check(
    conflicts: List[ConflictRecord],
) -> PipelineState:
    """Determine next state after the Consistency Checker completes.

    Args:
        conflicts: ConflictRecords produced by the Consistency Checker.
                   Empty list means no conflicts detected.

    Returns:
        PipelineState.FEEDBACK_RENDERED  — if no conflicts (skip adjudication).
        PipelineState.ADJUDICATED        — if conflicts require adjudication.
        PipelineState.RE_EXTRACT         — if any conflict recommends re-extraction.
        PipelineState.RE_SCORE           — if any conflict recommends re-scoring.
        PipelineState.HUMAN_REVIEW       — if any conflict recommends human review.

    When multiple conflicts exist, the highest-severity recommended_path wins.
    """
    if not conflicts:
        return PipelineState.FEEDBACK_RENDERED

    worst_path = max(
        (cr.recommended_path for cr in conflicts),
        key=lambda p: _PATH_SEVERITY.get(p, 0),
    )
    return _PATH_TO_STATE[worst_path]


def route_after_adjudication(
    records: List[AdjudicationRecord],
) -> PipelineState:
    """Determine next state after the Adjudicator completes.

    Args:
        records: AdjudicationRecords produced by the Adjudicator.

    Returns:
        PipelineState.FEEDBACK_RENDERED  — if all records are resolved.
        PipelineState.RE_EXTRACT         — if any unresolved record needs re-extraction.
        PipelineState.RE_SCORE           — if any unresolved record needs re-scoring.
        PipelineState.HUMAN_REVIEW       — if any unresolved record needs human review.

    When multiple unresolved records exist, the highest-severity path wins.
    """
    unresolved = [r for r in records if not r.is_resolved]

    if not unresolved:
        return PipelineState.FEEDBACK_RENDERED

    worst_path = max(
        (r.resolution_path for r in unresolved),
        key=lambda p: _PATH_SEVERITY.get(p, 0),
    )
    return _PATH_TO_STATE[worst_path]
