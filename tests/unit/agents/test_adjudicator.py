import pytest

from src.agents import adjudicator
from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.package import DataPackage, Unit
from src.contracts.scoring import DimensionScore, RaterChainResult
from src.providers.fake import FakeProvider, fake_response
from src.providers.prompt_loader import PromptLoader

_SCALE = {"scale_id": "ordinal_1_5", "type": "ordinal", "min": 1, "max": 5}
_DIMENSION = {
    "dimension_id": "a4_1",
    "name": "用户群体识别的全面性",
    "scale_ref": "ordinal_1_5",
    "levels": [{"rank": r, "summary": str(r), "descriptors": [f"level {r}"]} for r in range(1, 6)],
}


def _rubric() -> RubricSnapshot:
    return RubricSnapshot(
        rubric_id="r",
        rubric_version="t",
        rubric_name="n",
        dimensions=[_DIMENSION],
        scales=[_SCALE],
        dimension_by_id={"a4_1": _DIMENSION},
        dimension_by_code={},
        scale_by_id={"ordinal_1_5": _SCALE},
    )


def _package() -> DataPackage:
    units = [
        Unit(id=0, kind="prose", text="老年用户经常无法看懂界面文字。", source_file="a.md", char_range=(0, 10), speaker=None),
        Unit(id=1, kind="prose", text="年轻用户反馈良好。", source_file="a.md", char_range=(10, 20), speaker=None),
        Unit(id=2, kind="prose", text="第三方观察者未曾提及的一句话。", source_file="a.md", char_range=(20, 30), speaker=None),
    ]
    return DataPackage(package_id="pkg-1", units=units, metadata={})


def _chain(rater_id: str, score_val: int, evidence_unit_ids) -> RaterChainResult:
    return RaterChainResult(
        rater_id=rater_id,
        dimension_id="a4_1",
        selected_unit_ids=list(evidence_unit_ids),
        evidence_unit_ids=list(evidence_unit_ids),
        score=DimensionScore(
            score=score_val,
            supporting_unit_ids=list(evidence_unit_ids),
            rationale="r",
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


def test_adjudicate_prompt_never_shows_either_raters_score() -> None:
    """防锚定：Rater3 的 prompt 只应看到双链引用的 unit_ids，不含分数字段。"""
    from src.agents.prompt_builders import build_adjudication_prompt

    chain_a = _chain("rater_1", 2, [0])
    chain_b = _chain("rater_2", 4, [1])

    prompt = build_adjudication_prompt(_package(), _DIMENSION, chain_a, chain_b, _template())
    cited_lines = [line for line in prompt.splitlines() if "cited units" in line]

    assert cited_lines == ["rater_1 cited units: [0]", "rater_2 cited units: [1]"]
