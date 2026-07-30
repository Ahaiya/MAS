from src.agents import feedback
from src.contracts.configuration import RubricSnapshot
from src.contracts.package import DataPackage, Unit
from src.contracts.scoring import DimensionScore, FinalDecision, RaterChainResult, ScoreSource
from src.providers.base import LLMResponse, TokenUsage
from src.providers.fake import FakeProvider
from src.providers.prompt_loader import PromptLoader

_DIMENSIONS = [
    {"code": f"A4-{i}", "name": f"dim{i}", "weight": 0.5,
     "anchors": {r: f"level {r}" for r in range(1, 6)}}
    for i in (1, 2)
]


def _rubric() -> RubricSnapshot:
    return RubricSnapshot(
        dim_id="a4",
        dim_name="A4 用户研究",
        indicator_description="desc",
        dimensions=_DIMENSIONS,
        scale_min=1,
        scale_max=5,
        scale_levels={r: str(r) for r in range(1, 6)},
    )


def _package() -> DataPackage:
    units = [Unit(id=i, kind="prose", text=f"text {i}", source_file="a.md", char_range=(0, 5), speaker=None) for i in range(5)]
    return DataPackage(package_id="pkg-1", units=units, metadata={})


def _decision(
    code: str, score_val: int, source: ScoreSource, unit_ids, rationale: str = "定分理由"
) -> FinalDecision:
    return FinalDecision(
        code=code,
        final_score=score_val,
        source=source,
        unit_ids=list(unit_ids),
        rationale=rationale,
    )


def _chain(rater_id: str, code: str, score_val: int) -> RaterChainResult:
    return RaterChainResult(
        rater_id=rater_id,
        code=code,
        selected_unit_ids=[0, 1],
        evidence_unit_ids=[0],
        score=DimensionScore(
            score=score_val,
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
        _decision("A4-2", 4, ScoreSource.CONSENSUS, [0]),
        _decision("A4-1", 3, ScoreSource.CONSENSUS, [0]),
    ]

    radar = feedback.build_radar_data(decisions)

    assert radar == [{"code": "A4-1", "score": 3}, {"code": "A4-2", "score": 4}]


# ── generate_feedback_text ───────────────────────────────────────────────────


def test_generate_feedback_text_returns_llm_content() -> None:
    decision = _decision("A4-1", 3, ScoreSource.CONSENSUS, [0])
    provider = FakeProvider([_text_response("做得不错，继续加油。")])

    text = feedback.generate_feedback_text(_package(), decision, _DIMENSIONS[0], {}, provider, _feedback_template())

    assert text == "做得不错，继续加油。"


# ── build_feedback_report ────────────────────────────────────────────────────


def test_build_feedback_report_has_primary_score_radar_and_per_dim_fields() -> None:
    decisions = [
        _decision("A4-1", 3, ScoreSource.CONSENSUS, [0]),
        _decision("A4-2", 5, ScoreSource.ADJUDICATED, [2]),
    ]
    provider = FakeProvider([_text_response("反馈1"), _text_response("反馈2")])

    result = feedback.build_feedback_report(_package(), decisions, _rubric(), provider, _feedback_template())

    assert result["primary_score"] == 4.0
    assert result["radar"] == [{"code": "A4-1", "score": 3}, {"code": "A4-2", "score": 5}]
    assert result["dimensions"]["A4-1"] == {
        "final_score": 3,
        "source": "consensus",
        "unit_ids": [0],
        "feedback": "反馈1",
    }
    assert result["dimensions"]["A4-2"] == {
        "final_score": 5,
        "source": "adjudicated",
        "unit_ids": [2],
        "feedback": "反馈2",
    }


def test_feedback_report_unit_ids_resolve_back_to_package_text() -> None:
    decisions = [_decision("A4-1", 3, ScoreSource.CONSENSUS, [3, 4])]
    provider = FakeProvider([_text_response("反馈")])
    package = _package()

    result = feedback.build_feedback_report(package, decisions, _rubric(), provider, _feedback_template())

    cited_texts = [package.get_unit(uid).text for uid in result["dimensions"]["A4-1"]["unit_ids"]]
    assert cited_texts == ["text 3", "text 4"]


# ── build_rater_chains_report ────────────────────────────────────────────────


def test_build_rater_chains_report_includes_both_full_chains_and_final_decisions() -> None:
    chains_a = [_chain("rater_1", "A4-1", 3), _chain("rater_1", "A4-2", 4)]
    chains_b = [_chain("rater_2", "A4-1", 3), _chain("rater_2", "A4-2", 5)]
    decisions = [
        _decision("A4-1", 3, ScoreSource.CONSENSUS, [0]),
        _decision("A4-2", 4, ScoreSource.ADJUDICATED, [2]),
    ]

    result = feedback.build_rater_chains_report(chains_a, chains_b, decisions)

    assert set(result.keys()) == {"chains", "final_decisions"}
    assert len(result["chains"]) == 4
    assert {c["rater_id"] for c in result["chains"]} == {"rater_1", "rater_2"}
    assert [d["code"] for d in result["final_decisions"]] == ["A4-1", "A4-2"]
    assert result["final_decisions"][1]["source"] == "adjudicated"


def test_build_rater_chains_report_does_not_collide_when_rater_ids_match() -> None:
    """chains 是扁平列表，不按 rater_id 做外层 dict 键——两条链同 rater_id 时也不互相覆盖。"""
    chains_a = [_chain("rater_x", "A4-1", 3)]
    chains_b = [_chain("rater_x", "A4-2", 4)]

    result = feedback.build_rater_chains_report(chains_a, chains_b, [])

    assert len(result["chains"]) == 2
