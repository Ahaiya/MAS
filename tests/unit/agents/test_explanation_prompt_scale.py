from src.agents.prompt_builders import build_explanation_prompt
from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import FinalDimensionDecision
from src.providers.prompt_loader import PromptLoader


def test_explanation_prompt_uses_rubric_scale_max() -> None:
    dimension = {
        "dimension_id": "a1_1",
        "code": "A1-1",
        "name": "关键变量识别的全面性",
        "scale_ref": "ordinal_1_4",
    }
    scale = {
        "scale_id": "ordinal_1_4",
        "type": "ordinal",
        "min": 1,
        "max": 4,
    }
    rubric = RubricSnapshot(
        rubric_id="physics_experiment",
        rubric_version="test",
        rubric_name="物理实验",
        dimensions=[dimension],
        scales=[scale],
        dimension_by_id={"a1_1": dimension},
        dimension_by_code={"A1-1": dimension},
        scale_by_id={"ordinal_1_4": scale},
    )
    decision = FinalDimensionDecision(
        decision_id="decision-1",
        dimension_id="a1_1",
        final_score=create_score_representation(4, "ordinal_1_4"),
        primary_hypothesis_id="hyp-1",
        adjudication_id=None,
        evidence_span_ids=[],
        descriptor_refs=[],
        decision_confidence=0.9,
        decision_note=None,
    )

    template = PromptLoader().load("configs/prompts/explanation.yaml")

    prompt = build_explanation_prompt(
        decision=decision,
        evidence_spans=[],
        rubric=rubric,
        template=template,
        hypotheses=[],
    )

    assert "4 / 4" in prompt
    assert "4 / 5" not in prompt
