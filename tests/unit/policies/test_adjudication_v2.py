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


def _policy(score_gap_threshold: int = 1, drift_min_dimensions: int = 2) -> PolicySnapshot:
    return PolicySnapshot(
        score_gap_threshold=score_gap_threshold,
        drift_min_dimensions=drift_min_dimensions,
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


def test_gap_exactly_at_threshold_does_not_trigger() -> None:
    """规则一是严格大于：分差恰等于阈值不触发。"""
    chains_a = [_chain("rater_1", "a4_1", 2)]
    chains_b = [_chain("rater_2", "a4_1", 4)]

    assert needs_adjudication(chains_a, chains_b, _policy(score_gap_threshold=2)) == set()


def test_gap_above_threshold_triggers() -> None:
    chains_a = [_chain("rater_1", "a4_1", 1)]
    chains_b = [_chain("rater_2", "a4_1", 4)]

    assert needs_adjudication(chains_a, chains_b, _policy(score_gap_threshold=2)) == {"a4_1"}


def test_drift_min_dimensions_zero_never_triggers() -> None:
    """阈值为 0 时同向漂移规则整体关闭，不会把所有相邻分歧都拉进来。"""
    chains_a = [_chain("rater_1", "a4_1", 3), _chain("rater_1", "a4_2", 3)]
    chains_b = [_chain("rater_2", "a4_1", 4), _chain("rater_2", "a4_2", 4)]

    assert needs_adjudication(chains_a, chains_b, _policy(drift_min_dimensions=0)) == set()


def test_drift_min_dimensions_negative_never_triggers() -> None:
    chains_a = [_chain("rater_1", "a4_1", 3), _chain("rater_1", "a4_2", 3)]
    chains_b = [_chain("rater_2", "a4_1", 4), _chain("rater_2", "a4_2", 4)]

    assert needs_adjudication(chains_a, chains_b, _policy(drift_min_dimensions=-1)) == set()


def test_gap_and_drift_results_are_unioned() -> None:
    """规则一命中的观测点与规则二命中的观测点取并集。"""
    chains_a = [
        _chain("rater_1", "a4_1", 1),
        _chain("rater_1", "a4_2", 3),
        _chain("rater_1", "a4_3", 3),
    ]
    chains_b = [
        _chain("rater_2", "a4_1", 4),
        _chain("rater_2", "a4_2", 4),
        _chain("rater_2", "a4_3", 4),
    ]

    assert needs_adjudication(chains_a, chains_b, _policy()) == {"a4_1", "a4_2", "a4_3"}
