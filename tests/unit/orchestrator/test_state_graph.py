"""
Tests for state machine and node routing skeleton (Phase 3, Task 1).

Covers:
- PipelineState enum: all required states exist and are string-valued
- Terminal states: VALIDATED, FAILED, HUMAN_REVIEW identified correctly
- TRANSITIONS: the legal transition matrix is complete and correct
  - Normal (happy) path: INIT -> ... -> VALIDATED
  - Adjudication path: CONSISTENCY_CHECKED -> ADJUDICATED -> FEEDBACK_RENDERED
  - Skip-adjudication path: CONSISTENCY_CHECKED -> FEEDBACK_RENDERED (no conflict)
  - Fallback paths: RE_EXTRACT -> COVERAGE_PLANNED, RE_SCORE -> OBSERVATION_BUILT
  - Terminal sinks: VALIDATED, FAILED, HUMAN_REVIEW have no outgoing transitions
- StateGraph: state/transition registration, next_state lookup, illegal transition rejection
- Router: routing decisions based on contract fields (not hardcoded business rules)
  - route_after_consistency_check: dispatches based on ConflictRecord presence + paths
  - route_after_adjudication: dispatches based on AdjudicationRecord.is_resolved + paths

Zero-hardcoding: no trait names, dimension codes, or adjudication thresholds.
Routing reads contract fields (ConflictRecord.recommended_path, AdjudicationRecord.is_resolved)
and maps them to state transitions.
"""

import pytest

from src.orchestrator.states import PipelineState, TRANSITIONS, TERMINAL_STATES
from src.orchestrator.graph import StateGraph, IllegalTransitionError
from src.orchestrator.router import (
    route_after_consistency_check,
    route_after_adjudication,
)
from src.contracts.scoring import (
    ConflictRecord,
    ConflictType,
    ResolutionPath,
    AdjudicationRecord,
)
from src.contracts.score_representation import create_score_representation


# ── PipelineState enum ─────────────────────────────────────────────────────────


class TestPipelineState:
    def test_all_required_states_exist(self):
        required = [
            "INIT", "CONFIG_RESOLVED", "PREPROCESSED", "COVERAGE_PLANNED",
            "EVIDENCE_EXTRACTED", "OBSERVATION_BUILT", "SCORED",
            "CONSISTENCY_CHECKED", "ADJUDICATED", "RE_EXTRACT", "RE_SCORE",
            "FEEDBACK_RENDERED", "VALIDATED", "FAILED", "HUMAN_REVIEW",
        ]
        for name in required:
            assert hasattr(PipelineState, name), f"Missing state: {name}"

    def test_states_are_string_valued(self):
        for state in PipelineState:
            assert isinstance(state.value, str)

    def test_terminal_states(self):
        assert PipelineState.VALIDATED in TERMINAL_STATES
        assert PipelineState.FAILED in TERMINAL_STATES
        assert PipelineState.HUMAN_REVIEW in TERMINAL_STATES
        # Non-terminal states must NOT be in TERMINAL_STATES
        assert PipelineState.SCORED not in TERMINAL_STATES
        assert PipelineState.INIT not in TERMINAL_STATES


# ── TRANSITIONS (legal transition matrix) ──────────────────────────────────────


class TestTransitions:
    def test_normal_path_is_fully_connected(self):
        """The happy path from INIT to VALIDATED must be traversable."""
        normal_path = [
            PipelineState.INIT,
            PipelineState.CONFIG_RESOLVED,
            PipelineState.PREPROCESSED,
            PipelineState.COVERAGE_PLANNED,
            PipelineState.EVIDENCE_EXTRACTED,
            PipelineState.OBSERVATION_BUILT,
            PipelineState.SCORED,
            PipelineState.CONSISTENCY_CHECKED,
            PipelineState.FEEDBACK_RENDERED,  # skip adjudication (no conflict)
            PipelineState.VALIDATED,
        ]
        for i in range(len(normal_path) - 1):
            src, dst = normal_path[i], normal_path[i + 1]
            assert dst in TRANSITIONS.get(src, set()), \
                f"Missing transition: {src.value} -> {dst.value}"

    def test_adjudication_path(self):
        """CONSISTENCY_CHECKED -> ADJUDICATED -> FEEDBACK_RENDERED."""
        assert PipelineState.ADJUDICATED in TRANSITIONS[PipelineState.CONSISTENCY_CHECKED]
        assert PipelineState.FEEDBACK_RENDERED in TRANSITIONS[PipelineState.ADJUDICATED]

    def test_re_extract_loop(self):
        """RE_EXTRACT must loop back to COVERAGE_PLANNED."""
        assert PipelineState.COVERAGE_PLANNED in TRANSITIONS[PipelineState.RE_EXTRACT]

    def test_re_score_loop(self):
        """RE_SCORE must loop back to OBSERVATION_BUILT."""
        assert PipelineState.OBSERVATION_BUILT in TRANSITIONS[PipelineState.RE_SCORE]

    def test_fallback_entry_from_consistency_checked(self):
        allowed = TRANSITIONS[PipelineState.CONSISTENCY_CHECKED]
        assert PipelineState.RE_EXTRACT in allowed
        assert PipelineState.RE_SCORE in allowed
        assert PipelineState.HUMAN_REVIEW in allowed

    def test_fallback_entry_from_adjudicated(self):
        allowed = TRANSITIONS[PipelineState.ADJUDICATED]
        assert PipelineState.RE_EXTRACT in allowed
        assert PipelineState.RE_SCORE in allowed
        assert PipelineState.HUMAN_REVIEW in allowed

    def test_terminal_states_have_no_outgoing(self):
        for ts in TERMINAL_STATES:
            assert ts not in TRANSITIONS or len(TRANSITIONS[ts]) == 0, \
                f"Terminal state {ts.value} must have no outgoing transitions"

    def test_any_state_can_reach_failed(self):
        """FAILED is reachable via the graph's error path (set by orchestrator).
        We verify that FAILED exists as a state and is terminal, but it's set
        explicitly by the orchestrator on unrecoverable errors, not via TRANSITIONS."""
        assert PipelineState.FAILED in TERMINAL_STATES


# ── StateGraph ─────────────────────────────────────────────────────────────────


class TestStateGraph:
    @pytest.fixture
    def graph(self):
        return StateGraph()

    def test_initial_state_is_init(self, graph):
        assert graph.current_state == PipelineState.INIT

    def test_advance_normal_step(self, graph):
        graph.advance(PipelineState.CONFIG_RESOLVED)
        assert graph.current_state == PipelineState.CONFIG_RESOLVED

    def test_advance_full_normal_path(self, graph):
        path = [
            PipelineState.CONFIG_RESOLVED,
            PipelineState.PREPROCESSED,
            PipelineState.COVERAGE_PLANNED,
            PipelineState.EVIDENCE_EXTRACTED,
            PipelineState.OBSERVATION_BUILT,
            PipelineState.SCORED,
            PipelineState.CONSISTENCY_CHECKED,
            PipelineState.FEEDBACK_RENDERED,
            PipelineState.VALIDATED,
        ]
        for state in path:
            graph.advance(state)
        assert graph.current_state == PipelineState.VALIDATED

    def test_advance_adjudication_path(self, graph):
        """Normal path through adjudication to terminal."""
        path = [
            PipelineState.CONFIG_RESOLVED,
            PipelineState.PREPROCESSED,
            PipelineState.COVERAGE_PLANNED,
            PipelineState.EVIDENCE_EXTRACTED,
            PipelineState.OBSERVATION_BUILT,
            PipelineState.SCORED,
            PipelineState.CONSISTENCY_CHECKED,
            PipelineState.ADJUDICATED,
            PipelineState.FEEDBACK_RENDERED,
            PipelineState.VALIDATED,
        ]
        for state in path:
            graph.advance(state)
        assert graph.current_state == PipelineState.VALIDATED

    def test_advance_re_extract_loop(self, graph):
        """After CONSISTENCY_CHECKED, route to RE_EXTRACT and loop back."""
        for s in [
            PipelineState.CONFIG_RESOLVED,
            PipelineState.PREPROCESSED,
            PipelineState.COVERAGE_PLANNED,
            PipelineState.EVIDENCE_EXTRACTED,
            PipelineState.OBSERVATION_BUILT,
            PipelineState.SCORED,
            PipelineState.CONSISTENCY_CHECKED,
            PipelineState.RE_EXTRACT,
            PipelineState.COVERAGE_PLANNED,  # loop back
        ]:
            graph.advance(s)
        assert graph.current_state == PipelineState.COVERAGE_PLANNED

    def test_advance_re_score_loop(self, graph):
        """After CONSISTENCY_CHECKED, route to RE_SCORE and loop back."""
        for s in [
            PipelineState.CONFIG_RESOLVED,
            PipelineState.PREPROCESSED,
            PipelineState.COVERAGE_PLANNED,
            PipelineState.EVIDENCE_EXTRACTED,
            PipelineState.OBSERVATION_BUILT,
            PipelineState.SCORED,
            PipelineState.CONSISTENCY_CHECKED,
            PipelineState.RE_SCORE,
            PipelineState.OBSERVATION_BUILT,  # loop back
        ]:
            graph.advance(s)
        assert graph.current_state == PipelineState.OBSERVATION_BUILT

    def test_illegal_transition_raises(self, graph):
        """Cannot jump from INIT to SCORED."""
        with pytest.raises(IllegalTransitionError):
            graph.advance(PipelineState.SCORED)

    def test_illegal_transition_from_terminal(self, graph):
        """Cannot advance past a terminal state."""
        for s in [
            PipelineState.CONFIG_RESOLVED,
            PipelineState.PREPROCESSED,
            PipelineState.COVERAGE_PLANNED,
            PipelineState.EVIDENCE_EXTRACTED,
            PipelineState.OBSERVATION_BUILT,
            PipelineState.SCORED,
            PipelineState.CONSISTENCY_CHECKED,
            PipelineState.FEEDBACK_RENDERED,
            PipelineState.VALIDATED,
        ]:
            graph.advance(s)
        with pytest.raises(IllegalTransitionError):
            graph.advance(PipelineState.INIT)

    def test_force_fail_from_any_state(self, graph):
        """force_fail() should set state to FAILED from any state."""
        graph.advance(PipelineState.CONFIG_RESOLVED)
        graph.force_fail()
        assert graph.current_state == PipelineState.FAILED

    def test_is_terminal(self, graph):
        assert not graph.is_terminal()
        for s in [
            PipelineState.CONFIG_RESOLVED,
            PipelineState.PREPROCESSED,
            PipelineState.COVERAGE_PLANNED,
            PipelineState.EVIDENCE_EXTRACTED,
            PipelineState.OBSERVATION_BUILT,
            PipelineState.SCORED,
            PipelineState.CONSISTENCY_CHECKED,
            PipelineState.FEEDBACK_RENDERED,
            PipelineState.VALIDATED,
        ]:
            graph.advance(s)
        assert graph.is_terminal()

    def test_history_records_transitions(self, graph):
        graph.advance(PipelineState.CONFIG_RESOLVED)
        graph.advance(PipelineState.PREPROCESSED)
        assert len(graph.history) == 2
        assert graph.history[0] == (PipelineState.INIT, PipelineState.CONFIG_RESOLVED)
        assert graph.history[1] == (PipelineState.CONFIG_RESOLVED, PipelineState.PREPROCESSED)

    def test_available_transitions_from_init(self, graph):
        avail = graph.available_transitions()
        assert PipelineState.CONFIG_RESOLVED in avail
        assert PipelineState.SCORED not in avail


# ── Router functions ───────────────────────────────────────────────────────────


class TestRouteAfterConsistencyCheck:
    def test_no_conflicts_returns_feedback_rendered(self):
        result = route_after_consistency_check(conflicts=[])
        assert result == PipelineState.FEEDBACK_RENDERED

    def test_conflict_with_third_rater_returns_adjudicated(self):
        cr = ConflictRecord(
            conflict_id="c1",
            dimension_id="dim_any",
            hypothesis_ids=["h1", "h2"],
            conflict_type=ConflictType.NON_ADJACENT,
            trigger_rule_id="rule_any",
            conflict_detail="test",
            recommended_path=ResolutionPath.THIRD_RATER,
        )
        result = route_after_consistency_check(conflicts=[cr])
        assert result == PipelineState.ADJUDICATED

    def test_conflict_with_re_extract_returns_re_extract(self):
        cr = ConflictRecord(
            conflict_id="c1",
            dimension_id="dim_any",
            hypothesis_ids=["h1", "h2"],
            conflict_type=ConflictType.OTHER,
            trigger_rule_id="rule_any",
            conflict_detail="coverage gap",
            recommended_path=ResolutionPath.RE_EXTRACT,
        )
        result = route_after_consistency_check(conflicts=[cr])
        assert result == PipelineState.RE_EXTRACT

    def test_conflict_with_re_score_returns_re_score(self):
        cr = ConflictRecord(
            conflict_id="c1",
            dimension_id="dim_any",
            hypothesis_ids=["h1", "h2"],
            conflict_type=ConflictType.OTHER,
            trigger_rule_id="rule_any",
            conflict_detail="scorer drift",
            recommended_path=ResolutionPath.RE_SCORE,
        )
        result = route_after_consistency_check(conflicts=[cr])
        assert result == PipelineState.RE_SCORE

    def test_conflict_with_human_review_returns_human_review(self):
        cr = ConflictRecord(
            conflict_id="c1",
            dimension_id="dim_any",
            hypothesis_ids=["h1", "h2"],
            conflict_type=ConflictType.NON_ADJACENT,
            trigger_rule_id="rule_any",
            conflict_detail="unresolvable",
            recommended_path=ResolutionPath.HUMAN_REVIEW,
        )
        result = route_after_consistency_check(conflicts=[cr])
        assert result == PipelineState.HUMAN_REVIEW

    def test_mixed_conflicts_highest_severity_wins(self):
        """If multiple conflicts exist, the most severe recommended_path wins.
        Severity order: HUMAN_REVIEW > RE_EXTRACT > RE_SCORE > THIRD_RATER/POLICY_AVERAGE."""
        cr_adj = ConflictRecord(
            conflict_id="c1",
            dimension_id="dim_a",
            hypothesis_ids=["h1", "h2"],
            conflict_type=ConflictType.NON_ADJACENT,
            trigger_rule_id="r1",
            conflict_detail="test",
            recommended_path=ResolutionPath.THIRD_RATER,
        )
        cr_human = ConflictRecord(
            conflict_id="c2",
            dimension_id="dim_b",
            hypothesis_ids=["h3", "h4"],
            conflict_type=ConflictType.NON_ADJACENT,
            trigger_rule_id="r2",
            conflict_detail="test",
            recommended_path=ResolutionPath.HUMAN_REVIEW,
        )
        result = route_after_consistency_check(conflicts=[cr_adj, cr_human])
        assert result == PipelineState.HUMAN_REVIEW


class TestRouteAfterAdjudication:
    def test_all_resolved_returns_feedback_rendered(self):
        sr = create_score_representation(4, "ordinal_any")
        rec = AdjudicationRecord(
            adjudication_id="adj-1",
            conflict_id="c1",
            dimension_id="dim_any",
            resolution_path=ResolutionPath.THIRD_RATER,
            policy_rule_id="rule_any",
            resolved_score=sr,
            resolver_hypothesis_id="h3",
            resolution_note="resolved",
            is_resolved=True,
        )
        result = route_after_adjudication(records=[rec])
        assert result == PipelineState.FEEDBACK_RENDERED

    def test_unresolved_with_re_extract_returns_re_extract(self):
        rec = AdjudicationRecord(
            adjudication_id="adj-1",
            conflict_id="c1",
            dimension_id="dim_any",
            resolution_path=ResolutionPath.RE_EXTRACT,
            policy_rule_id="rule_any",
            resolved_score=None,
            resolver_hypothesis_id=None,
            resolution_note="coverage gap",
            is_resolved=False,
        )
        result = route_after_adjudication(records=[rec])
        assert result == PipelineState.RE_EXTRACT

    def test_unresolved_with_re_score_returns_re_score(self):
        rec = AdjudicationRecord(
            adjudication_id="adj-1",
            conflict_id="c1",
            dimension_id="dim_any",
            resolution_path=ResolutionPath.RE_SCORE,
            policy_rule_id="rule_any",
            resolved_score=None,
            resolver_hypothesis_id=None,
            resolution_note="scorer drift",
            is_resolved=False,
        )
        result = route_after_adjudication(records=[rec])
        assert result == PipelineState.RE_SCORE

    def test_unresolved_with_human_review_returns_human_review(self):
        rec = AdjudicationRecord(
            adjudication_id="adj-1",
            conflict_id="c1",
            dimension_id="dim_any",
            resolution_path=ResolutionPath.HUMAN_REVIEW,
            policy_rule_id="rule_any",
            resolved_score=None,
            resolver_hypothesis_id=None,
            resolution_note="unresolvable",
            is_resolved=False,
        )
        result = route_after_adjudication(records=[rec])
        assert result == PipelineState.HUMAN_REVIEW

    def test_mixed_resolved_and_unresolved(self):
        """If any record is unresolved, the worst-case path wins."""
        sr = create_score_representation(5, "ordinal_any")
        resolved = AdjudicationRecord(
            adjudication_id="adj-1", conflict_id="c1", dimension_id="dim_a",
            resolution_path=ResolutionPath.THIRD_RATER, policy_rule_id="r1",
            resolved_score=sr, resolver_hypothesis_id="h3",
            resolution_note="ok", is_resolved=True,
        )
        unresolved = AdjudicationRecord(
            adjudication_id="adj-2", conflict_id="c2", dimension_id="dim_b",
            resolution_path=ResolutionPath.RE_EXTRACT, policy_rule_id="r2",
            resolved_score=None, resolver_hypothesis_id=None,
            resolution_note="gap", is_resolved=False,
        )
        result = route_after_adjudication(records=[resolved, unresolved])
        assert result == PipelineState.RE_EXTRACT
