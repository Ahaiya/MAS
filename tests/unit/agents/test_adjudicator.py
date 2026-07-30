import pytest

from src.agents import adjudicator
from src.contracts.configuration import RubricSnapshot
from src.contracts.package import DataPackage, Unit
from src.contracts.scoring import DimensionScore, RaterChainResult
from src.providers.fake import FakeProvider, fake_response
from src.providers.prompt_loader import PromptLoader

_DIMENSION = {
    "code": "A4-1",
    "name": "用户群体识别的全面性",
    "anchors": {r: f"level {r}" for r in range(1, 6)},
}


def _rubric() -> RubricSnapshot:
    return RubricSnapshot(
        dim_id="a4",
        dim_name="A4 用户研究",
        indicator_description="desc",
        dimensions=[_DIMENSION],
        scale_min=1,
        scale_max=5,
        scale_levels={r: str(r) for r in range(1, 6)},
    )


def _package() -> DataPackage:
    units = [
        Unit(id=0, kind="prose", text="老年用户经常无法看懂界面文字。", source_file="a.md", char_range=(0, 10), speaker=None),
        Unit(id=1, kind="prose", text="年轻用户反馈良好。", source_file="a.md", char_range=(10, 20), speaker=None),
        Unit(id=2, kind="prose", text="第三方观察者未曾提及的一句话。", source_file="a.md", char_range=(20, 30), speaker=None),
    ]
    return DataPackage(package_id="pkg-1", units=units, metadata={})


def _chain(rater_id: str, score_val: int, evidence_unit_ids, rationale: str = "r") -> RaterChainResult:
    return RaterChainResult(
        rater_id=rater_id,
        code="A4-1",
        selected_unit_ids=list(evidence_unit_ids),
        evidence_unit_ids=list(evidence_unit_ids),
        score=DimensionScore(
            score=score_val,
            supporting_unit_ids=list(evidence_unit_ids),
            rationale=rationale,
            confidence=0.8,
        ),
    )


def _template():
    return PromptLoader().load("configs/prompts/adjudication.yaml")


def test_adjudicate_produces_dimension_score_from_scripted_response() -> None:
    provider = FakeProvider(
        [fake_response({"proposed_score": 4, "supporting_unit_ids": [2], "confidence": 0.9, "rationale": "仲裁理由"})]
    )
    chain_a = _chain("rater_1", 2, [0])
    chain_b = _chain("rater_2", 4, [1])

    result = adjudicator.adjudicate(_package(), _DIMENSION, _rubric(), chain_a, chain_b, provider, _template())

    assert result.score == 4
    assert result.supporting_unit_ids == [2]
    assert result.rationale == "仲裁理由"


def test_adjudicate_can_cite_unit_not_seen_by_either_rater() -> None:
    """Rater3 看得到完整原文，引用范围是整个 package，不局限于双链各自的证据子集。"""
    provider = FakeProvider([fake_response({"proposed_score": 3, "supporting_unit_ids": [2]})])
    chain_a = _chain("rater_1", 2, [0])
    chain_b = _chain("rater_2", 4, [1])

    result = adjudicator.adjudicate(_package(), _DIMENSION, _rubric(), chain_a, chain_b, provider, _template())

    assert result.supporting_unit_ids == [2]


def test_adjudicate_rejects_unit_id_outside_package() -> None:
    provider = FakeProvider([fake_response({"proposed_score": 3, "supporting_unit_ids": [999]})])
    chain_a = _chain("rater_1", 2, [0])
    chain_b = _chain("rater_2", 4, [1])

    with pytest.raises(ValueError, match="越界"):
        adjudicator.adjudicate(_package(), _DIMENSION, _rubric(), chain_a, chain_b, provider, _template())


def test_adjudicate_rejects_empty_supporting_unit_ids() -> None:
    """强制引用证据 unit_ids：仲裁分不允许零引用的裸分数。"""
    provider = FakeProvider([fake_response({"proposed_score": 3, "supporting_unit_ids": []})])
    chain_a = _chain("rater_1", 2, [0])
    chain_b = _chain("rater_2", 4, [1])

    with pytest.raises(ValueError, match="未引用任何证据"):
        adjudicator.adjudicate(_package(), _DIMENSION, _rubric(), chain_a, chain_b, provider, _template())


def test_adjudicate_rejects_omitted_supporting_unit_ids() -> None:
    provider = FakeProvider([fake_response({"proposed_score": 3})])
    chain_a = _chain("rater_1", 2, [0])
    chain_b = _chain("rater_2", 4, [1])

    with pytest.raises(ValueError, match="未引用任何证据"):
        adjudicator.adjudicate(_package(), _DIMENSION, _rubric(), chain_a, chain_b, provider, _template())


def test_adjudicate_prompt_shows_both_rationales_but_not_scores() -> None:
    """Rater3 要看到双方的判档理由（分歧的实质在理由里），但仍不给分数与
    confidence——自报的置信度是没有论证的权威感，不该成为它的偏向依据。"""
    from src.agents.prompt_builders import build_adjudication_prompt

    chain_a = _chain("rater_1", 2, [0], rationale="只覆盖了一类用户")
    chain_b = _chain("rater_2", 4, [1], rationale="识别了多类用户及其差异")

    prompt = build_adjudication_prompt(_package(), _DIMENSION, {}, chain_a, chain_b, _template())

    assert "只覆盖了一类用户" in prompt
    assert "识别了多类用户及其差异" in prompt
    cited_lines = [line.strip() for line in prompt.splitlines() if "cited units" in line]
    assert cited_lines == ["cited units: [0]", "cited units: [1]"]
    assert "0.8" not in prompt  # confidence 不进 prompt
