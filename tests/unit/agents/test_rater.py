import pytest

from src.agents import rater
from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.package import DataPackage, Unit
from src.providers.fake import FakeProvider, fake_response
from src.providers.prompt_loader import PromptLoader

_SCALE = {"scale_id": "ordinal_1_5", "type": "ordinal", "min": 1, "max": 5}
_INDICATOR = "二级指标的完整解释（供选段/取证判断相关性）"

_DIMENSION = {
    "dimension_id": "a4_1",
    "code": "A4-1",
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
        dimension_by_code={"A4-1": _DIMENSION},
        scale_by_id={"ordinal_1_5": _SCALE},
    )


def _package() -> DataPackage:
    units = [
        Unit(id=0, kind="prose", text="老年用户经常无法看懂界面文字。", source_file="a.md", char_range=(0, 10), speaker=None),
        Unit(id=1, kind="prose", text="年轻用户反馈良好。", source_file="a.md", char_range=(10, 20), speaker=None),
        Unit(id=2, kind="prose", text="与本维度无关的一句话。", source_file="a.md", char_range=(20, 30), speaker=None),
    ]
    return DataPackage(package_id="pkg-1", units=units, metadata={})


def _templates():
    loader = PromptLoader()
    return (
        loader.load("configs/prompts/select.yaml"),
        loader.load("configs/prompts/extraction.yaml"),
        loader.load("configs/prompts/scoring.yaml"),
    )


# ── select ───────────────────────────────────────────────────────────────────


def test_select_returns_ids_from_scripted_response() -> None:
    select_t, _, _ = _templates()
    provider = FakeProvider([fake_response({"selected_unit_ids": [0, 1]})])

    selected = rater.select(_package(), _DIMENSION, provider, select_t, "rater_1", _INDICATOR)

    assert selected == [0, 1]


def test_select_silently_filters_hallucinated_ids() -> None:
    select_t, _, _ = _templates()
    provider = FakeProvider([fake_response({"selected_unit_ids": [0, 999]})])

    selected = rater.select(_package(), _DIMENSION, provider, select_t, "rater_1", _INDICATOR)

    assert selected == [0]


# ── extract ──────────────────────────────────────────────────────────────────


def test_extract_returns_evidence_ids_from_scripted_response() -> None:
    _, extract_t, _ = _templates()
    provider = FakeProvider([fake_response({"evidence_unit_ids": [0]})])

    evidence = rater.extract(_package(), [0, 1], _DIMENSION, provider, extract_t, "rater_1", _INDICATOR)

    assert evidence == [0]


def test_extract_rejects_out_of_bounds_unit_id() -> None:
    _, extract_t, _ = _templates()
    provider = FakeProvider([fake_response({"evidence_unit_ids": [2]})])

    with pytest.raises(ValueError, match="越界"):
        rater.extract(_package(), [0, 1], _DIMENSION, provider, extract_t, "rater_1", _INDICATOR)


# ── score ────────────────────────────────────────────────────────────────────


def test_score_produces_dimension_score_from_scripted_response() -> None:
    _, _, score_t = _templates()
    provider = FakeProvider(
        [fake_response({"proposed_score": 4, "supporting_unit_ids": [0], "confidence": 0.9, "rationale": "理由"})]
    )

    dim_score = rater.score(_package(), [0], _DIMENSION, _rubric(), provider, score_t, "rater_1")

    assert dim_score.dimension_id == "a4_1"
    assert dim_score.score.canonical_score == 4
    assert dim_score.supporting_unit_ids == [0]
    assert dim_score.confidence == 0.9
    assert dim_score.rationale == "理由"


def test_score_clamps_out_of_range_score_to_scale() -> None:
    _, _, score_t = _templates()
    provider = FakeProvider([fake_response({"proposed_score": 99, "supporting_unit_ids": [0]})])

    dim_score = rater.score(_package(), [0], _DIMENSION, _rubric(), provider, score_t, "rater_1")

    assert dim_score.score.canonical_score == 5


def test_score_rejects_out_of_bounds_supporting_unit_id() -> None:
    _, _, score_t = _templates()
    provider = FakeProvider([fake_response({"proposed_score": 3, "supporting_unit_ids": [1]})])

    with pytest.raises(ValueError, match="越界"):
        rater.score(_package(), [0], _DIMENSION, _rubric(), provider, score_t, "rater_1")


def test_score_passes_through_empty_supporting_ids_when_model_omits_them() -> None:
    """未额外合成默认值——模型没引用支持编号就原样留空，不代它决定。"""
    _, _, score_t = _templates()
    provider = FakeProvider([fake_response({"proposed_score": 3})])

    dim_score = rater.score(_package(), [0, 1], _DIMENSION, _rubric(), provider, score_t, "rater_1")

    assert dim_score.supporting_unit_ids == []


# ── run_chain（主接缝：整条链 + FakeProvider） ────────────────────────────────


def test_run_chain_produces_rater_chain_result_end_to_end() -> None:
    select_t, extract_t, score_t = _templates()
    provider = FakeProvider(
        [
            fake_response({"selected_unit_ids": [0, 1]}),
            fake_response({"evidence_unit_ids": [0]}),
            fake_response(
                {"proposed_score": 4, "supporting_unit_ids": [0], "confidence": 0.85, "rationale": "有清晰证据"}
            ),
        ]
    )

    result = rater.run_chain(
        _package(), "a4_1", _rubric(), provider, select_t, extract_t, score_t, rater_id="rater_1"
    )

    assert result.rater_id == "rater_1"
    assert result.dimension_id == "a4_1"
    assert result.selected_unit_ids == [0, 1]
    assert result.evidence_unit_ids == [0]
    assert result.score.score.canonical_score == 4
    assert result.score.supporting_unit_ids == [0]
    # 三趟共用同一个 provider 实例，且按 select→extract→score 顺序各调用一次
    assert len(provider.requests) == 3


def test_run_chain_evidence_unit_ids_resolve_back_to_source_text() -> None:
    select_t, extract_t, score_t = _templates()
    provider = FakeProvider(
        [
            fake_response({"selected_unit_ids": [0, 1, 2]}),
            fake_response({"evidence_unit_ids": [0, 2]}),
            fake_response({"proposed_score": 3, "supporting_unit_ids": [0]}),
        ]
    )
    package = _package()

    result = rater.run_chain(package, "a4_1", _rubric(), provider, select_t, extract_t, score_t, rater_id="rater_2")

    cited_texts = [package.get_unit(uid).text for uid in result.evidence_unit_ids]
    assert cited_texts == ["老年用户经常无法看懂界面文字。", "与本维度无关的一句话。"]


def test_run_chain_raises_on_unknown_dimension() -> None:
    select_t, extract_t, score_t = _templates()
    provider = FakeProvider([])

    with pytest.raises(ValueError):
        rater.run_chain(_package(), "does_not_exist", _rubric(), provider, select_t, extract_t, score_t, rater_id="rater_1")
