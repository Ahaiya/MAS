"""
Tests for Configuration Schema Validation (P1T3)

Covers:
- RubricFileSchema: Scale, Level, Dimension, RubricCore
- AdjudicationFileSchema: Trigger (threshold/pattern), OutputPolicy, ResolutionStrategy
- AggregationFileSchema: FormulaVariant, weights
- ExplanationFileSchema: RenderSection, CitationRules, DisplayAnnotationPolicy
- PromptFileSchema: prompt_template, metadata
- Validation of actual YAML config files
- Extra-field rejection across all schemas
"""

import pytest
import yaml
from pathlib import Path
from pydantic import ValidationError

from src.config.schema import (
    # Rubric
    ScaleSchema,
    LevelSchema,
    ObservationConfig,
    EvidenceRequirementsConfig,
    DimensionSchema,
    RubricValidationRuleSchema,
    RubricCoreSchema,
    RubricFileSchema,
    # Adjudication
    RaterConfigSchema,
    ThresholdSchema,
    TriggerPatternSchema,
    TriggerSchema,
    OutputPolicySchema,
    ResolutionStrategySchema,
    AdjudicationPolicySchema,
    AdjudicationFileSchema,
    # Aggregation
    AggregationOutputSchema,
    FormulaVariantSchema,
    AggregationPolicySchema,
    AggregationFileSchema,
    # Explanation
    ExplanationRequirementsSchema,
    RenderSectionSchema,
    CitationRulesSchema,
    DisplayAnnotationPolicySchema,
    OutputConstraintsSchema,
    ExplanationPolicySchema,
    ExplanationFileSchema,
    # Prompt
    PromptMetadataSchema,
    PromptFileSchema,
)

CONFIGS_ROOT = Path("configs")


# ── Rubric Schema Tests ───────────────────────────────────────────────────────


class TestScaleSchema:
    def test_valid_creation(self):
        scale = ScaleSchema(
            scale_id="ordinal_6",
            type="ordinal",
            min=1,
            max=6,
            canonical_score_type="integer",
            display_overlays_allowed=True,
        )
        assert scale.scale_id == "ordinal_6"
        assert scale.min == 1
        assert scale.max == 6

    def test_missing_required_field(self):
        with pytest.raises(ValidationError):
            ScaleSchema(scale_id="test", type="ordinal", min=1, max=6)
            # missing canonical_score_type, display_overlays_allowed

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            ScaleSchema(
                scale_id="ordinal_6",
                type="ordinal",
                min=1,
                max=6,
                canonical_score_type="integer",
                display_overlays_allowed=True,
                unexpected_field="bad",
            )

    def test_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            ScaleSchema(
                scale_id="ordinal_6",
                type="ordinal",
                min="not_an_int",
                max=6,
                canonical_score_type="integer",
                display_overlays_allowed=True,
            )


class TestLevelSchema:
    def test_valid_creation(self):
        level = LevelSchema(rank=3, summary="Moderate quality.", descriptors=["detail a", "detail b"])
        assert level.rank == 3
        assert len(level.descriptors) == 2

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            LevelSchema(rank=1, summary="Low", descriptors=[], extra_key="oops")

    def test_missing_required_field(self):
        with pytest.raises(ValidationError):
            LevelSchema(rank=1, summary="Low")  # missing descriptors


class TestObservationConfig:
    def test_valid_creation(self):
        obs = ObservationConfig(required_facets=["facet_a", "facet_b"])
        assert len(obs.required_facets) == 2

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            ObservationConfig(required_facets=[], unexpected="bad")


class TestEvidenceRequirementsConfig:
    def test_valid_creation(self):
        req = EvidenceRequirementsConfig(
            minimum_evidence_units=2,
            allowed_evidence_scope=["span", "global"],
            require_textual_grounding=True,
        )
        assert req.minimum_evidence_units == 2

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            EvidenceRequirementsConfig(
                minimum_evidence_units=2,
                allowed_evidence_scope=["span"],
                require_textual_grounding=True,
                extra="bad",
            )


class TestDimensionSchema:
    @pytest.fixture
    def valid_dimension(self):
        return {
            "dimension_id": "ideas_content",
            "code": "I",
            "name": "Ideas and Content",
            "scale_ref": "ordinal_6",
            "description": "Clarity and focus.",
            "observation_schema": {"required_facets": ["clarity_focus"]},
            "evidence_requirements": {
                "minimum_evidence_units": 2,
                "allowed_evidence_scope": ["span"],
                "require_textual_grounding": True,
            },
            "levels": [
                {"rank": 1, "summary": "Low", "descriptors": ["desc1"]},
                {"rank": 6, "summary": "High", "descriptors": ["desc2"]},
            ],
        }

    def test_valid_creation(self, valid_dimension):
        dim = DimensionSchema(**valid_dimension)
        assert dim.dimension_id == "ideas_content"
        assert dim.code == "I"
        assert len(dim.levels) == 2

    def test_extra_field_rejected(self, valid_dimension):
        valid_dimension["unexpected"] = "bad"
        with pytest.raises(ValidationError):
            DimensionSchema(**valid_dimension)

    def test_nested_observation_validated(self, valid_dimension):
        valid_dimension["observation_schema"]["extra_key"] = "bad"
        with pytest.raises(ValidationError):
            DimensionSchema(**valid_dimension)


class TestRubricCoreSchema:
    @pytest.fixture
    def minimal_rubric_core(self):
        return {
            "rubric_id": "test_rubric",
            "rubric_version": "v1",
            "rubric_name": "Test Rubric",
            "description": "A test rubric.",
            "scales": [
                {
                    "scale_id": "ordinal_6",
                    "type": "ordinal",
                    "min": 1,
                    "max": 6,
                    "canonical_score_type": "integer",
                    "display_overlays_allowed": True,
                }
            ],
            "dimensions": [
                {
                    "dimension_id": "dim1",
                    "code": "D",
                    "name": "Dimension One",
                    "scale_ref": "ordinal_6",
                    "description": "A dimension.",
                    "observation_schema": {"required_facets": ["facet_x"]},
                    "evidence_requirements": {
                        "minimum_evidence_units": 1,
                        "allowed_evidence_scope": ["span"],
                        "require_textual_grounding": True,
                    },
                    "levels": [{"rank": 1, "summary": "Low", "descriptors": ["d1"]}],
                }
            ],
        }

    def test_valid_creation(self, minimal_rubric_core):
        core = RubricCoreSchema(**minimal_rubric_core)
        assert core.rubric_id == "test_rubric"
        assert len(core.dimensions) == 1

    def test_validation_rules_optional(self, minimal_rubric_core):
        core = RubricCoreSchema(**minimal_rubric_core)
        assert core.validation_rules == []

    def test_extra_field_rejected(self, minimal_rubric_core):
        minimal_rubric_core["extra"] = "bad"
        with pytest.raises(ValidationError):
            RubricCoreSchema(**minimal_rubric_core)


class TestRubricFileSchema:
    def test_validates_actual_rubric_yaml(self):
        path = CONFIGS_ROOT / "rubrics/asap_set8_baseline.yaml"
        data = yaml.safe_load(path.read_text())
        schema = RubricFileSchema(**data)
        assert schema.schema_version == "2.0"
        assert schema.rubric_core.rubric_id == "asap_set8_writing_traits"
        assert len(schema.rubric_core.dimensions) == 6
        assert len(schema.rubric_core.scales) == 1

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            RubricFileSchema(schema_version="2.0", rubric_core={}, extra="bad")

    def test_missing_rubric_core_rejected(self):
        with pytest.raises(ValidationError):
            RubricFileSchema(schema_version="2.0")


# ── Adjudication Schema Tests ─────────────────────────────────────────────────


class TestThresholdSchema:
    def test_valid_creation(self):
        t = ThresholdSchema(operator=">", value=1)
        assert t.operator == ">"
        assert t.value == 1

    def test_float_value(self):
        t = ThresholdSchema(operator=">=", value=1.5)
        assert t.value == 1.5

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            ThresholdSchema(operator=">", value=1, extra="bad")


class TestTriggerSchema:
    def test_trigger_with_threshold(self):
        trigger = TriggerSchema(
            trigger_id="non_adjacent",
            type="score_distance",
            applies_to_dimensions=["*"],
            description="Non-adjacency rule.",
            threshold={"operator": ">", "value": 1},
            action="invoke_resolution",
            priority=1,
        )
        assert trigger.trigger_id == "non_adjacent"
        assert trigger.threshold is not None
        assert trigger.pattern is None

    def test_trigger_with_pattern(self):
        trigger = TriggerSchema(
            trigger_id="cusp_rule",
            type="pattern_match",
            applies_to_dimensions=["dim1", "dim2"],
            description="Cusp rule.",
            pattern={"one_rater_all_scores": [4, 4, 4, 4], "other_rater_has_one_3_and_three_4s": True},
            exclusions=["voice", "word_choice"],
            action="invoke_resolution",
            priority=2,
        )
        assert trigger.pattern is not None
        assert trigger.exclusions == ["voice", "word_choice"]

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            TriggerSchema(
                trigger_id="t",
                type="score_distance",
                applies_to_dimensions=["*"],
                description="x",
                action="invoke_resolution",
                priority=1,
                unexpected="bad",
            )


class TestAdjudicationFileSchema:
    def test_validates_actual_adjudication_yaml(self):
        path = CONFIGS_ROOT / "policies/adjudication/asap_set8_default.yaml"
        data = yaml.safe_load(path.read_text())
        schema = AdjudicationFileSchema(**data)
        assert schema.schema_version == "2.0"
        assert schema.adjudication_policy.policy_id == "asap_set8_default_adjudication"
        assert len(schema.adjudication_policy.triggers) == 2
        # First trigger uses threshold, second uses pattern
        assert schema.adjudication_policy.triggers[0].threshold is not None
        assert schema.adjudication_policy.triggers[1].pattern is not None

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            AdjudicationFileSchema(
                schema_version="2.0",
                adjudication_policy={},
                unexpected="bad",
            )


# ── Aggregation Schema Tests ──────────────────────────────────────────────────


class TestFormulaVariantSchema:
    def test_valid_creation(self):
        variant = FormulaVariantSchema(
            variant_id="without_resolution",
            applies_when="resolution_not_used",
            description="Use averages.",
            source_raters=["rater_1", "rater_2"],
            aggregation_method="average_per_trait_then_weighted_sum",
            weights={"dim_a": 2, "dim_b": 4},
            formula_representation="2*a + 4*b",
        )
        assert variant.variant_id == "without_resolution"
        assert variant.weights["dim_a"] == 2

    def test_zero_weights_allowed(self):
        variant = FormulaVariantSchema(
            variant_id="test",
            applies_when="always",
            description="Test.",
            source_raters=["rater_1"],
            aggregation_method="direct",
            weights={"dim_a": 0, "dim_b": 2},
            formula_representation="2*b",
        )
        assert variant.weights["dim_a"] == 0

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            FormulaVariantSchema(
                variant_id="test",
                applies_when="always",
                description="Test.",
                source_raters=[],
                aggregation_method="direct",
                weights={},
                formula_representation="x",
                unexpected="bad",
            )


class TestAggregationFileSchema:
    def test_validates_actual_aggregation_yaml(self):
        path = CONFIGS_ROOT / "policies/aggregation/asap_set8_composite.yaml"
        data = yaml.safe_load(path.read_text())
        schema = AggregationFileSchema(**data)
        assert schema.schema_version == "2.0"
        assert schema.aggregation_policy.policy_id == "asap_set8_composite"
        assert len(schema.aggregation_policy.composite_formula) == 2
        assert len(schema.aggregation_policy.outputs) == 2

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            AggregationFileSchema(
                schema_version="2.0",
                aggregation_policy={},
                unexpected="bad",
            )


# ── Explanation Schema Tests ──────────────────────────────────────────────────


class TestRenderSectionSchema:
    def test_with_fields(self):
        section = RenderSectionSchema(
            section_id="dimension_score",
            type="score_with_annotation",
            description="Show score.",
            fields=["dimension_name", "canonical_score"],
        )
        assert section.fields is not None
        assert section.requirements is None

    def test_with_requirements(self):
        section = RenderSectionSchema(
            section_id="rubric_alignment",
            type="descriptor_based_commentary",
            description="Commentary.",
            requirements=["must quote descriptors"],
            template_style="structured_paragraph",
        )
        assert section.requirements is not None

    def test_with_render_when(self):
        section = RenderSectionSchema(
            section_id="uncertainty_note",
            type="conditional_uncertainty",
            description="Low confidence note.",
            render_when=["observation_confidence_below_threshold"],
            content_style="caveat_statement",
        )
        assert section.render_when is not None

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            RenderSectionSchema(
                section_id="test",
                type="test",
                description="test",
                unexpected="bad",
            )


class TestExplanationFileSchema:
    def test_validates_actual_explanation_yaml(self):
        path = CONFIGS_ROOT / "policies/explanation/evidence_grounded_v1.yaml"
        data = yaml.safe_load(path.read_text())
        schema = ExplanationFileSchema(**data)
        assert schema.schema_version == "2.0"
        assert schema.explanation_policy.policy_id == "evidence_grounded_v1"
        assert len(schema.explanation_policy.render_sections) == 4
        assert schema.explanation_policy.requirements.require_evidence_links is True
        assert schema.explanation_policy.citation_rules.allow_synthetic_summary is False
        assert schema.explanation_policy.display_annotation_policy.allow_range_annotations is True

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            ExplanationFileSchema(
                schema_version="2.0",
                explanation_policy={},
                unexpected="bad",
            )


# ── Prompt Schema Tests ───────────────────────────────────────────────────────


class TestPromptFileSchema:
    def test_valid_creation(self):
        prompt = PromptFileSchema(
            prompt_template="Hello {{ name }}",
            metadata={"template_version": "v1", "compatible_dimensions": ["*"]},
        )
        assert "{{ name }}" in prompt.prompt_template
        assert prompt.metadata.template_version == "v1"

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            PromptFileSchema(
                prompt_template="Hello",
                metadata={"template_version": "v1", "compatible_dimensions": []},
                unexpected="bad",
            )

    def test_validates_evidence_extraction_prompt(self):
        path = CONFIGS_ROOT / "prompts/evidence_extraction.yaml"
        data = yaml.safe_load(path.read_text())
        schema = PromptFileSchema(**data)
        assert schema.metadata.template_version == "v1"
        assert "{{ dimension_name }}" in schema.prompt_template

    def test_validates_scoring_prompt(self):
        path = CONFIGS_ROOT / "prompts/scoring.yaml"
        data = yaml.safe_load(path.read_text())
        schema = PromptFileSchema(**data)
        assert schema.metadata.template_version == "v1"


# ── Cross-Config Validation ───────────────────────────────────────────────────


class TestAllActualConfigs:
    """Validate that every actual YAML config file passes its schema."""

    def test_rubric_file(self):
        path = CONFIGS_ROOT / "rubrics/asap_set8_baseline.yaml"
        data = yaml.safe_load(path.read_text())
        schema = RubricFileSchema(**data)
        # Each dimension must reference a known scale
        scale_ids = {s.scale_id for s in schema.rubric_core.scales}
        for dim in schema.rubric_core.dimensions:
            assert dim.scale_ref in scale_ids, f"Dimension {dim.dimension_id} refs unknown scale"

    def test_adjudication_file(self):
        path = CONFIGS_ROOT / "policies/adjudication/asap_set8_default.yaml"
        data = yaml.safe_load(path.read_text())
        AdjudicationFileSchema(**data)

    def test_aggregation_file(self):
        path = CONFIGS_ROOT / "policies/aggregation/asap_set8_composite.yaml"
        data = yaml.safe_load(path.read_text())
        AggregationFileSchema(**data)

    def test_explanation_file(self):
        path = CONFIGS_ROOT / "policies/explanation/evidence_grounded_v1.yaml"
        data = yaml.safe_load(path.read_text())
        ExplanationFileSchema(**data)

    def test_all_prompt_files(self):
        for prompt_path in (CONFIGS_ROOT / "prompts").glob("*.yaml"):
            data = yaml.safe_load(prompt_path.read_text())
            PromptFileSchema(**data)
