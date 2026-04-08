"""
Phase 5 验收测试 — build_explanation_prompt() 与 feedback.py 对齐模板变量

验证：
1. final_score = decision.final_score.canonical_score
2. was_adjudicated=False 时：justification_1=rater_1，justification_2=rater_2
3. was_adjudicated=True 时：justification_1=rater_3
4. evidence_spans 扁平列表 [{span_id, quote, support_type}]，仅含 decision.evidence_span_ids
5. evidence_focus / audience 出现在渲染后 prompt
6. feedback._render_commentary() 正确解析 JSON {"feedback": "..."}
7. 非 JSON 响应回落到原始文本
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.agents import feedback
from src.agents.prompt_builders import build_explanation_prompt
from src.contracts.artifact_bundle import PolicySnapshot, RubricSnapshot
from src.contracts.evidence import (
    DimensionObservation,
    EvidenceScope,
    EvidenceSpan,
    FacetFinding,
    ObservationConfidence,
)
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import FinalDimensionDecision, ScoreHypothesis
from src.providers.base import BaseProvider, LLMRequest, LLMResponse, ProviderCapability, TokenUsage
from src.providers.prompt_loader import PromptLoader, PromptTemplate


# ── 工具 ──────────────────────────────────────────────────────────────────────

def _load_explanation_template() -> PromptTemplate:
    root = Path(__file__).resolve().parents[3]
    return PromptLoader().load(root / "configs" / "prompts" / "explanation.yaml")


def _inline_template(text: str) -> PromptTemplate:
    return PromptTemplate(
        template_text=text,
        metadata={"template_version": "test"},
        source_path="inline",
    )


def _rubric() -> RubricSnapshot:
    scale_id = "ordinal_1_5"
    dim = {
        "dimension_id": "a4_1",
        "code": "A4-1",
        "name": "用户群体识别的全面性",
        "scale_ref": scale_id,
        "observation_schema": {"required_facets": ["a4_1"]},
        "levels": [
            {"rank": 1, "summary": "待改进", "descriptors": []},
            {"rank": 5, "summary": "优秀", "descriptors": []},
        ],
    }
    scale = {"scale_id": scale_id, "type": "ordinal", "min": 1, "max": 5}
    return RubricSnapshot(
        rubric_id="task_a4",
        rubric_version="1.0",
        rubric_name="A4",
        dimensions=[dim],
        scales=[scale],
        dimension_by_id={"a4_1": dim},
        dimension_by_code={"A4-1": dim},
        scale_by_id={scale_id: scale},
    )


def _policy() -> PolicySnapshot:
    return PolicySnapshot(
        adjudication_policy={"triggers": []},
        aggregation_policy={},
        explanation_policy={
            "policy_id": "exp-phase5",
            "requirements": {
                "require_descriptor_alignment": True,
                "require_evidence_links": True,
                "require_score_citation": True,
            },
            "citation_rules": {"min_citations_per_dimension": 1},
            "output_constraints": {
                "max_commentary_length_per_dimension": 500,
                "require_evidence_score_chain": True,
                "low_confidence_threshold": 0.5,
            },
            "render_sections": [],
        },
        policy_version="phase5-v1",
    )


def _spans() -> List[EvidenceSpan]:
    return [
        EvidenceSpan(
            span_id="span-e01",
            document_id="doc-1",
            unit_id="unit-1",
            text_quote="学生清晰识别了三类老年用户群体，并进行了深入对比。",
            start_offset=0, end_offset=25,
            scope=EvidenceScope.SPAN,
            dimension_id="a4_1",
            facet_ids=["a4_1"],
            extraction_note="test",
            support_type="supporting",
        ),
        EvidenceSpan(
            span_id="span-e02",
            document_id="doc-1",
            unit_id="unit-2",
            text_quote="仅提到'老年人'一个笼统群体。",
            start_offset=30, end_offset=45,
            scope=EvidenceScope.SPAN,
            dimension_id="a4_1",
            facet_ids=["a4_1"],
            extraction_note="test",
            support_type="counter",
        ),
        EvidenceSpan(
            span_id="span-e03",
            document_id="doc-1",
            unit_id="unit-3",
            text_quote="该片段与本维度无关。",
            start_offset=50, end_offset=60,
            scope=EvidenceScope.SPAN,
            dimension_id="a4_1",
            facet_ids=["a4_1"],
            extraction_note="test",
            support_type="neutral",
        ),
    ]


def _observation() -> DimensionObservation:
    return DimensionObservation(
        observation_id="obs-a4-1",
        document_id="doc-1",
        dimension_id="a4_1",
        supporting_span_ids=["span-e01"],
        counter_span_ids=["span-e02"],
        facet_findings=[
            FacetFinding(
                facet_id="a4_1",
                supporting_span_ids=["span-e01"],
                counter_span_ids=["span-e02"],
                finding_note="",
            )
        ],
        observation_confidence=ObservationConfidence.HIGH,
        uncertainty_notes=[],
    )


def _decision(adjudication_id: Optional[str] = None) -> FinalDimensionDecision:
    return FinalDimensionDecision(
        decision_id="dec-a4-1",
        dimension_id="a4_1",
        final_score=create_score_representation(4, "ordinal_1_5"),
        primary_hypothesis_id="hyp-r1",
        adjudication_id=adjudication_id,
        evidence_span_ids=["span-e01", "span-e02"],  # span-e03 不在列表
        descriptor_refs=[],
        decision_confidence=0.85,
        decision_note="",
    )


def _make_hyp(rater_id: str, rationale: str) -> ScoreHypothesis:
    return ScoreHypothesis(
        hypothesis_id=f"hyp-{rater_id}",
        observation_id="obs-a4-1",
        dimension_id="a4_1",
        rater_id=rater_id,
        score=create_score_representation(4, "ordinal_1_5"),
        descriptor_refs=[],
        evidence_span_ids=["span-e01"],
        rationale=rationale,
        confidence=0.8,
    )


class _CaptureProvider(BaseProvider):
    def __init__(self, response_content: str = '{"feedback": "测试反馈文本"}') -> None:
        self.response_content = response_content
        self.last_prompt: Optional[str] = None

    @property
    def name(self) -> str:
        return "capture"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.TEXT_COMPLETION})

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.last_prompt = request.prompt
        return LLMResponse(
            content=self.response_content,
            structured_data=None,
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            provider_name=self.name,
            model_id="capture-v1",
        )


# ── final_score 测试 ──────────────────────────────────────────────────────────

class TestFinalScore:
    def test_final_score_in_prompt(self):
        template = _load_explanation_template()
        prompt = build_explanation_prompt(
            _decision(), _spans(), _rubric(), template
        )
        assert "4" in prompt  # final_score = 4

    def test_final_score_uses_canonical_score(self):
        template = _inline_template("SCORE={{ final_score }}")
        prompt = build_explanation_prompt(
            _decision(), _spans(), _rubric(), template
        )
        assert "SCORE=4" in prompt


# ── justification 测试 ────────────────────────────────────────────────────────

class TestJustifications:
    def test_non_adjudicated_justification1_from_rater1(self):
        template = _inline_template("J1={{ justification_1 }}|J2={{ justification_2 }}")
        hyps = [_make_hyp("rater_1", "R1评分理由"), _make_hyp("rater_2", "R2评分理由")]
        prompt = build_explanation_prompt(
            _decision(), _spans(), _rubric(), template, hypotheses=hyps
        )
        assert "J1=R1评分理由" in prompt
        assert "J2=R2评分理由" in prompt

    def test_adjudicated_justification1_from_rater3(self):
        template = _inline_template("J1={{ justification_1 }}|ADJ={{ was_adjudicated }}")
        hyps = [
            _make_hyp("rater_1", "R1评分"),
            _make_hyp("rater_2", "R2评分"),
            _make_hyp("rater_3", "R3仲裁理由"),
        ]
        prompt = build_explanation_prompt(
            _decision(adjudication_id="adj-001"),
            _spans(), _rubric(), template, hypotheses=hyps,
        )
        assert "J1=R3仲裁理由" in prompt
        assert "ADJ=True" in prompt

    def test_adjudicated_justification2_empty(self):
        template = _inline_template("J2={{ justification_2 }}")
        hyps = [_make_hyp("rater_3", "仲裁理由")]
        prompt = build_explanation_prompt(
            _decision(adjudication_id="adj-001"),
            _spans(), _rubric(), template, hypotheses=hyps,
        )
        assert "J2=" in prompt
        assert "仲裁" not in prompt.split("J2=")[1]  # J2 应为空

    def test_no_hypotheses_justifications_empty(self):
        template = _inline_template("J1={{ justification_1 }}|J2={{ justification_2 }}")
        prompt = build_explanation_prompt(
            _decision(), _spans(), _rubric(), template
        )
        assert "J1=|" in prompt  # 均为空

    def test_real_template_was_adjudicated_path(self):
        """仲裁路径：模板渲染 'Rater 1:' 不出现，只渲染单一 justification_1。"""
        template = _load_explanation_template()
        hyps = [_make_hyp("rater_3", "综合两评委意见，最终定为4分。")]
        prompt = build_explanation_prompt(
            _decision(adjudication_id="adj-001"),
            _spans(), _rubric(), template,
            hypotheses=hyps,
        )
        assert "综合两评委意见" in prompt
        assert "Rater 1:" not in prompt  # 仲裁路径不显示 Rater 1/2 标签

    def test_real_template_non_adjudicated_path(self):
        """非仲裁路径：模板渲染 'Rater 1:'/'Rater 2:'。"""
        template = _load_explanation_template()
        hyps = [_make_hyp("rater_1", "R1判断"), _make_hyp("rater_2", "R2判断")]
        prompt = build_explanation_prompt(
            _decision(), _spans(), _rubric(), template, hypotheses=hyps,
        )
        assert "Rater 1:" in prompt
        assert "Rater 2:" in prompt


# ── evidence_spans 扁平列表测试 ────────────────────────────────────────────────

class TestEvidenceSpansFlat:
    def test_only_decision_span_ids_included(self):
        """decision.evidence_span_ids = [e01, e02]；e03 不在其中，不出现。"""
        template = _load_explanation_template()
        prompt = build_explanation_prompt(
            _decision(), _spans(), _rubric(), template
        )
        assert "span-e01" in prompt
        assert "span-e02" in prompt
        assert "span-e03" not in prompt

    def test_span_quote_in_prompt(self):
        template = _load_explanation_template()
        prompt = build_explanation_prompt(
            _decision(), _spans(), _rubric(), template
        )
        assert "学生清晰识别了三类老年用户群体" in prompt


# ── evidence_focus / audience 测试 ────────────────────────────────────────────

class TestEvidenceFocusAudience:
    def test_evidence_focus_in_prompt(self):
        template = _load_explanation_template()
        prompt = build_explanation_prompt(
            _decision(), _spans(), _rubric(), template,
            evidence_focus="聚焦学生的用户细分深度",
        )
        assert "聚焦学生的用户细分深度" in prompt

    def test_audience_evaluator_default(self):
        template = _load_explanation_template()
        prompt = build_explanation_prompt(
            _decision(), _spans(), _rubric(), template
        )
        # evaluator 分支的提示语
        assert "evidence-grounded" in prompt

    def test_audience_student_path(self):
        template = _load_explanation_template()
        prompt = build_explanation_prompt(
            _decision(), _spans(), _rubric(), template,
            audience="student",
        )
        assert "encouraging" in prompt


# ── JSON 响应解析测试 ──────────────────────────────────────────────────────────

class TestJsonResponseParsing:
    def _run_feedback(self, response_content: str) -> str:
        provider = _CaptureProvider(response_content=response_content)
        out = feedback.run(
            decisions=[_decision()],
            observations=[_observation()],
            spans=_spans(),
            hypotheses=[_make_hyp("rater_1", "R1理由")],
            rubric=_rubric(),
            policy=_policy(),
            provider=provider,
            template=_load_explanation_template(),
        )
        return out["dimensions"]["a4_1"]["feedback_text"]

    def test_json_feedback_extracted(self):
        text = self._run_feedback('{"feedback": "这是解析出的反馈文本"}')
        assert text == "这是解析出的反馈文本"

    def test_non_json_falls_back_to_raw_text(self):
        raw = "这是非JSON的原始文本反馈"
        text = self._run_feedback(raw)
        assert text == raw

    def test_json_without_feedback_key_falls_back(self):
        text = self._run_feedback('{"other_key": "值"}')
        assert text == '{"other_key": "值"}'

    def test_empty_response_uses_fallback(self):
        text = self._run_feedback("")
        # 空响应 → fallback_text（来自 render_dimension_explanation）
        assert isinstance(text, str)
        assert len(text) > 0


# ── feedback.run() 新参数透传测试 ─────────────────────────────────────────────

class TestFeedbackRunNewParams:
    def test_evidence_focus_passed_to_prompt(self):
        provider = _CaptureProvider()
        feedback.run(
            decisions=[_decision()],
            observations=[_observation()],
            spans=_spans(),
            hypotheses=[_make_hyp("rater_1", "理由")],
            rubric=_rubric(),
            policy=_policy(),
            provider=provider,
            template=_load_explanation_template(),
            evidence_focus="重点关注AI协同分析行为",
        )
        assert provider.last_prompt is not None
        assert "重点关注AI协同分析行为" in provider.last_prompt

    def test_audience_student_passed_to_prompt(self):
        provider = _CaptureProvider()
        feedback.run(
            decisions=[_decision()],
            observations=[_observation()],
            spans=_spans(),
            hypotheses=[_make_hyp("rater_1", "理由")],
            rubric=_rubric(),
            policy=_policy(),
            provider=provider,
            template=_load_explanation_template(),
            audience="student",
        )
        assert provider.last_prompt is not None
        assert "encouraging" in provider.last_prompt
