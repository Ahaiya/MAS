import pytest

from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import FinalDecision, ScoreSource
from src.policies.aggregation import aggregate_final_decisions

_SCALE_REF = "ordinal_1_5"


def _decision(dimension_id: str, score_val: int, source: ScoreSource = ScoreSource.CONSENSUS) -> FinalDecision:
    return FinalDecision(
        dimension_id=dimension_id,
        final_score=create_score_representation(score_val, _SCALE_REF),
        source=source,
        unit_ids=[0],
    )


def test_aggregate_is_equal_weight_average() -> None:
    decisions = [_decision("a4_1", 3), _decision("a4_2", 4), _decision("a4_3", 5)]

    assert aggregate_final_decisions(decisions) == 4.0


def test_aggregate_treats_consensus_and_adjudicated_the_same() -> None:
    """一级指标分只看 final_score，不区分 source——两种 source 都已是权威值。"""
    decisions = [
        _decision("a4_1", 2, ScoreSource.CONSENSUS),
        _decision("a4_2", 4, ScoreSource.ADJUDICATED),
    ]

    assert aggregate_final_decisions(decisions) == 3.0


def test_aggregate_rejects_empty_decisions() -> None:
    with pytest.raises(ValueError):
        aggregate_final_decisions([])
