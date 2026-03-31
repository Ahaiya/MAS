"""
Tests for Adjudication Trigger Evaluation (Phase 4, Task 2).

Verifies that:
1. score_distance triggers fire when |score1 - score2| exceeds threshold.
2. pattern_match (cusp) triggers fire when cross-dimension score patterns match.
3. adjacent_drift triggers fire when same-direction adjacent disagreements accumulate.
4. Dimension filtering (applies_to, exclusions) works correctly.
5. All thresholds, patterns, and dimension lists come from config.
6. reconciliation.run() delegates to evaluate_all_triggers().

Zero-hardcoding: all fixtures use synthetic dimension IDs, rater labels,
and score values — no real trait names or ASAP-specific data.
"""

import pytest

from src.contracts.artifact_bundle import PolicySnapshot
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import (
    ConflictRecord,
    ConflictType,
    ResolutionPath,
    ScoreHypothesis,
)
from src.policies.adjudication import (
    evaluate_adjacent_drift_trigger,
    evaluate_all_triggers,
    evaluate_pattern_match_trigger,
    evaluate_score_distance_trigger,
)
from src.agents import reconciliation


# ── Helpers ───────────────────────────────────────────────────────────────────

SCALE = "test_scale"


def _hyp(dim_id: str, rater_id: str, score: int) -> ScoreHypothesis:
    """Build a minimal ScoreHypothesis fixture."""
    return ScoreHypothesis(
        hypothesis_id=f"hyp-{dim_id}-{rater_id}",
        observation_id=f"obs-{dim_id}",
        dimension_id=dim_id,
        rater_id=rater_id,
        score=create_score_representation(score, SCALE),
        descriptor_refs=[f"level_{score}"],
        evidence_span_ids=[],
        rationale="fixture",
        confidence=0.8,
    )


def _score_distance_trigger(
    trigger_id: str = "t_distance",
    operator: str = ">",
    value: int = 1,
    applies_to: list | None = None,
    exclusions: list | None = None,
    action: str = "invoke_resolution",
    priority: int = 1,
) -> dict:
    return {
        "trigger_id": trigger_id,
        "type": "score_distance",
        "applies_to_dimensions": applies_to or ["*"],
        "threshold": {"operator": operator, "value": value},
        "exclusions": exclusions or [],
        "action": action,
        "priority": priority,
    }


def _pattern_trigger(
    trigger_id: str = "t_cusp",
    applies_to: list | None = None,
    expected_scores: list | None = None,
    one_minus_one: bool = True,
    exclusions: list | None = None,
    action: str = "invoke_resolution",
    priority: int = 2,
) -> dict:
    return {
        "trigger_id": trigger_id,
        "type": "pattern_match",
        "applies_to_dimensions": applies_to or [],
        "pattern": {
            "one_rater_all_scores": expected_scores or [],
            "other_rater_has_one_3_and_three_4s": one_minus_one,
        },
        "exclusions": exclusions or [],
        "action": action,
        "priority": priority,
    }


def _adjacent_drift_trigger(
    trigger_id: str = "t_adjacent_drift",
    applies_to: list | None = None,
    score_gap: int = 1,
    min_matching_dimensions: int = 3,
    require_same_direction: bool = True,
    exclusions: list | None = None,
    action: str = "invoke_resolution",
    priority: int = 3,
) -> dict:
    return {
        "trigger_id": trigger_id,
        "type": "adjacent_drift",
        "applies_to_dimensions": applies_to or ["*"],
        "pattern": {
            "score_gap": score_gap,
            "min_matching_dimensions": min_matching_dimensions,
            "require_same_direction": require_same_direction,
        },
        "exclusions": exclusions or [],
        "action": action,
        "priority": priority,
    }


def _policy(*triggers: dict) -> PolicySnapshot:
    return PolicySnapshot(
        adjudication_policy={
            "policy_id": "test_adj",
            "raters": {
                "required_independent_scores": 2,
                "rater_labels": ["rater_1", "rater_2"],
                "resolution_rater_label": "rater_3",
            },
            "triggers": list(triggers),
            "resolution_strategy": {"default": "use_resolution_rater_as_authoritative"},
        },
        aggregation_policy={},
        explanation_policy={},
        policy_version="v1",
    )


# ── Score Distance Trigger Tests ──────────────────────────────────────────────


class TestScoreDistanceTrigger:
    def test_no_conflict_when_within_threshold(self):
        trigger = _score_distance_trigger(operator=">", value=1)
        by_dim = {
            "d1": [_hyp("d1", "r1", 3), _hyp("d1", "r2", 4)],
        }
        conflicts = evaluate_score_distance_trigger(trigger, by_dim)
        assert conflicts == []

    def test_no_conflict_when_equal(self):
        trigger = _score_distance_trigger(operator=">", value=1)
        by_dim = {
            "d1": [_hyp("d1", "r1", 3), _hyp("d1", "r2", 3)],
        }
        conflicts = evaluate_score_distance_trigger(trigger, by_dim)
        assert conflicts == []

    def test_conflict_when_exceeds_threshold(self):
        trigger = _score_distance_trigger(operator=">", value=1)
        by_dim = {
            "d1": [_hyp("d1", "r1", 1), _hyp("d1", "r2", 4)],
        }
        conflicts = evaluate_score_distance_trigger(trigger, by_dim)
        assert len(conflicts) == 1
        assert conflicts[0].dimension_id == "d1"
        assert conflicts[0].conflict_type == ConflictType.NON_ADJACENT

    def test_conflict_record_has_both_hypothesis_ids(self):
        trigger = _score_distance_trigger(operator=">", value=1)
        h1 = _hyp("d1", "r1", 1)
        h2 = _hyp("d1", "r2", 4)
        by_dim = {"d1": [h1, h2]}
        conflicts = evaluate_score_distance_trigger(trigger, by_dim)
        assert h1.hypothesis_id in conflicts[0].hypothesis_ids
        assert h2.hypothesis_id in conflicts[0].hypothesis_ids

    def test_conflict_record_has_trigger_rule_id(self):
        trigger = _score_distance_trigger(trigger_id="my_rule")
        by_dim = {"d1": [_hyp("d1", "r1", 1), _hyp("d1", "r2", 4)]}
        conflicts = evaluate_score_distance_trigger(trigger, by_dim)
        assert conflicts[0].trigger_rule_id == "my_rule"

    def test_uses_resolution_path_from_action(self):
        trigger = _score_distance_trigger(action="human_review")
        by_dim = {"d1": [_hyp("d1", "r1", 1), _hyp("d1", "r2", 4)]}
        conflicts = evaluate_score_distance_trigger(trigger, by_dim)
        assert conflicts[0].recommended_path == ResolutionPath.HUMAN_REVIEW

    def test_threshold_gte_operator(self):
        trigger = _score_distance_trigger(operator=">=", value=2)
        by_dim = {
            "d1": [_hyp("d1", "r1", 1), _hyp("d1", "r2", 3)],
        }
        conflicts = evaluate_score_distance_trigger(trigger, by_dim)
        assert len(conflicts) == 1

    def test_different_threshold_from_config(self):
        trigger = _score_distance_trigger(operator=">", value=3)
        # diff=2 is NOT > 3
        by_dim = {"d1": [_hyp("d1", "r1", 1), _hyp("d1", "r2", 3)]}
        conflicts = evaluate_score_distance_trigger(trigger, by_dim)
        assert conflicts == []
        # diff=4 IS > 3
        by_dim2 = {"d1": [_hyp("d1", "r1", 1), _hyp("d1", "r2", 5)]}
        conflicts2 = evaluate_score_distance_trigger(trigger, by_dim2)
        assert len(conflicts2) == 1

    def test_independent_per_dimension(self):
        trigger = _score_distance_trigger(operator=">", value=1)
        by_dim = {
            "d1": [_hyp("d1", "r1", 1), _hyp("d1", "r2", 4)],
            "d2": [_hyp("d2", "r1", 3), _hyp("d2", "r2", 3)],
        }
        conflicts = evaluate_score_distance_trigger(trigger, by_dim)
        assert len(conflicts) == 1
        assert conflicts[0].dimension_id == "d1"

    def test_wildcard_applies_to_all_dimensions(self):
        trigger = _score_distance_trigger(applies_to=["*"])
        by_dim = {
            "d1": [_hyp("d1", "r1", 1), _hyp("d1", "r2", 4)],
            "d2": [_hyp("d2", "r1", 1), _hyp("d2", "r2", 4)],
        }
        conflicts = evaluate_score_distance_trigger(trigger, by_dim)
        dim_ids = {c.dimension_id for c in conflicts}
        assert dim_ids == {"d1", "d2"}

    def test_specific_dimension_filter(self):
        trigger = _score_distance_trigger(applies_to=["d1"])
        by_dim = {
            "d1": [_hyp("d1", "r1", 1), _hyp("d1", "r2", 4)],
            "d2": [_hyp("d2", "r1", 1), _hyp("d2", "r2", 4)],
        }
        conflicts = evaluate_score_distance_trigger(trigger, by_dim)
        assert len(conflicts) == 1
        assert conflicts[0].dimension_id == "d1"

    def test_exclusion_filter(self):
        trigger = _score_distance_trigger(applies_to=["*"], exclusions=["d2"])
        by_dim = {
            "d1": [_hyp("d1", "r1", 1), _hyp("d1", "r2", 4)],
            "d2": [_hyp("d2", "r1", 1), _hyp("d2", "r2", 4)],
        }
        conflicts = evaluate_score_distance_trigger(trigger, by_dim)
        assert len(conflicts) == 1
        assert conflicts[0].dimension_id == "d1"

    def test_deterministic(self):
        trigger = _score_distance_trigger()
        by_dim = {"d1": [_hyp("d1", "r1", 1), _hyp("d1", "r2", 4)]}
        c1 = evaluate_score_distance_trigger(trigger, by_dim)
        c2 = evaluate_score_distance_trigger(trigger, by_dim)
        assert c1[0].conflict_id == c2[0].conflict_id


# ── Pattern Match (Cusp) Trigger Tests ────────────────────────────────────────


class TestPatternMatchTrigger:
    def test_no_cusp_when_no_pattern_match(self):
        trigger = _pattern_trigger(
            applies_to=["d1", "d2", "d3"],
            expected_scores=[5, 5, 5],
        )
        hyps = [
            _hyp("d1", "r1", 3), _hyp("d1", "r2", 3),
            _hyp("d2", "r1", 3), _hyp("d2", "r2", 3),
            _hyp("d3", "r1", 3), _hyp("d3", "r2", 3),
        ]
        conflicts = evaluate_pattern_match_trigger(trigger, hyps)
        assert conflicts == []

    def test_cusp_fires_when_pattern_matches(self):
        # r1 has all 5s, r2 has one 4 and two 5s (cusp pattern)
        trigger = _pattern_trigger(
            applies_to=["d1", "d2", "d3"],
            expected_scores=[5, 5, 5],
        )
        hyps = [
            _hyp("d1", "r1", 5), _hyp("d1", "r2", 4),  # r2 is 1 less
            _hyp("d2", "r1", 5), _hyp("d2", "r2", 5),
            _hyp("d3", "r1", 5), _hyp("d3", "r2", 5),
        ]
        conflicts = evaluate_pattern_match_trigger(trigger, hyps)
        assert len(conflicts) == 3  # one per applicable dimension
        for c in conflicts:
            assert c.conflict_type == ConflictType.CUSP

    def test_cusp_fires_with_inverted_raters(self):
        # r2 has all expected, r1 has one-minus-one
        trigger = _pattern_trigger(
            applies_to=["d1", "d2"],
            expected_scores=[7, 7],
        )
        hyps = [
            _hyp("d1", "r1", 6), _hyp("d1", "r2", 7),  # r1 is 1 less
            _hyp("d2", "r1", 7), _hyp("d2", "r2", 7),
        ]
        conflicts = evaluate_pattern_match_trigger(trigger, hyps)
        assert len(conflicts) == 2

    def test_no_cusp_when_two_scores_differ(self):
        # r2 has TWO scores that differ — not a cusp
        trigger = _pattern_trigger(
            applies_to=["d1", "d2", "d3"],
            expected_scores=[5, 5, 5],
        )
        hyps = [
            _hyp("d1", "r1", 5), _hyp("d1", "r2", 4),
            _hyp("d2", "r1", 5), _hyp("d2", "r2", 4),  # two diffs
            _hyp("d3", "r1", 5), _hyp("d3", "r2", 5),
        ]
        conflicts = evaluate_pattern_match_trigger(trigger, hyps)
        assert conflicts == []

    def test_no_cusp_when_neither_matches_expected(self):
        trigger = _pattern_trigger(
            applies_to=["d1", "d2"],
            expected_scores=[5, 5],
        )
        hyps = [
            _hyp("d1", "r1", 3), _hyp("d1", "r2", 4),
            _hyp("d2", "r1", 3), _hyp("d2", "r2", 4),
        ]
        conflicts = evaluate_pattern_match_trigger(trigger, hyps)
        assert conflicts == []

    def test_cusp_conflict_has_trigger_rule_id(self):
        trigger = _pattern_trigger(
            trigger_id="my_cusp",
            applies_to=["d1", "d2"],
            expected_scores=[5, 5],
        )
        hyps = [
            _hyp("d1", "r1", 5), _hyp("d1", "r2", 4),
            _hyp("d2", "r1", 5), _hyp("d2", "r2", 5),
        ]
        conflicts = evaluate_pattern_match_trigger(trigger, hyps)
        assert all(c.trigger_rule_id == "my_cusp" for c in conflicts)

    def test_cusp_uses_resolution_path_from_action(self):
        trigger = _pattern_trigger(
            applies_to=["d1", "d2"],
            expected_scores=[5, 5],
            action="re_score",
        )
        hyps = [
            _hyp("d1", "r1", 5), _hyp("d1", "r2", 4),
            _hyp("d2", "r1", 5), _hyp("d2", "r2", 5),
        ]
        conflicts = evaluate_pattern_match_trigger(trigger, hyps)
        assert all(c.recommended_path == ResolutionPath.RE_SCORE for c in conflicts)

    def test_cusp_exclusions(self):
        # d3 is excluded from the cusp check
        trigger = _pattern_trigger(
            applies_to=["d1", "d2", "d3"],
            expected_scores=[5, 5, 5],
            exclusions=["d3"],
        )
        # After exclusion, only d1 and d2 are applicable (2 dims),
        # but expected_scores has 3 values -> mismatch -> no conflicts
        hyps = [
            _hyp("d1", "r1", 5), _hyp("d1", "r2", 4),
            _hyp("d2", "r1", 5), _hyp("d2", "r2", 5),
            _hyp("d3", "r1", 5), _hyp("d3", "r2", 5),
        ]
        conflicts = evaluate_pattern_match_trigger(trigger, hyps)
        assert conflicts == []

    def test_cusp_different_expected_scores(self):
        # Config says expected is [3, 3] — cusp at 2-3 boundary
        trigger = _pattern_trigger(
            applies_to=["d1", "d2"],
            expected_scores=[3, 3],
        )
        hyps = [
            _hyp("d1", "r1", 3), _hyp("d1", "r2", 2),  # one diff
            _hyp("d2", "r1", 3), _hyp("d2", "r2", 3),
        ]
        conflicts = evaluate_pattern_match_trigger(trigger, hyps)
        assert len(conflicts) == 2

    def test_no_cusp_when_disabled(self):
        trigger = _pattern_trigger(
            applies_to=["d1", "d2"],
            expected_scores=[5, 5],
            one_minus_one=False,
        )
        hyps = [
            _hyp("d1", "r1", 5), _hyp("d1", "r2", 4),
            _hyp("d2", "r1", 5), _hyp("d2", "r2", 5),
        ]
        conflicts = evaluate_pattern_match_trigger(trigger, hyps)
        assert conflicts == []

    def test_incomplete_hypotheses_skip_cusp(self):
        # Only r1 has hypotheses for d2 — cusp check is skipped
        trigger = _pattern_trigger(
            applies_to=["d1", "d2"],
            expected_scores=[5, 5],
        )
        hyps = [
            _hyp("d1", "r1", 5), _hyp("d1", "r2", 4),
            _hyp("d2", "r1", 5),  # no r2 for d2
        ]
        conflicts = evaluate_pattern_match_trigger(trigger, hyps)
        assert conflicts == []

    def test_deterministic(self):
        trigger = _pattern_trigger(
            applies_to=["d1", "d2"],
            expected_scores=[5, 5],
        )
        hyps = [
            _hyp("d1", "r1", 5), _hyp("d1", "r2", 4),
            _hyp("d2", "r1", 5), _hyp("d2", "r2", 5),
        ]
        c1 = evaluate_pattern_match_trigger(trigger, hyps)
        c2 = evaluate_pattern_match_trigger(trigger, hyps)
        ids1 = sorted(c.conflict_id for c in c1)
        ids2 = sorted(c.conflict_id for c in c2)
        assert ids1 == ids2


# ── Adjacent Drift Trigger Tests ──────────────────────────────────────────────


class TestAdjacentDriftTrigger:
    def test_no_conflict_when_adjacent_count_below_threshold(self):
        trigger = _adjacent_drift_trigger(min_matching_dimensions=3)
        hyps = [
            _hyp("d1", "r1", 4), _hyp("d1", "r2", 5),
            _hyp("d2", "r1", 2), _hyp("d2", "r2", 3),
        ]
        conflicts = evaluate_adjacent_drift_trigger(trigger, hyps)
        assert conflicts == []

    def test_conflict_when_three_adjacent_diffs_share_direction(self):
        trigger = _adjacent_drift_trigger(min_matching_dimensions=3)
        hyps = [
            _hyp("d1", "r1", 4), _hyp("d1", "r2", 5),
            _hyp("d2", "r1", 4), _hyp("d2", "r2", 5),
            _hyp("d3", "r1", 2), _hyp("d3", "r2", 3),
        ]
        conflicts = evaluate_adjacent_drift_trigger(trigger, hyps)
        assert len(conflicts) == 3
        assert all(c.conflict_type == ConflictType.ADJACENT_DRIFT for c in conflicts)

    def test_no_conflict_when_direction_is_mixed_and_required(self):
        trigger = _adjacent_drift_trigger(min_matching_dimensions=3)
        hyps = [
            _hyp("d1", "r1", 4), _hyp("d1", "r2", 5),
            _hyp("d2", "r1", 5), _hyp("d2", "r2", 4),
            _hyp("d3", "r1", 2), _hyp("d3", "r2", 3),
        ]
        conflicts = evaluate_adjacent_drift_trigger(trigger, hyps)
        assert conflicts == []

    def test_conflict_when_direction_is_mixed_but_not_required(self):
        trigger = _adjacent_drift_trigger(
            min_matching_dimensions=3,
            require_same_direction=False,
        )
        hyps = [
            _hyp("d1", "r1", 4), _hyp("d1", "r2", 5),
            _hyp("d2", "r1", 5), _hyp("d2", "r2", 4),
            _hyp("d3", "r1", 2), _hyp("d3", "r2", 3),
        ]
        conflicts = evaluate_adjacent_drift_trigger(trigger, hyps)
        assert len(conflicts) == 3

    def test_uses_resolution_path_from_action(self):
        trigger = _adjacent_drift_trigger(
            min_matching_dimensions=3,
            action="human_review",
        )
        hyps = [
            _hyp("d1", "r1", 4), _hyp("d1", "r2", 5),
            _hyp("d2", "r1", 4), _hyp("d2", "r2", 5),
            _hyp("d3", "r1", 2), _hyp("d3", "r2", 3),
        ]
        conflicts = evaluate_adjacent_drift_trigger(trigger, hyps)
        assert all(c.recommended_path == ResolutionPath.HUMAN_REVIEW for c in conflicts)

    def test_respects_dimension_filters(self):
        trigger = _adjacent_drift_trigger(
            applies_to=["d1", "d2", "d3"],
            exclusions=["d3"],
            min_matching_dimensions=2,
        )
        hyps = [
            _hyp("d1", "r1", 4), _hyp("d1", "r2", 5),
            _hyp("d2", "r1", 4), _hyp("d2", "r2", 5),
            _hyp("d3", "r1", 2), _hyp("d3", "r2", 3),
        ]
        conflicts = evaluate_adjacent_drift_trigger(trigger, hyps)
        assert len(conflicts) == 2
        assert {c.dimension_id for c in conflicts} == {"d1", "d2"}

    def test_deterministic(self):
        trigger = _adjacent_drift_trigger(min_matching_dimensions=3)
        hyps = [
            _hyp("d1", "r1", 4), _hyp("d1", "r2", 5),
            _hyp("d2", "r1", 4), _hyp("d2", "r2", 5),
            _hyp("d3", "r1", 2), _hyp("d3", "r2", 3),
        ]
        c1 = evaluate_adjacent_drift_trigger(trigger, hyps)
        c2 = evaluate_adjacent_drift_trigger(trigger, hyps)
        assert [c.conflict_id for c in c1] == [c.conflict_id for c in c2]


# ── Combined / evaluate_all_triggers Tests ────────────────────────────────────


class TestEvaluateAllTriggers:
    def test_empty_hypotheses(self):
        policy = _policy(_score_distance_trigger())
        conflicts = evaluate_all_triggers([], policy)
        assert conflicts == []

    def test_score_distance_only(self):
        policy = _policy(_score_distance_trigger(operator=">", value=1))
        hyps = [_hyp("d1", "r1", 1), _hyp("d1", "r2", 4)]
        conflicts = evaluate_all_triggers(hyps, policy)
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == ConflictType.NON_ADJACENT

    def test_pattern_match_only(self):
        policy = _policy(
            _pattern_trigger(
                applies_to=["d1", "d2"],
                expected_scores=[5, 5],
            )
        )
        hyps = [
            _hyp("d1", "r1", 5), _hyp("d1", "r2", 4),
            _hyp("d2", "r1", 5), _hyp("d2", "r2", 5),
        ]
        conflicts = evaluate_all_triggers(hyps, policy)
        assert len(conflicts) == 2
        assert all(c.conflict_type == ConflictType.CUSP for c in conflicts)

    def test_both_triggers_fire(self):
        policy = _policy(
            _score_distance_trigger(operator=">", value=1),
            _pattern_trigger(
                applies_to=["d1", "d2"],
                expected_scores=[5, 5],
            ),
        )
        hyps = [
            _hyp("d1", "r1", 5), _hyp("d1", "r2", 2),  # distance fires (diff=3)
            _hyp("d2", "r1", 5), _hyp("d2", "r2", 5),
        ]
        conflicts = evaluate_all_triggers(hyps, policy)
        types = {c.conflict_type for c in conflicts}
        # d1 has distance conflict; no cusp because r2 doesn't match cusp pattern
        assert ConflictType.NON_ADJACENT in types

    def test_deduplicates_by_conflict_id(self):
        policy = _policy(_score_distance_trigger())
        hyps = [_hyp("d1", "r1", 1), _hyp("d1", "r2", 4)]
        c1 = evaluate_all_triggers(hyps, policy)
        # Run again — same conflicts, same IDs
        c2 = evaluate_all_triggers(hyps, policy)
        assert len(c1) == len(c2)
        assert c1[0].conflict_id == c2[0].conflict_id

    def test_no_triggers_configured(self):
        policy = _policy()  # no triggers
        hyps = [_hyp("d1", "r1", 1), _hyp("d1", "r2", 4)]
        conflicts = evaluate_all_triggers(hyps, policy)
        assert conflicts == []

    def test_adjacent_drift_only(self):
        policy = _policy(_adjacent_drift_trigger(min_matching_dimensions=3))
        hyps = [
            _hyp("d1", "r1", 4), _hyp("d1", "r2", 5),
            _hyp("d2", "r1", 4), _hyp("d2", "r2", 5),
            _hyp("d3", "r1", 2), _hyp("d3", "r2", 3),
        ]
        conflicts = evaluate_all_triggers(hyps, policy)
        assert len(conflicts) == 3
        assert all(c.conflict_type == ConflictType.ADJACENT_DRIFT for c in conflicts)

    def test_higher_priority_trigger_wins_for_same_pair(self):
        policy = _policy(
            _adjacent_drift_trigger(
                trigger_id="adj_high_priority",
                min_matching_dimensions=3,
                priority=2,
            ),
            _adjacent_drift_trigger(
                trigger_id="adj_low_priority",
                min_matching_dimensions=3,
                priority=3,
            ),
        )
        hyps = [
            _hyp("d1", "r1", 4), _hyp("d1", "r2", 3),
            _hyp("d2", "r1", 4), _hyp("d2", "r2", 3),
            _hyp("d3", "r1", 4), _hyp("d3", "r2", 3),
        ]
        conflicts = evaluate_all_triggers(hyps, policy)
        assert len(conflicts) == 3
        assert all(c.conflict_type == ConflictType.ADJACENT_DRIFT for c in conflicts)
        assert all(c.trigger_rule_id == "adj_high_priority" for c in conflicts)


# ── Integration: reconciliation.run() ────────────────────────────────────────


class TestConsistencyCheckerRun:
    def test_delegates_to_evaluate_all_triggers(self):
        policy = _policy(_score_distance_trigger(operator=">", value=1))
        hyps = [_hyp("d1", "r1", 1), _hyp("d1", "r2", 4)]
        conflicts = reconciliation.run(hyps, policy).conflicts
        assert len(conflicts) == 1
        assert isinstance(conflicts[0], ConflictRecord)

    def test_no_conflicts(self):
        policy = _policy(_score_distance_trigger(operator=">", value=1))
        hyps = [_hyp("d1", "r1", 3), _hyp("d1", "r2", 3)]
        conflicts = reconciliation.run(hyps, policy).conflicts
        assert conflicts == []

    def test_handles_cusp_trigger(self):
        policy = _policy(
            _pattern_trigger(
                applies_to=["d1", "d2"],
                expected_scores=[5, 5],
            ),
        )
        hyps = [
            _hyp("d1", "r1", 5), _hyp("d1", "r2", 4),
            _hyp("d2", "r1", 5), _hyp("d2", "r2", 5),
        ]
        conflicts = reconciliation.run(hyps, policy).conflicts
        assert len(conflicts) == 2
        assert all(c.conflict_type == ConflictType.CUSP for c in conflicts)
