"""
Phase 3 验收测试 — build_extraction_prompt() 对齐模板变量

验证：
1. 当前维度的完整 anchors 出现在 prompt
2. prompt 不应混入其他维度 anchors
3. evidence_focus 出现在渲染后 prompt
4. extractor.run() 透传 evidence_focus
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.agents import extractor
from src.agents.prompt_builders import build_extraction_prompt
from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.request_models import CoveragePlan, NormalizedDocument, TextUnit
from src.providers.base import BaseProvider, LLMRequest, LLMResponse, ProviderCapability, TokenUsage
from src.providers.prompt_loader import PromptLoader, PromptTemplate


# ── 工具 ──────────────────────────────────────────────────────────────────────

def _load_extraction_template() -> PromptTemplate:
    root = Path(__file__).resolve().parents[3]
    return PromptLoader().load(root / "configs" / "prompts" / "evidence_extraction.yaml")


def _inline_template(text: str) -> PromptTemplate:
    return PromptTemplate(
        template_text=text,
        metadata={"template_version": "test"},
        source_path="inline",
    )


def _rubric(scale_min: int = 1, scale_max: int = 5) -> RubricSnapshot:
    scale_id = "ordinal_1_5"
    levels = [
        {"rank": 1, "summary": "待改进", "descriptors": ["基本无法完成"]},
        {"rank": 2, "summary": "接近合格", "descriptors": ["初步尝试"]},
        {"rank": 3, "summary": "合格", "descriptors": ["基本完成"]},
        {"rank": 4, "summary": "良好", "descriptors": ["较好完成"]},
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
    scale_entry = {"scale_id": scale_id, "type": "ordinal", "min": scale_min, "max": scale_max}
    return RubricSnapshot(
        rubric_id="task_a4",
        rubric_version="1.0",
        rubric_name="A4 用户需求与痛点分析",
        dimensions=[dim, other_dim],
        scales=[scale_entry],
        indicator_description="分析老年用户、利益相关方与场景约束。",
        raw_task_rubric={
            "task_id": "a4",
            "task_name": "A4 用户需求与痛点分析",
            "indicator_description": "分析老年用户、利益相关方与场景约束。",
            "dimensions": [
                {
                    "code": "A4-1",
                    "name": "用户群体识别的全面性",
                    "anchors": {
                        1: "基本无法完成",
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


def _plan() -> CoveragePlan:
    return CoveragePlan(
        plan_id="plan-phase3",
        document_id="doc-phase3",
        dimension_id="a4_1",
        coverage_strategy="full_scan",
        target_unit_ids=[],
        required_facets=["a4_1"],
        minimum_evidence_units=1,
        allowed_evidence_scopes=["span", "global"],
    )


def _document(text: str = "学生分析了老年用户的痛点。AI辅助分析了三类用户群体。") -> NormalizedDocument:
    unit = TextUnit(
        unit_id="unit-0",
        document_id="doc-phase3",
        text=text,
        start_offset=0,
        end_offset=len(text),
        unit_type="chunk",
        sequence_index=0,
        chunk_title="全文",
        chunk_method="llm_semantic",
    )
    return NormalizedDocument(
        document_id="doc-phase3",
        request_id="req-phase3",
        normalized_text=text,
        text_units=[unit],
        char_count=len(text),
        word_count=len(text.split()),
        document_type="conversation",
        token_estimate=10,
        document_metadata={},
    )


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
        return LLMResponse(
            content='{"evidence_spans": []}',
            structured_data={"evidence_spans": []},
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            provider_name=self.name,
            model_id="fixed-v1",
        )


# ── build_extraction_prompt() 单元测试 ────────────────────────────────────────

class TestBuildExtractionPrompt:
    def test_current_dimension_anchors_are_in_prompt(self):
        template = _load_extraction_template()
        prompt = build_extraction_prompt(
            _plan(), _document(), _rubric(), template
        )
        assert "## Rubric Anchors For This Observation Point" in prompt
        assert "5: 出色完成" in prompt
        assert "2: 初步尝试" in prompt
        assert "1: 基本无法完成" in prompt

    def test_other_dimension_anchors_are_not_in_prompt(self):
        template = _load_extraction_template()
        prompt = build_extraction_prompt(
            _plan(), _document(), _rubric(), template
        )
        assert "痛点5分锚点" not in prompt
        assert "痛点1分锚点" not in prompt

    def test_evidence_focus_in_prompt(self):
        template = _load_extraction_template()
        prompt = build_extraction_prompt(
            _plan(), _document(), _rubric(), template,
            evidence_focus="关注学生是否识别了多类老年用户群体",
        )
        assert "关注学生是否识别了多类老年用户群体" in prompt

    def test_dimension_name_in_prompt(self):
        template = _load_extraction_template()
        prompt = build_extraction_prompt(
            _plan(), _document(), _rubric(), template
        )
        assert "用户群体识别的全面性" in prompt

    def test_chunk_text_in_prompt(self):
        text = "unique_marker_学生分析内容_xyz"
        template = _load_extraction_template()
        prompt = build_extraction_prompt(
            _plan(), _document(text), _rubric(), template
        )
        assert "unique_marker_学生分析内容_xyz" in prompt

    def test_no_undefined_error_without_evidence_focus(self):
        """模板有 evidence_focus 变量；默认空字符串时不报错。"""
        template = _load_extraction_template()
        prompt = build_extraction_prompt(
            _plan(), _document(), _rubric(), template
        )
        assert isinstance(prompt, str)

    def test_anchor_extraction_with_custom_scale(self):
        """3 分制量表：rank=3 为优秀，rank=1 为待改进。"""
        scale_id = "ordinal_1_3"
        levels = [
            {"rank": 1, "summary": "基础", "descriptors": []},
            {"rank": 2, "summary": "中等", "descriptors": []},
            {"rank": 3, "summary": "优异", "descriptors": []},
        ]
        dim = {
            "dimension_id": "dim_x",
            "code": "X1",
            "name": "测试维度",
            "scale_ref": scale_id,
            "observation_schema": {"required_facets": ["dim_x"]},
            "evidence_requirements": {"minimum_evidence_units": 1},
            "levels": levels,
        }
        scale_entry = {"scale_id": scale_id, "type": "ordinal", "min": 1, "max": 3}
        rubric = RubricSnapshot(
            rubric_id="test",
            rubric_version="1.0",
            rubric_name="Test",
            dimensions=[dim],
            scales=[scale_entry],
            dimension_by_id={"dim_x": dim},
            dimension_by_code={"X1": dim},
            scale_by_id={scale_id: scale_entry},
        )
        plan = CoveragePlan(
            plan_id="p", document_id="d", dimension_id="dim_x",
            coverage_strategy="full_scan", target_unit_ids=[],
            required_facets=["dim_x"], minimum_evidence_units=1,
            allowed_evidence_scopes=["span", "global"],
        )
        template = _inline_template(
            "{% for anchor in dimension_anchors %}{{ anchor.rank }}={{ anchor.text }} {% endfor %}"
        )
        prompt = build_extraction_prompt(plan, _document(), rubric, template)
        assert "3=优异" in prompt
        assert "1=基础" in prompt


# ── extractor.run() 集成测试 ──────────────────────────────────────────────────

class TestExtractorRunWithEvidenceFocus:
    def test_evidence_focus_passed_to_prompt(self):
        template = _load_extraction_template()
        provider = _FixedProvider()
        extractor.run(
            _plan(),
            _document(),
            _rubric(),
            provider,
            template,
            evidence_focus="关注AI辅助分析行为",
        )
        assert provider.last_prompt is not None
        assert "关注AI辅助分析行为" in provider.last_prompt

    def test_run_without_evidence_focus_does_not_raise(self):
        template = _load_extraction_template()
        provider = _FixedProvider()
        spans = extractor.run(_plan(), _document(), _rubric(), provider, template)
        assert isinstance(spans, list)
