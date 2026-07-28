from src.contracts.artifact_bundle import PolicySnapshot
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import DimensionScore, RaterChainResult
from src.policies.adjudication import needs_adjudication

_SCALE_REF = "ordinal_1_5"


def _chain(rater_id: str, dimension_id: str, score_val: int) -> RaterChainResult:
    return RaterChainResult(
        rater_id=rater_id,
        dimension_id=dimension_id,
        selected_unit_ids=[0],
        evidence_unit_ids=[0],
        score=DimensionScore(
            dimension_id=dimension_id,
            score=create_score_representation(score_val, _SCALE_REF),
            supporting_unit_ids=[0],
            rationale="r",
            confidence=0.8,
        ),
    )


def _policy() -> PolicySnapshot:
    return PolicySnapshot(
        adjudication_policy={
            "triggers": [
                {
                    "trigger_id": "score_distance",
                    "type": "score_distance",
                    "applies_to_dimensions": ["*"],
                    "threshold": {"operator": ">", "value": 1},
                },
                {
                    "trigger_id": "adjacent_drift",
                    "type": "adjacent_drift",
                    "applies_to_dimensions": ["*"],
                    "pattern": {
                        "score_gap": 1,
                        "min_matching_dimensions": 2,
                        "require_same_direction": True,
                    },
                },
            ]
        },
        policy_version="test",
    )


def test_identical_scores_never_trigger() -> None:
    chains_a = [_chain("rater_1", "a4_1", 3), _chain("rater_1", "a4_2", 4)]
    chains_b = [_chain("rater_2", "a4_1", 3), _chain("rater_2", "a4_2", 4)]

    assert needs_adjudication(chains_a, chains_b, _policy()) == set()


def test_isolated_diff_of_one_does_not_trigger() -> None:
    """分差恰好为 1、且不构成 ≥2 维同向漂移组时，不触发（属于一致范畴）。"""
    chains_a = [_chain("rater_1", "a4_1", 3), _chain("rater_1", "a4_2", 4)]
    chains_b = [_chain("rater_2", "a4_1", 4), _chain("rater_2", "a4_2", 4)]

    assert needs_adjudication(chains_a, chains_b, _policy()) == set()


def test_score_distance_greater_than_one_triggers() -> None:
    chains_a = [_chain("rater_1", "a4_1", 2), _chain("rater_1", "a4_2", 4)]
    chains_b = [_chain("rater_2", "a4_1", 4), _chain("rater_2", "a4_2", 4)]

    assert needs_adjudication(chains_a, chains_b, _policy()) == {"a4_1"}


def test_adjacent_drift_same_direction_across_two_dims_triggers() -> None:
    chains_a = [
        _chain("rater_1", "a4_1", 3),
        _chain("rater_1", "a4_2", 3),
        _chain("rater_1", "a4_3", 4),
    ]
    chains_b = [
        _chain("rater_2", "a4_1", 4),
        _chain("rater_2", "a4_2", 4),
        _chain("rater_2", "a4_3", 4),
    ]

    assert needs_adjudication(chains_a, chains_b, _policy()) == {"a4_1", "a4_2"}


def test_adjacent_drift_opposite_directions_does_not_trigger() -> None:
    """两维分歧方向相反时不构成"同向"漂移，不触发。"""
    chains_a = [_chain("rater_1", "a4_1", 3), _chain("rater_1", "a4_2", 4)]
    chains_b = [_chain("rater_2", "a4_1", 4), _chain("rater_2", "a4_2", 3)]

    assert needs_adjudication(chains_a, chains_b, _policy()) == set()
