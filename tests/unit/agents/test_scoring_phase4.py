"""
Phase 4 验收测试 — build_scoring_prompt() 对齐模板变量

验证：
1. 当前维度的完整 anchors 出现在 prompt
2. prompt 不应混入其他维度 anchors
3. evidence_spans 为扁平列表，包含 span_id / chunk_id / quote / support_type
4. calibration_notes 按维度 code 从 per-dimension 列表精确匹配
5. calibration_notes 无 per-dimension 命中时回落到全局键
6. evidence_focus 出现在渲染后 prompt
7. prior_rater_context 在仲裁路径中正确渲染
8. scorer.run() 透传 evidence_focus
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.agents import scorer
from src.agents.prompt_builders import build_scoring_prompt
from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.evidence import (
    DimensionObservation,
    EvidenceScope,
    EvidenceSpan,
    FacetFinding,
    ObservationConfidence,
)
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import ScoreHypothesis
from src.providers.base import BaseProvider, LLMRequest, LLMResponse, ProviderCapability, TokenUsage
from src.providers.prompt_loader import PromptLoader, PromptTemplate


# ── 工具 ──────────────────────────────────────────────────────────────────────

def _load_scoring_template() -> PromptTemplate:
    root = Path(__file__).resolve().parents[3]
    return PromptLoader().load(root / "configs" / "prompts" / "scoring.yaml")


def _rubric() -> RubricSnapshot:
    scale_id = "ordinal_1_5"
    levels = [
        {"rank": 1, "summary": "待改进", "descriptors": ["基本无法完成"]},
        {"rank": 3, "summary": "合格", "descriptors": ["基本完成"]},
        {"rank": 5, "summary": "优秀", "descriptors": ["出色完成"]},
    ]
    other_levels = [
        {"rank": 1, "summary": "待改进", "descriptors": ["痛点1分锚点"]},
        {"rank": 5, "summary": "优秀", "descriptors": ["痛点5分锚点"]},
    ]
    dim = {
        "dimension_id": "a4_1",
        "code": "A4-1",
        "name": "用户群体识别的全面性",
        "scale_ref": scale_id,
        "observation_schema": {"required_facets": ["a4_1"]},
        "evidence_requirements": {"minimum_evidence_units": 1},
        "levels": levels,
    }
    other_dim = {
        "dimension_id": "a4_2",
        "code": "A4-2",
        "name": "痛点定位与需求精准度",
        "scale_ref": scale_id,
        "observation_schema": {"required_facets": ["a4_2"]},
        "evidence_requirements": {"minimum_evidence_units": 1},
        "levels": other_levels,
    }
    scale_entry = {"scale_id": scale_id, "type": "ordinal", "min": 1, "max": 5}
    return RubricSnapshot(
        rubric_id="task_a4",
        rubric_version="1.0",
        rubric_name="A4",
        dimensions=[dim, other_dim],
        scales=[scale_entry],
        indicator_description="分析用户需求、痛点与AI协同过程。",
        raw_task_rubric={
            "task_id": "a4",
            "task_name": "A4",
            "indicator_description": "分析用户需求、痛点与AI协同过程。",
            "dimensions": [
                {
                    "code": "A4-1",
                    "name": "用户群体识别的全面性",
                    "anchors": {
                        1: "基本无法完成",
                        3: "基本完成",
                        5: "出色完成",
                    },
                },
                {
                    "code": "A4-2",
                    "name": "痛点定位与需求精准度",
                    "anchors": {
                        1: "痛点1分锚点",
                        5: "痛点5分锚点",
                    },
                },
            ],
        },
        dimension_by_id={"a4_1": dim, "a4_2": other_dim},
        dimension_by_code={"A4-1": dim, "A4-2": other_dim},
        scale_by_id={scale_id: scale_entry},
    )


def _spans() -> List[EvidenceSpan]:
    return [
        EvidenceSpan(
            span_id="span-s01",
            document_id="doc-1",
            unit_id="unit-abc",
            text_quote="学生识别了三类老年用户群体。",
            start_offset=0,
            end_offset=15,
            scope=EvidenceScope.SPAN,
            dimension_id="a4_1",
            facet_ids=["a4_1"],
            extraction_note="test",
            support_type="supporting",
        ),
        EvidenceSpan(
            span_id="span-s02",
            document_id="doc-1",
            unit_id="unit-def",
            text_quote="仅泛泛提到老年人。",
            start_offset=20,
            end_offset=30,
            scope=EvidenceScope.SPAN,
            dimension_id="a4_1",
            facet_ids=["a4_1"],
            extraction_note="test",
            support_type="counter",
        ),
    ]


def _observation() -> DimensionObservation:
    return DimensionObservation(
        observation_id="obs-a4-1",
        document_id="doc-1",
        dimension_id="a4_1",
        supporting_span_ids=["span-s01"],
        counter_span_ids=["span-s02"],
        facet_findings=[
            FacetFinding(
                facet_id="a4_1",
                supporting_span_ids=["span-s01"],
                counter_span_ids=["span-s02"],
                finding_note="mixed evidence",
            ),
        ],
        observation_confidence=ObservationConfidence.MEDIUM,
        uncertainty_notes=[],
    )


def _task_scoring_context() -> dict:
    """Task-format scoring context (matching task_a4_context.yaml structure)."""
    return {
        "material_context": {"type": "conversation", "evidence_focus": "关注AI协同行为"},
        "score_anchors": [
            {"title": "参考样本A", "note": "3分标准示例"},
        ],
        "scoring_context": [
            {"code": "A4-1", "calibration_notes": "注意区分表层识别与深度分析"},
            {"code": "A4-2", "calibration_notes": "关注痛点优先级排序"},
        ],
    }


def _legacy_scoring_context() -> dict:
    """Legacy format (flat dict with top-level calibration_notes)."""
    return {
        "calibration_notes": "全局校准提示",
        "score_anchors": [],
    }


class _FixedProvider(BaseProvider):
    def __init__(self) -> None:
        self.last_prompt: Optional[str] = None

    @property
    def name(self) -> str:
        return "fixed"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.TEXT_COMPLETION, ProviderCapability.STRUCTURED_OUTPUT})

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.last_prompt = request.prompt
        payload = {
            "proposed_score": 3,
            "evidence_ids": ["span-s01"],
            "justification": "测试评分",
        }
        return LLMResponse(
            content=json.dumps(payload),
            structured_data=payload,
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            provider_name=self.name,
            model_id="fixed-v1",
        )


# ── Anchor 提取测试 ────────────────────────────────────────────────────────────

class TestAnchorExtraction:
    def test_current_dimension_anchors_are_in_prompt(self):
        template = _load_scoring_template()
        prompt = build_scoring_prompt(_observation(), _spans(), _rubric(), template)
        assert "## Rubric Anchors For This Observation Point" in prompt
        assert "5: 出色完成" in prompt
        assert "3: 基本完成" in prompt
        assert "1: 基本无法完成" in prompt

    def test_other_dimension_anchors_are_not_in_prompt(self):
        template = _load_scoring_template()
        prompt = build_scoring_prompt(_observation(), _spans(), _rubric(), template)
        assert "痛点5分锚点" not in prompt
        assert "痛点1分锚点" not in prompt


# ── Evidence Spans 扁平列表测试 ────────────────────────────────────────────────

class TestEvidenceSpansFlat:
    def test_supporting_span_in_prompt(self):
        template = _load_scoring_template()
        prompt = build_scoring_prompt(_observation(), _spans(), _rubric(), template)
        assert "span-s01" in prompt
        assert "学生识别了三类老年用户群体。" in prompt

    def test_counter_span_in_prompt(self):
        template = _load_scoring_template()
        prompt = build_scoring_prompt(_observation(), _spans(), _rubric(), template)
        assert "span-s02" in prompt
        assert "仅泛泛提到老年人。" in prompt

    def test_span_format_includes_chunk_id_and_support_type(self):
        template = _load_scoring_template()
        prompt = build_scoring_prompt(_observation(), _spans(), _rubric(), template)
        # Template renders: [span_id|chunk_id] "quote" (support_type)
        assert "unit-abc" in prompt
        assert "supporting" in prompt
        assert "counter" in prompt

    def test_no_duplicate_spans(self):
        """Same span_id in both supporting and counter of different facets → only once."""
        obs = DimensionObservation(
            observation_id="obs-dup",
            document_id="doc-1",
            dimension_id="a4_1",
            supporting_span_ids=["span-s01"],
            counter_span_ids=[],
            facet_findings=[
                FacetFinding(
                    facet_id="a4_1",
                    supporting_span_ids=["span-s01"],
                    counter_span_ids=["span-s01"],  # same span in both
                    finding_note="",
                ),
            ],
            observation_confidence=ObservationConfidence.MEDIUM,
            uncertainty_notes=[],
        )
        template = _load_scoring_template()
        prompt = build_scoring_prompt(obs, _spans(), _rubric(), template)
        assert prompt.count("span-s01") == 1


# ── Calibration Notes 测试 ────────────────────────────────────────────────────

class TestCalibrationNotes:
    def test_per_dimension_calibration_matched_by_code(self):
        template = _load_scoring_template()
        prompt = build_scoring_prompt(
            _observation(), _spans(), _rubric(), template,
            scoring_context=_task_scoring_context(),
        )
        assert "注意区分表层识别与深度分析" in prompt

    def test_per_dimension_other_code_not_matched(self):
        """A4-2 calibration_notes should NOT appear for a4_1 observation."""
        template = _load_scoring_template()
        prompt = build_scoring_prompt(
            _observation(), _spans(), _rubric(), template,
            scoring_context=_task_scoring_context(),
        )
        assert "关注痛点优先级排序" not in prompt

    def test_falls_back_to_global_when_no_per_dimension_match(self):
        template = _load_scoring_template()
        prompt = build_scoring_prompt(
            _observation(), _spans(), _rubric(), template,
            scoring_context=_legacy_scoring_context(),
        )
        assert "全局校准提示" in prompt

    def test_no_context_no_calibration_notes(self):
        template = _load_scoring_template()
        prompt = build_scoring_prompt(_observation(), _spans(), _rubric(), template)
        assert "Calibration Notes" not in prompt


# ── Evidence Focus 测试 ───────────────────────────────────────────────────────

class TestEvidenceFocus:
    def test_evidence_focus_in_prompt(self):
        template = _load_scoring_template()
        prompt = build_scoring_prompt(
            _observation(), _spans(), _rubric(), template,
            evidence_focus="重点关注学生的AI协同分析行为",
        )
        assert "重点关注学生的AI协同分析行为" in prompt

    def test_empty_evidence_focus_no_error(self):
        template = _load_scoring_template()
        prompt = build_scoring_prompt(_observation(), _spans(), _rubric(), template)
        assert isinstance(prompt, str)


# ── 仲裁路径测试 ──────────────────────────────────────────────────────────────

class TestAdjudicationPath:
    def _make_hyp(self, rater_id: str, score: int, rationale: str) -> ScoreHypothesis:
        return ScoreHypothesis(
            hypothesis_id=f"hyp-{rater_id}",
            observation_id="obs-a4-1",
            dimension_id="a4_1",
            rater_id=rater_id,
            score=create_score_representation(score, "ordinal_1_5"),
            descriptor_refs=[],
            evidence_span_ids=["span-s01"],
            rationale=rationale,
            confidence=0.7,
        )

    def test_prior_rater_context_in_prompt(self):
        template = _load_scoring_template()
        hyp1 = self._make_hyp("rater_1", 3, "判断依据一")
        hyp2 = self._make_hyp("rater_2", 4, "判断依据二")
        prompt = build_scoring_prompt(
            _observation(), _spans(), _rubric(), template,
            prior_hypotheses=[hyp1, hyp2],
        )
        assert "rater_1" in prompt
        assert "判断依据一" in prompt
        assert "rater_2" in prompt
        assert "判断依据二" in prompt

    def test_adjudication_header_present(self):
        template = _load_scoring_template()
        hyp = self._make_hyp("rater_1", 2, "评分理由")
        prompt = build_scoring_prompt(
            _observation(), _spans(), _rubric(), template,
            prior_hypotheses=[hyp],
        )
        assert "Adjudication" in prompt

    def test_no_prior_context_no_adjudication_section(self):
        template = _load_scoring_template()
        prompt = build_scoring_prompt(_observation(), _spans(), _rubric(), template)
        assert "Adjudication" not in prompt


# ── scorer.run() 集成测试 ─────────────────────────────────────────────────────

class TestScorerRunWithEvidenceFocus:
    def test_evidence_focus_passed_to_prompt(self):
        template = _load_scoring_template()
        provider = _FixedProvider()
        scorer.run(
            _observation(), _spans(), _rubric(), provider, template, "rater_1",
            evidence_focus="关注AI工具使用深度",
        )
        assert provider.last_prompt is not None
        assert "关注AI工具使用深度" in provider.last_prompt

    def test_run_without_evidence_focus_does_not_raise(self):
        template = _load_scoring_template()
        provider = _FixedProvider()
        hyp = scorer.run(
            _observation(), _spans(), _rubric(), provider, template, "rater_1",
        )
        assert hyp.score.canonical_score in range(1, 6)
