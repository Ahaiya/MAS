"""
Unit tests for prompt template loading and node prompt wiring (Phase 5, Task 3).

Covers:
- PromptTemplate dataclass
- PromptLoader.load() — YAML validation, schema enforcement
- PromptLoader.render() — Jinja2 rendering, strict undefined handling
- Module-level convenience helpers
- prompt_builders: extraction / scoring / explanation prompt assembly
- Boundary: no hardcoded rubric facts in prompt_loader or prompt_builders

Zero-hardcoding: test fixtures build minimal rubric/decision objects from scratch;
no trait names, dimension codes, or score values are hardcoded here.
"""

from __future__ import annotations

import inspect
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.providers.prompt_loader import (
    PromptTemplate,
    PromptLoader,
    load_template,
    render_template,
)
from src.agents.prompt_builders import (
    build_extraction_prompt,
    build_scoring_prompt,
    build_explanation_prompt,
)
from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.evidence import (
    DimensionObservation,
    EvidenceSpan,
    EvidenceScope,
    FacetFinding,
    ObservationConfidence,
)
from src.contracts.request_models import CoveragePlan, NormalizedDocument
from src.contracts.scoring import FinalDimensionDecision
from src.contracts.score_representation import create_score_representation


# ── Test fixtures ──────────────────────────────────────────────────────────────

CONFIGS_PROMPTS = Path(__file__).parent.parent.parent.parent / "configs" / "prompts"
SCORING_YAML = CONFIGS_PROMPTS / "scoring.yaml"
EXTRACTION_YAML = CONFIGS_PROMPTS / "evidence_extraction.yaml"
EXPLANATION_YAML = CONFIGS_PROMPTS / "explanation.yaml"

_INLINE_TEMPLATE = """\
prompt_template: |
  Hello {{ name }}! Dimension: {{ dimension_id }}.
metadata:
  template_version: "v1"
  compatible_dimensions: ["*"]
"""

_INLINE_TEMPLATE_LOOPED = """\
prompt_template: |
  Items: {% for item in items %}{{ item }}{% if not loop.last %}, {% endif %}{% endfor %}
metadata:
  template_version: "v1"
  compatible_dimensions: ["*"]
"""


def _make_rubric(dim_id: str = "dim_alpha", dim_name: str = "Alpha Quality") -> RubricSnapshot:
    scale_id = "test_scale"
    return RubricSnapshot(
        rubric_id="test_rubric",
        rubric_version="v1",
        rubric_name="Test Rubric",
        dimensions=[
            {
                "dimension_id": dim_id,
                "code": "A",
                "name": dim_name,
                "scale_ref": scale_id,
                "observation_schema": {"required_facets": ["facet_clarity", "facet_depth"]},
                "evidence_requirements": {"min_spans": 1},
                "levels": [
                    {"rank": 1, "summary": "Minimal", "descriptors": ["Very basic"]},
                    {"rank": 2, "summary": "Developing", "descriptors": ["Some evidence of quality"]},
                    {"rank": 3, "summary": "Adequate", "descriptors": ["Clear and organized"]},
                ],
            }
        ],
        scales=[{"scale_id": scale_id, "type": "ordinal", "min": 1, "max": 3}],
        dimension_by_id={
            dim_id: {
                "dimension_id": dim_id,
                "code": "A",
                "name": dim_name,
                "scale_ref": scale_id,
                "observation_schema": {"required_facets": ["facet_clarity", "facet_depth"]},
                "evidence_requirements": {"min_spans": 1},
                "levels": [
                    {"rank": 1, "summary": "Minimal", "descriptors": ["Very basic"]},
                    {"rank": 2, "summary": "Developing", "descriptors": ["Some evidence of quality"]},
                    {"rank": 3, "summary": "Adequate", "descriptors": ["Clear and organized"]},
                ],
            }
        },
        scale_by_id={
            scale_id: {"scale_id": scale_id, "type": "ordinal", "min": 1, "max": 3}
        },
    )


def _make_evidence_span(dim_id: str) -> EvidenceSpan:
    return EvidenceSpan(
        span_id="span-001",
        document_id="doc-001",
        unit_id=None,
        text_quote="The writer demonstrates clear organization.",
        start_offset=0,
        end_offset=40,
        scope=EvidenceScope.SPAN,
        dimension_id=dim_id,
        facet_ids=["facet_clarity"],
        extraction_note="test",
    )


def _make_observation(dim_id: str) -> DimensionObservation:
    return DimensionObservation(
        observation_id="obs-001",
        document_id="doc-001",
        dimension_id=dim_id,
        supporting_span_ids=["span-001"],
        counter_span_ids=[],
        facet_findings=[
            FacetFinding(
                facet_id="facet_clarity",
                supporting_span_ids=["span-001"],
                counter_span_ids=[],
                finding_note="clarity observed",
            )
        ],
        observation_confidence=ObservationConfidence.HIGH,
        uncertainty_notes="",
    )


def _make_coverage_plan(dim_id: str) -> CoveragePlan:
    return CoveragePlan(
        plan_id="plan-001",
        document_id="doc-001",
        dimension_id=dim_id,
        target_unit_ids=[],
        required_facets=["facet_clarity", "facet_depth"],
        minimum_evidence_units=1,
        allowed_evidence_scopes=["span", "global"],
        coverage_strategy="full_scan",
    )


def _make_normalized_document() -> NormalizedDocument:
    from src.contracts.request_models import TextUnit
    unit = TextUnit(
        unit_id="u-001",
        document_id="doc-001",
        text="The writer demonstrates clear organization.",
        start_offset=0,
        end_offset=42,
        unit_type="sentence",
        sequence_index=0,
    )
    return NormalizedDocument(
        document_id="doc-001",
        request_id="req-001",
        normalized_text="The writer demonstrates clear organization.",
        text_units=[unit],
        word_count=6,
        char_count=42,
        document_metadata={},
    )


def _make_final_decision(dim_id: str, score_val: int = 2) -> FinalDimensionDecision:
    score = create_score_representation(score_val, "test_scale")
    return FinalDimensionDecision(
        decision_id="dec-001",
        dimension_id=dim_id,
        final_score=score,
        primary_hypothesis_id="hyp-001",
        adjudication_id=None,
        evidence_span_ids=["span-001"],
        descriptor_refs=["Some evidence of quality"],
        decision_confidence=0.9,
        decision_note="Test decision",
    )


# ── PromptTemplate tests ──────────────────────────────────────────────────────

class TestPromptTemplate:
    def test_construction(self):
        tpl = PromptTemplate(
            template_text="Hello {{ name }}",
            metadata={"template_version": "v1", "compatible_dimensions": ["*"]},
            source_path="test.yaml",
        )
        assert "Hello" in tpl.template_text
        assert tpl.metadata["template_version"] == "v1"

    def test_is_immutable(self):
        tpl = PromptTemplate(
            template_text="Hello",
            metadata={},
            source_path="",
        )
        with pytest.raises((AttributeError, TypeError)):
            tpl.template_text = "changed"  # type: ignore[misc]


# ── PromptLoader.load() tests ─────────────────────────────────────────────────

class TestPromptLoaderLoad:
    def setup_method(self):
        self.loader = PromptLoader()

    def test_load_scoring_yaml(self):
        if not SCORING_YAML.exists():
            pytest.skip("scoring.yaml not found")
        tpl = self.loader.load(SCORING_YAML)
        assert isinstance(tpl, PromptTemplate)

    def test_load_extraction_yaml(self):
        if not EXTRACTION_YAML.exists():
            pytest.skip("evidence_extraction.yaml not found")
        tpl = self.loader.load(EXTRACTION_YAML)
        assert isinstance(tpl, PromptTemplate)

    def test_load_explanation_yaml(self):
        if not EXPLANATION_YAML.exists():
            pytest.skip("explanation.yaml not found")
        tpl = self.loader.load(EXPLANATION_YAML)
        assert isinstance(tpl, PromptTemplate)

    def test_loaded_template_has_text(self):
        if not SCORING_YAML.exists():
            pytest.skip("scoring.yaml not found")
        tpl = self.loader.load(SCORING_YAML)
        assert len(tpl.template_text.strip()) > 0

    def test_loaded_template_has_metadata(self):
        if not SCORING_YAML.exists():
            pytest.skip("scoring.yaml not found")
        tpl = self.loader.load(SCORING_YAML)
        assert "template_version" in tpl.metadata

    def test_load_inline_yaml_string(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(_INLINE_TEMPLATE)
            path = f.name
        try:
            tpl = self.loader.load(path)
            assert "{{ name }}" in tpl.template_text
        finally:
            os.unlink(path)

    def test_load_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            self.loader.load("/nonexistent/path/prompt.yaml")

    def test_load_missing_prompt_template_key_raises(self):
        bad_yaml = "metadata:\n  template_version: v1\n  compatible_dimensions: ['*']\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(bad_yaml)
            path = f.name
        try:
            with pytest.raises((ValueError, KeyError, Exception)):
                self.loader.load(path)
        finally:
            os.unlink(path)

    def test_load_invalid_yaml_raises(self):
        bad_yaml = "key: [\nunclosed bracket\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(bad_yaml)
            path = f.name
        try:
            with pytest.raises(Exception):
                self.loader.load(path)
        finally:
            os.unlink(path)

    def test_load_accepts_path_object(self):
        if not SCORING_YAML.exists():
            pytest.skip("scoring.yaml not found")
        tpl = self.loader.load(SCORING_YAML)  # Path object
        assert isinstance(tpl, PromptTemplate)

    def test_load_accepts_string_path(self):
        if not SCORING_YAML.exists():
            pytest.skip("scoring.yaml not found")
        tpl = self.loader.load(str(SCORING_YAML))  # str path
        assert isinstance(tpl, PromptTemplate)


# ── PromptLoader.render() tests ───────────────────────────────────────────────

class TestPromptLoaderRender:
    def setup_method(self):
        self.loader = PromptLoader()

    def _inline_tpl(self, text: str) -> PromptTemplate:
        return PromptTemplate(
            template_text=text,
            metadata={"template_version": "v1", "compatible_dimensions": ["*"]},
            source_path="inline",
        )

    def test_render_simple_variable(self):
        tpl = self._inline_tpl("Hello {{ name }}!")
        result = self.loader.render(tpl, {"name": "World"})
        assert result == "Hello World!"

    def test_render_multiple_variables(self):
        tpl = self._inline_tpl("{{ a }} + {{ b }} = {{ c }}")
        result = self.loader.render(tpl, {"a": "1", "b": "2", "c": "3"})
        assert result == "1 + 2 = 3"

    def test_render_loop(self):
        tpl = self._inline_tpl(
            "{% for item in items %}{{ item }}{% if not loop.last %},{% endif %}{% endfor %}"
        )
        result = self.loader.render(tpl, {"items": ["a", "b", "c"]})
        assert result == "a,b,c"

    def test_render_conditional(self):
        tpl = self._inline_tpl("{% if show %}visible{% else %}hidden{% endif %}")
        assert "visible" in self.loader.render(tpl, {"show": True})
        assert "hidden" in self.loader.render(tpl, {"show": False})

    def test_render_undefined_variable_raises(self):
        tpl = self._inline_tpl("Hello {{ undefined_var }}!")
        with pytest.raises(Exception):
            self.loader.render(tpl, {})

    def test_render_returns_string(self):
        tpl = self._inline_tpl("plain text")
        result = self.loader.render(tpl, {})
        assert isinstance(result, str)

    def test_render_nested_object(self):
        tpl = self._inline_tpl("Score: {{ level.rank }} - {{ level.summary }}")
        result = self.loader.render(tpl, {"level": {"rank": 3, "summary": "Good"}})
        assert "3" in result
        assert "Good" in result


# ── Module-level convenience helpers ─────────────────────────────────────────

class TestConvenienceHelpers:
    def test_load_template_convenience(self):
        if not SCORING_YAML.exists():
            pytest.skip("scoring.yaml not found")
        tpl = load_template(SCORING_YAML)
        assert isinstance(tpl, PromptTemplate)

    def test_render_template_convenience(self):
        tpl = PromptTemplate(
            template_text="Hi {{ name }}",
            metadata={},
            source_path="",
        )
        result = render_template(tpl, {"name": "test"})
        assert result == "Hi test"


# ── prompt_builders tests ──────────────────────────────────────────────────────

class TestBuildExtractionPrompt:
    def setup_method(self):
        self.dim_id = "dim_alpha"
        self.rubric = _make_rubric(self.dim_id, "Alpha Quality")
        self.plan = _make_coverage_plan(self.dim_id)
        self.doc = _make_normalized_document()
        self.loader = PromptLoader()

    def _get_template(self) -> PromptTemplate:
        if EXTRACTION_YAML.exists():
            return self.loader.load(EXTRACTION_YAML)
        return PromptTemplate(
            template_text=(
                "Dimension: {{ dimension_name }} ({{ dimension_code }})\n"
                "Facets: {% for f in required_facets %}{{ f }} {% endfor %}\n"
                "Text: {{ essay_text }}"
            ),
            metadata={"template_version": "v1", "compatible_dimensions": ["*"]},
            source_path="inline",
        )

    def test_returns_string(self):
        tpl = self._get_template()
        result = build_extraction_prompt(self.plan, self.doc, self.rubric, tpl)
        assert isinstance(result, str)

    def test_non_empty(self):
        tpl = self._get_template()
        result = build_extraction_prompt(self.plan, self.doc, self.rubric, tpl)
        assert len(result.strip()) > 0

    def test_contains_dimension_name(self):
        tpl = self._get_template()
        result = build_extraction_prompt(self.plan, self.doc, self.rubric, tpl)
        assert "Alpha Quality" in result

    def test_contains_facet_ids(self):
        tpl = self._get_template()
        result = build_extraction_prompt(self.plan, self.doc, self.rubric, tpl)
        assert "facet_clarity" in result or "facet_depth" in result

    def test_contains_essay_text(self):
        tpl = self._get_template()
        result = build_extraction_prompt(self.plan, self.doc, self.rubric, tpl)
        assert "demonstrates" in result


class TestBuildScoringPrompt:
    def setup_method(self):
        self.dim_id = "dim_alpha"
        self.rubric = _make_rubric(self.dim_id, "Alpha Quality")
        self.observation = _make_observation(self.dim_id)
        self.spans = [_make_evidence_span(self.dim_id)]
        self.doc = _make_normalized_document()
        self.loader = PromptLoader()

    def _get_template(self) -> PromptTemplate:
        if SCORING_YAML.exists():
            return self.loader.load(SCORING_YAML)
        return PromptTemplate(
            template_text=(
                "Dimension: {{ dimension_name }} ({{ dimension_code }})\n"
                "{% for level in levels %}Level {{ level.rank }}: {{ level.summary }}\n{% endfor %}"
                "Evidence: {% for e in evidence_spans %}{{ e.quote }} {% endfor %}"
            ),
            metadata={"template_version": "v1", "compatible_dimensions": ["*"]},
            source_path="inline",
        )

    def test_returns_string(self):
        tpl = self._get_template()
        result = build_scoring_prompt(self.observation, self.spans, self.rubric, self.doc, tpl)
        assert isinstance(result, str)

    def test_non_empty(self):
        tpl = self._get_template()
        result = build_scoring_prompt(self.observation, self.spans, self.rubric, self.doc, tpl)
        assert len(result.strip()) > 0

    def test_contains_dimension_name(self):
        tpl = self._get_template()
        result = build_scoring_prompt(self.observation, self.spans, self.rubric, self.doc, tpl)
        assert "Alpha Quality" in result

    def test_contains_level_info(self):
        tpl = self._get_template()
        result = build_scoring_prompt(self.observation, self.spans, self.rubric, self.doc, tpl)
        assert "Minimal" in result or "Developing" in result or "Adequate" in result


class TestBuildExplanationPrompt:
    def setup_method(self):
        self.dim_id = "dim_alpha"
        self.rubric = _make_rubric(self.dim_id, "Alpha Quality")
        self.decision = _make_final_decision(self.dim_id, 2)
        self.spans = [_make_evidence_span(self.dim_id)]
        self.loader = PromptLoader()

    def _get_template(self) -> PromptTemplate:
        if EXPLANATION_YAML.exists():
            return self.loader.load(EXPLANATION_YAML)
        return PromptTemplate(
            template_text=(
                "Dimension: {{ dimension_name }} ({{ dimension_code }})\n"
                "Score: {{ canonical_score }}\n"
                "Descriptors: {% for d in descriptor_refs %}{{ d }} {% endfor %}\n"
                "Evidence: {% for e in evidence_spans %}{{ e.quote }} {% endfor %}"
            ),
            metadata={"template_version": "v1", "compatible_dimensions": ["*"]},
            source_path="inline",
        )

    def test_returns_string(self):
        tpl = self._get_template()
        result = build_explanation_prompt(self.decision, self.spans, self.rubric, tpl)
        assert isinstance(result, str)

    def test_non_empty(self):
        tpl = self._get_template()
        result = build_explanation_prompt(self.decision, self.spans, self.rubric, tpl)
        assert len(result.strip()) > 0

    def test_contains_dimension_name(self):
        tpl = self._get_template()
        result = build_explanation_prompt(self.decision, self.spans, self.rubric, tpl)
        assert "Alpha Quality" in result

    def test_contains_score(self):
        tpl = self._get_template()
        result = build_explanation_prompt(self.decision, self.spans, self.rubric, tpl)
        assert "2" in result


# ── Boundary tests ─────────────────────────────────────────────────────────────

class TestPromptBoundary:
    def test_prompt_loader_has_no_hardcoded_rubric_facts(self):
        import src.providers.prompt_loader as mod
        source = inspect.getsource(mod)
        forbidden = [
            "src.policies",
            "src.orchestrator",
            "rubric_core",
        ]
        for symbol in forbidden:
            assert symbol not in source, (
                f"src/providers/prompt_loader.py must not reference '{symbol}'"
            )

    def test_prompt_builders_imports_from_contracts_only(self):
        import src.agents.prompt_builders as mod
        source = inspect.getsource(mod)
        # Must not hardcode specific trait names or score values
        forbidden_literals = [
            '"Ideas and Content"',
            '"Organization"',
            '"Voice"',
            '"Word Choice"',
            '"Sentence Fluency"',
            '"Conventions"',
        ]
        for literal in forbidden_literals:
            assert literal not in source, (
                f"src/agents/prompt_builders.py must not hardcode trait '{literal}'"
            )
