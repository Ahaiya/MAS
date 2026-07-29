import pytest

from src.contracts.scoring import FinalDecision, ScoreSource
from src.policies.aggregation import aggregate_final_decisions

_SCALE_REF = "ordinal_1_5"


def _decision(dimension_id: str, score_val: int, source: ScoreSource = ScoreSource.CONSENSUS) -> FinalDecision:
    return FinalDecision(
        dimension_id=dimension_id,
        final_score=score_val,
        source=source,
        unit_ids=[0],
    )


def test_aggregate_uses_observation_point_weights() -> None:
    """量规里 0.4/0.6 的权重必须真的生效，而不是被抹平成等权 3.5。"""
    decisions = [_decision("f2_1", 3), _decision("f2_2", 4)]

    assert aggregate_final_decisions(decisions, {"f2_1": 0.4, "f2_2": 0.6}) == pytest.approx(3.6)


def test_aggregate_equal_weights_is_arithmetic_mean() -> None:
    decisions = [_decision("a4_1", 3), _decision("a4_2", 4), _decision("a4_3", 5)]
    weights = {"a4_1": 1 / 3, "a4_2": 1 / 3, "a4_3": 1 / 3}

    assert aggregate_final_decisions(decisions, weights) == pytest.approx(4.0)


def test_aggregate_single_observation_point_degenerates_to_its_score() -> None:
    assert aggregate_final_decisions([_decision("d1_1", 3)], {"d1_1": 1.0}) == pytest.approx(3.0)


def test_aggregate_renormalizes_when_observation_points_are_missing() -> None:
    """某个观测点评价失败没有 FinalDecision 时，剩余权重要重新归一化——
    否则一个观测点失败会让整个 dim 分数凭空变低（0.4*3+0.6*4 少掉一项）。"""
    weights = {"d3_1": 0.25, "d3_2": 0.25, "d3_3": 0.3, "d3_4": 0.2}
    decisions = [_decision("d3_1", 2), _decision("d3_3", 4)]

    # (0.25*2 + 0.3*4) / (0.25 + 0.3) = 1.7 / 0.55
    assert aggregate_final_decisions(decisions, weights) == pytest.approx(1.7 / 0.55)


def test_aggregate_single_surviving_observation_point_equals_its_score() -> None:
    """归一化的极端情形：只剩一个观测点时，dim 分就是它自己的分。"""
    weights = {"f2_1": 0.4, "f2_2": 0.6}

    assert aggregate_final_decisions([_decision("f2_2", 5)], weights) == pytest.approx(5.0)


def test_aggregate_treats_consensus_and_adjudicated_the_same() -> None:
    """二级指标分只看 final_score，不区分 source——两种 source 都已是权威值。"""
    decisions = [
        _decision("a4_1", 2, ScoreSource.CONSENSUS),
        _decision("a4_2", 4, ScoreSource.ADJUDICATED),
    ]

    assert aggregate_final_decisions(decisions, {"a4_1": 0.5, "a4_2": 0.5}) == pytest.approx(3.0)


def test_aggregate_rejects_empty_decisions() -> None:
    with pytest.raises(ValueError):
        aggregate_final_decisions([], {"a4_1": 1.0})


def test_aggregate_rejects_unknown_dimension_id() -> None:
    """decisions 与 weights 对不上是配置/编译期出了问题，必须炸响而不是静默算。"""
    with pytest.raises(KeyError):
        aggregate_final_decisions([_decision("a4_9", 3)], {"a4_1": 1.0})
