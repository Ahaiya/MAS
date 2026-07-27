from src.agents import report
from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.package import DataPackage, Unit
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import DimensionScore, FinalDecision, RaterChainResult, ScoreSource
from src.providers.base import LLMResponse, TokenUsage
from src.providers.fake import FakeProvider
from src.providers.prompt_loader import PromptLoader

_SCALE = {"scale_id": "ordinal_1_5", "type": "ordinal", "min": 1, "max": 5}
_DIMENSIONS = [
    {"dimension_id": f"a4_{i}", "name": f"dim{i}", "scale_ref": "ordinal_1_5",
     "levels": [{"rank": r, "summary": str(r), "descriptors": [f"level {r}"]} for r in range(1, 6)]}
    for i in (1, 2)
]


def _rubric() -> RubricSnapshot:
    return RubricSnapshot(
        rubric_id="r",
        rubric_version="t",
        rubric_name="n",
        dimensions=_DIMENSIONS,
        scales=[_SCALE],
        dimension_by_id={d["dimension_id"]: d for d in _DIMENSIONS},
        dimension_by_code={},
        scale_by_id={"ordinal_1_5": _SCALE},
    )


def _package() -> DataPackage:
    units = [Unit(id=i, kind="prose", text=f"text {i}", source_file="a.md", char_range=(0, 5), speaker=None) for i in range(5)]
    return DataPackage(package_id="pkg-1", units=units, metadata={})


def _decision(dimension_id: str, score_val: int, source: ScoreSource, unit_ids) -> FinalDecision:
    return FinalDecision(
        dimension_id=dimension_id,
        final_score=create_score_representation(score_val, "ordinal_1_5"),
        source=source,
        unit_ids=list(unit_ids),
    )


def _chain(rater_id: str, dimension_id: str, score_val: int) -> RaterChainResult:
    return RaterChainResult(
        rater_id=rater_id,
        dimension_id=dimension_id,
        selected_unit_ids=[0, 1],
        evidence_unit_ids=[0],
        score=DimensionScore(
            dimension_id=dimension_id,
            score=create_score_representation(score_val, "ordinal_1_5"),
            supporting_unit_ids=[0],
            rationale="r",
            confidence=0.8,
        ),
    )


def _text_response(text: str) -> LLMResponse:
    return LLMResponse(content=text, structured_data=None, usage=TokenUsage(0, 0, 0), provider_name="fake", model_id="m")


def _feedback_template():
    return PromptLoader().load("configs/prompts/feedback.yaml")


# ── build_radar_data ─────────────────────────────────────────────────────────


def test_build_radar_data_returns_score_per_dimension_sorted() -> None:
    decisions = [
        _decision("a4_2", 4, ScoreSource.CONSENSUS, [0]),
        _decision("a4_1", 3, ScoreSource.CONSENSUS, [0]),
    ]

    radar = report.build_radar_data(decisions)

    assert radar == [{"dimension_id": "a4_1", "score": 3}, {"dimension_id": "a4_2", "score": 4}]


# ── generate_feedback_text ───────────────────────────────────────────────────


def test_generate_feedback_text_returns_llm_content() -> None:
    decision = _decision("a4_1", 3, ScoreSource.CONSENSUS, [0])
    provider = FakeProvider([_text_response("做得不错，继续加油。")])

    text = report.generate_feedback_text(_package(), decision, _DIMENSIONS[0], provider, _feedback_template())

    assert text == "做得不错，继续加油。"


# ── build_feedback_report ────────────────────────────────────────────────────


def test_build_feedback_report_has_primary_score_radar_and_per_dim_fields() -> None:
    decisions = [
        _decision("a4_1", 3, ScoreSource.CONSENSUS, [0]),
        _decision("a4_2", 5, ScoreSource.ADJUDICATED, [2]),
    ]
    provider = FakeProvider([_text_response("反馈1"), _text_response("反馈2")])

    result = report.build_feedback_report(_package(), decisions, _rubric(), provider, _feedback_template())

    assert result["primary_score"] == 4.0
    assert result["radar"] == [{"dimension_id": "a4_1", "score": 3}, {"dimension_id": "a4_2", "score": 5}]
    assert result["dimensions"]["a4_1"] == {
        "final_score": 3,
        "source": "consensus",
        "unit_ids": [0],
        "feedback": "反馈1",
    }
    assert result["dimensions"]["a4_2"] == {
        "final_score": 5,
        "source": "adjudicated",
        "unit_ids": [2],
        "feedback": "反馈2",
    }


def test_feedback_report_unit_ids_resolve_back_to_package_text() -> None:
    decisions = [_decision("a4_1", 3, ScoreSource.CONSENSUS, [3, 4])]
    provider = FakeProvider([_text_response("反馈")])
    package = _package()

    result = report.build_feedback_report(package, decisions, _rubric(), provider, _feedback_template())

    cited_texts = [package.get_unit(uid).text for uid in result["dimensions"]["a4_1"]["unit_ids"]]
    assert cited_texts == ["text 3", "text 4"]


# ── build_rater_chains_report ────────────────────────────────────────────────


def test_build_rater_chains_report_includes_both_full_chains_and_final_decisions() -> None:
    chains_a = [_chain("rater_1", "a4_1", 3), _chain("rater_1", "a4_2", 4)]
    chains_b = [_chain("rater_2", "a4_1", 3), _chain("rater_2", "a4_2", 5)]
    decisions = [
        _decision("a4_1", 3, ScoreSource.CONSENSUS, [0]),
        _decision("a4_2", 4, ScoreSource.ADJUDICATED, [2]),
    ]

    result = report.build_rater_chains_report(chains_a, chains_b, decisions)

    assert set(result.keys()) == {"chains", "final_decisions"}
    assert len(result["chains"]) == 4
    assert {c["rater_id"] for c in result["chains"]} == {"rater_1", "rater_2"}
    assert [d["dimension_id"] for d in result["final_decisions"]] == ["a4_1", "a4_2"]
    assert result["final_decisions"][1]["source"] == "adjudicated"


def test_build_rater_chains_report_does_not_collide_when_rater_ids_match() -> None:
    """chains 是扁平列表，不按 rater_id 做外层 dict 键——两条链同 rater_id 时也不互相覆盖。"""
    chains_a = [_chain("rater_x", "a4_1", 3)]
    chains_b = [_chain("rater_x", "a4_2", 4)]

    result = report.build_rater_chains_report(chains_a, chains_b, [])

    assert len(result["chains"]) == 2
