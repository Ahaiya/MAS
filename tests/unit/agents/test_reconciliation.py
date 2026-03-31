"""Unit tests for unified reconciliation agent (Stage L)."""

from src.agents import reconciliation
from src.contracts.artifact_bundle import PolicySnapshot
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import ResolutionPath, ScoreHypothesis


SCALE = "test_scale"


def _hyp(dim_id: str, rater_id: str, score: int) -> ScoreHypothesis:
    return ScoreHypothesis(
        hypothesis_id=f"hyp-{dim_id}-{rater_id}",
        observation_id=f"obs-{dim_id}",
        dimension_id=dim_id,
        rater_id=rater_id,
        score=create_score_representation(score, SCALE),
        descriptor_refs=[f"desc-{score}"],
        evidence_span_ids=[f"span-{dim_id}"],
        rationale="fixture",
        confidence=0.8,
    )


def _policy(
    *,
    default_strategy: str = "use_resolution_rater_as_authoritative",
    fallback_if_no_resolution: str = "average_of_raters",
    re_score_scope: str = "all_dimensions",
) -> PolicySnapshot:
    return PolicySnapshot(
        adjudication_policy={
            "policy_id": "test_adj",
            "raters": {
                "required_independent_scores": 2,
                "rater_labels": ["rater_1", "rater_2"],
                "optional_resolution_rater": 1,
                "resolution_rater_label": "rater_3",
            },
            "triggers": [
                {
                    "trigger_id": "non_adj",
                    "type": "score_distance",
                    "applies_to_dimensions": ["*"],
                    "description": "non adjacent",
                    "threshold": {"operator": ">", "value": 1},
                    "action": "invoke_resolution",
                    "priority": 1,
                }
            ],
            "resolution_strategy": {
                "default": default_strategy,
                "fallback_if_no_resolution": fallback_if_no_resolution,
                "re_score_scope": re_score_scope,
            },
        },
        aggregation_policy={},
        explanation_policy={},
        policy_version="test-v1",
    )


def test_no_conflict_produces_decisions():
    policy = _policy()
    hyps = [_hyp("d1", "rater_1", 3), _hyp("d1", "rater_2", 3)]

    result = reconciliation.run(hyps, policy)
    assert result.conflicts == []
    adj_records, decisions = reconciliation.resolve(result.conflicts, hyps, policy)

    assert adj_records == []
    assert len(decisions) == 1
    assert decisions[0].final_score.canonical_score == 3
    assert decisions[0].primary_hypothesis_id == "hyp-d1-rater_1"


def test_conflict_detected_needs_resolution():
    policy = _policy()
    hyps = [_hyp("d1", "rater_1", 1), _hyp("d1", "rater_2", 4)]

    result = reconciliation.run(hyps, policy)
    assert len(result.conflicts) == 1
    assert result.needs_resolution_scoring is True


def test_re_score_scope_all_dimensions():
    policy = _policy(re_score_scope="all_dimensions")
    hyps = [
        _hyp("d1", "rater_1", 1),
        _hyp("d1", "rater_2", 4),  # conflict
        _hyp("d2", "rater_1", 3),
        _hyp("d2", "rater_2", 3),  # converged
    ]

    result = reconciliation.run(hyps, policy)
    assert result.needs_resolution_scoring is True
    assert result.resolution_dimension_ids == ["d1", "d2"]


def test_re_score_scope_conflicted_only():
    policy = _policy(re_score_scope="conflicted_only")
    hyps = [
        _hyp("d1", "rater_1", 1),
        _hyp("d1", "rater_2", 4),  # conflict
        _hyp("d2", "rater_1", 3),
        _hyp("d2", "rater_2", 3),  # converged
    ]

    result = reconciliation.run(hyps, policy)
    assert result.needs_resolution_scoring is True
    assert result.resolution_dimension_ids == ["d1"]


def test_resolve_authoritative():
    policy = _policy(default_strategy="use_resolution_rater_as_authoritative")
    hyps = [
        _hyp("d1", "rater_1", 2),
        _hyp("d1", "rater_2", 5),
        _hyp("d1", "rater_3", 4),
    ]

    result = reconciliation.run(hyps, policy)
    adj_records, decisions = reconciliation.resolve(result.conflicts, hyps, policy)

    assert len(adj_records) == 1
    assert adj_records[0].resolution_path == ResolutionPath.THIRD_RATER
    assert adj_records[0].is_resolved is True
    assert decisions[0].final_score.canonical_score == 4


def test_resolve_average_of_raters():
    policy = _policy(default_strategy="average_of_raters")
    hyps = [_hyp("d1", "rater_1", 2), _hyp("d1", "rater_2", 5)]

    result = reconciliation.run(hyps, policy)
    adj_records, decisions = reconciliation.resolve(result.conflicts, hyps, policy)

    assert len(adj_records) == 1
    assert adj_records[0].resolution_path == ResolutionPath.POLICY_AVERAGE
    # Round-half-up(3.5) == 4
    assert decisions[0].final_score.canonical_score == 4


def test_resolve_fallback_human_review():
    policy = _policy(
        default_strategy="use_resolution_rater_as_authoritative",
        fallback_if_no_resolution="human_review",
    )
    hyps = [_hyp("d1", "rater_1", 2), _hyp("d1", "rater_2", 5)]

    result = reconciliation.run(hyps, policy)
    adj_records, decisions = reconciliation.resolve(result.conflicts, hyps, policy)

    assert len(adj_records) == 1
    assert adj_records[0].resolution_path == ResolutionPath.HUMAN_REVIEW
    assert adj_records[0].is_resolved is False
    assert decisions[0].decision_note is not None
    assert "fallback" in decisions[0].decision_note


def test_decision_note_populated():
    policy = _policy()
    hyps = [
        _hyp("d1", "rater_1", 2),
        _hyp("d1", "rater_2", 5),
        _hyp("d1", "rater_3", 4),  # conflict resolved by resolution rater
        _hyp("d2", "rater_1", 3),
        _hyp("d2", "rater_2", 3),  # no conflict
    ]

    result = reconciliation.run(hyps, policy)
    _, decisions = reconciliation.resolve(result.conflicts, hyps, policy)
    assert decisions
    assert all((d.decision_note or "").strip() for d in decisions)

