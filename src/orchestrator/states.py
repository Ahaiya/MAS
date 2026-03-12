"""
Pipeline State Definitions

Defines the PipelineState enum, the legal transition matrix (TRANSITIONS),
and the set of terminal states.

Design invariants:
- States correspond 1:1 to the state machine nodes in architecture.md.
- TRANSITIONS encodes ONLY legal state-to-state edges. Attempting any
  other transition is an invariant violation.
- No business rules (trait names, adjudication thresholds, etc.) appear here.
  Routing decisions are made by router.py based on contract field values.
- Terminal states (VALIDATED, FAILED, HUMAN_REVIEW) have no outgoing edges.
- FAILED can be reached from any state via force_fail() on the StateGraph,
  but is NOT in TRANSITIONS — it's set directly by the orchestrator on
  unrecoverable errors.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet, Set


class PipelineState(str, Enum):
    """All possible states in the evaluation pipeline state machine."""

    # Start
    INIT = "init"

    # Normal forward path
    CONFIG_RESOLVED = "config_resolved"
    PREPROCESSED = "preprocessed"
    COVERAGE_PLANNED = "coverage_planned"
    EVIDENCE_EXTRACTED = "evidence_extracted"
    OBSERVATION_BUILT = "observation_built"
    SCORED = "scored"
    CONSISTENCY_CHECKED = "consistency_checked"
    ADJUDICATED = "adjudicated"
    FEEDBACK_RENDERED = "feedback_rendered"

    # Fallback / re-entry
    RE_EXTRACT = "re_extract"
    RE_SCORE = "re_score"

    # Terminal states
    VALIDATED = "validated"
    FAILED = "failed"
    HUMAN_REVIEW = "human_review"


# ── Legal transition matrix ────────────────────────────────────────────────────

TRANSITIONS: Dict[PipelineState, Set[PipelineState]] = {
    # Normal forward path
    PipelineState.INIT: {
        PipelineState.CONFIG_RESOLVED,
    },
    PipelineState.CONFIG_RESOLVED: {
        PipelineState.PREPROCESSED,
    },
    PipelineState.PREPROCESSED: {
        PipelineState.COVERAGE_PLANNED,
    },
    PipelineState.COVERAGE_PLANNED: {
        PipelineState.EVIDENCE_EXTRACTED,
    },
    PipelineState.EVIDENCE_EXTRACTED: {
        PipelineState.OBSERVATION_BUILT,
    },
    PipelineState.OBSERVATION_BUILT: {
        PipelineState.SCORED,
    },
    PipelineState.SCORED: {
        PipelineState.CONSISTENCY_CHECKED,
    },

    # Branching after consistency check:
    #   - no conflict      -> FEEDBACK_RENDERED (skip adjudication)
    #   - conflict exists   -> ADJUDICATED
    #   - coverage gap      -> RE_EXTRACT
    #   - scorer drift      -> RE_SCORE
    #   - unresolvable      -> HUMAN_REVIEW
    PipelineState.CONSISTENCY_CHECKED: {
        PipelineState.ADJUDICATED,
        PipelineState.FEEDBACK_RENDERED,
        PipelineState.RE_EXTRACT,
        PipelineState.RE_SCORE,
        PipelineState.HUMAN_REVIEW,
    },

    # Branching after adjudication:
    #   - all resolved      -> FEEDBACK_RENDERED
    #   - coverage gap      -> RE_EXTRACT
    #   - scorer drift      -> RE_SCORE
    #   - unresolvable      -> HUMAN_REVIEW
    PipelineState.ADJUDICATED: {
        PipelineState.FEEDBACK_RENDERED,
        PipelineState.RE_EXTRACT,
        PipelineState.RE_SCORE,
        PipelineState.HUMAN_REVIEW,
    },

    # Fallback loops — re-enter the pipeline at a prior stage
    PipelineState.RE_EXTRACT: {
        PipelineState.COVERAGE_PLANNED,
    },
    PipelineState.RE_SCORE: {
        PipelineState.OBSERVATION_BUILT,
    },

    # Terminal forward
    PipelineState.FEEDBACK_RENDERED: {
        PipelineState.VALIDATED,
    },

    # Terminal states — no outgoing transitions
    # (FAILED is reached via force_fail(), not via TRANSITIONS)
}

# ── Terminal state set ─────────────────────────────────────────────────────────

TERMINAL_STATES: FrozenSet[PipelineState] = frozenset({
    PipelineState.VALIDATED,
    PipelineState.FAILED,
    PipelineState.HUMAN_REVIEW,
})
