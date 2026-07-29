import dataclasses

import pytest

from src.contracts.scoring import DimensionScore, FinalDecision, RaterChainResult, ScoreSource


def _dimension_score(confidence: float = 0.9) -> DimensionScore:
    return DimensionScore(
        score=3,
        supporting_unit_ids=[1, 2],
        rationale="unit 1 与 2 显示清晰的因果链",
        confidence=confidence,
    )


def test_dimension_score_constructs_with_valid_fields() -> None:
    ds = _dimension_score()
    assert ds.score == 3
    assert ds.supporting_unit_ids == [1, 2]
    assert ds.confidence == 0.9


def test_dimension_score_is_immutable() -> None:
    ds = _dimension_score()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ds.confidence = 0.5  # type: ignore[misc]


def test_dimension_score_rejects_out_of_range_confidence() -> None:
    with pytest.raises(ValueError):
        _dimension_score(confidence=1.5)


def test_rater_chain_result_constructs_with_valid_fields() -> None:
    chain = RaterChainResult(
        rater_id="rater_1",
        dimension_id="a4_1",
        selected_unit_ids=[1, 2, 3],
        evidence_unit_ids=[1, 2],
        score=_dimension_score(),
    )
    assert chain.rater_id == "rater_1"
    assert chain.selected_unit_ids == [1, 2, 3]
    assert chain.score.supporting_unit_ids == [1, 2]


def test_rater_chain_result_is_immutable() -> None:
    chain = RaterChainResult(
        rater_id="rater_1",
        dimension_id="a4_1",
        selected_unit_ids=[1],
        evidence_unit_ids=[1],
        score=_dimension_score(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        chain.rater_id = "rater_2"  # type: ignore[misc]


def test_final_decision_constructs_with_valid_fields() -> None:
    decision = FinalDecision(
        dimension_id="a4_1",
        final_score=3,
        source=ScoreSource.CONSENSUS,
        unit_ids=[1, 2],
    )
    assert decision.source is ScoreSource.CONSENSUS
    assert decision.unit_ids == [1, 2]
    assert decision.to_dict()["source"] == "consensus"


def test_final_decision_is_immutable() -> None:
    decision = FinalDecision(
        dimension_id="a4_1",
        final_score=3,
        source=ScoreSource.ADJUDICATED,
        unit_ids=[1],
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.source = ScoreSource.CONSENSUS  # type: ignore[misc]
