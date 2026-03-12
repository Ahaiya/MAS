"""
Configuration Schema Definitions (P1T3)

Pydantic v2 schemas for validating all runtime config YAML artifacts.
No business facts (trait names, score values, weights, formulas) are
hardcoded here — schemas only define structure and types.

Schemas:
- Rubric: ScaleSchema, LevelSchema, DimensionSchema, RubricCoreSchema, RubricFileSchema
- Adjudication: TriggerSchema, AdjudicationPolicySchema, AdjudicationFileSchema
- Aggregation: FormulaVariantSchema, AggregationPolicySchema, AggregationFileSchema
- Explanation: RenderSectionSchema, ExplanationPolicySchema, ExplanationFileSchema
- Prompt: PromptMetadataSchema, PromptFileSchema
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


# ── Rubric Core Schemas ───────────────────────────────────────────────────────


class ScaleSchema(BaseModel):
    """Defines a scoring scale (e.g., 1–6 ordinal)."""

    model_config = ConfigDict(extra="forbid")

    scale_id: str
    type: str
    min: int
    max: int
    canonical_score_type: str
    display_overlays_allowed: bool


class LevelSchema(BaseModel):
    """Defines a single score level with its descriptors."""

    model_config = ConfigDict(extra="forbid")

    rank: int
    summary: str
    descriptors: list[str]


class ObservationConfig(BaseModel):
    """Observation schema embedded in a dimension definition."""

    model_config = ConfigDict(extra="forbid")

    required_facets: list[str]


class EvidenceRequirementsConfig(BaseModel):
    """Evidence requirements embedded in a dimension definition."""

    model_config = ConfigDict(extra="forbid")

    minimum_evidence_units: int
    allowed_evidence_scope: list[str]
    require_textual_grounding: bool


class DimensionSchema(BaseModel):
    """A single rubric dimension with levels and evidence requirements."""

    model_config = ConfigDict(extra="forbid")

    dimension_id: str
    code: str
    name: str
    scale_ref: str
    description: str
    observation_schema: ObservationConfig
    evidence_requirements: EvidenceRequirementsConfig
    levels: list[LevelSchema]


class RubricValidationRuleSchema(BaseModel):
    """A rubric-level validation rule."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    type: str
    description: str


class RubricCoreSchema(BaseModel):
    """The rubric core: scales, dimensions, and validation rules."""

    model_config = ConfigDict(extra="forbid")

    rubric_id: str
    rubric_version: str
    rubric_name: str
    description: str
    scales: list[ScaleSchema]
    dimensions: list[DimensionSchema]
    validation_rules: list[RubricValidationRuleSchema] = []


class RubricFileSchema(BaseModel):
    """Top-level schema for a rubric YAML file."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    rubric_core: RubricCoreSchema


# ── Adjudication Policy Schemas ───────────────────────────────────────────────


class RaterConfigSchema(BaseModel):
    """Rater configuration: how many raters and their labels."""

    model_config = ConfigDict(extra="forbid")

    required_independent_scores: int
    rater_labels: list[str]
    optional_resolution_rater: int
    resolution_rater_label: str


class ThresholdSchema(BaseModel):
    """Numeric threshold for score-distance trigger."""

    model_config = ConfigDict(extra="forbid")

    operator: str
    value: int | float


class TriggerPatternSchema(BaseModel):
    """Pattern specification for cusp-rule trigger."""

    model_config = ConfigDict(extra="forbid")

    one_rater_all_scores: list[int]
    other_rater_has_one_3_and_three_4s: bool


class TriggerSchema(BaseModel):
    """An adjudication trigger (threshold-based or pattern-based)."""

    model_config = ConfigDict(extra="forbid")

    trigger_id: str
    type: str
    applies_to_dimensions: list[str]
    description: str
    threshold: ThresholdSchema | None = None
    pattern: TriggerPatternSchema | None = None
    exclusions: list[str] = []
    action: str
    priority: int


class OutputPolicySchema(BaseModel):
    """What to preserve in adjudication output."""

    model_config = ConfigDict(extra="forbid")

    preserve_all_candidates: bool
    preserve_trigger_reason: bool
    preserve_resolution_path: bool
    preserve_conflict_metadata: bool


class ResolutionStrategySchema(BaseModel):
    """How to resolve conflicts when adjudication is invoked."""

    model_config = ConfigDict(extra="forbid")

    default: str
    fallback_if_no_resolution: str


class AdjudicationPolicySchema(BaseModel):
    """Full adjudication policy: raters, triggers, output, resolution."""

    model_config = ConfigDict(extra="forbid")

    policy_id: str
    policy_version: str
    policy_name: str
    description: str
    raters: RaterConfigSchema
    triggers: list[TriggerSchema]
    output_policy: OutputPolicySchema
    resolution_strategy: ResolutionStrategySchema
    metadata: dict[str, Any] = {}


class AdjudicationFileSchema(BaseModel):
    """Top-level schema for an adjudication policy YAML file."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    adjudication_policy: AdjudicationPolicySchema


# ── Aggregation Policy Schemas ────────────────────────────────────────────────


class AggregationOutputSchema(BaseModel):
    """Defines a single output produced by the aggregation policy."""

    model_config = ConfigDict(extra="forbid")

    output_id: str
    type: str
    description: str
    always_produce: bool


class FormulaVariantSchema(BaseModel):
    """A composite-formula variant (e.g., with or without resolution)."""

    model_config = ConfigDict(extra="forbid")

    variant_id: str
    applies_when: str
    description: str
    source_raters: list[str]
    aggregation_method: str
    weights: dict[str, int | float]
    formula_representation: str


class AggregationPolicySchema(BaseModel):
    """Full aggregation policy: outputs, formula variants, notes."""

    model_config = ConfigDict(extra="forbid")

    policy_id: str
    policy_version: str
    policy_name: str
    description: str
    outputs: list[AggregationOutputSchema]
    composite_formula: list[FormulaVariantSchema]
    notes: list[str] = []
    metadata: dict[str, Any] = {}


class AggregationFileSchema(BaseModel):
    """Top-level schema for an aggregation policy YAML file."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    aggregation_policy: AggregationPolicySchema


# ── Explanation Policy Schemas ────────────────────────────────────────────────


class ExplanationRequirementsSchema(BaseModel):
    """Requirements that all explanations must meet."""

    model_config = ConfigDict(extra="forbid")

    require_descriptor_alignment: bool
    require_evidence_links: bool
    forbid_unreferenced_claims: bool
    require_score_citation: bool


class RenderSectionSchema(BaseModel):
    """A single section in the explanation render pipeline."""

    model_config = ConfigDict(extra="forbid")

    section_id: str
    type: str
    description: str
    fields: list[str] | None = None
    requirements: list[str] | None = None
    render_when: list[str] | None = None
    content_style: str | None = None
    template_style: str | None = None


class CitationRulesSchema(BaseModel):
    """Rules governing how citations appear in explanations."""

    model_config = ConfigDict(extra="forbid")

    descriptor_citation_style: str
    evidence_citation_style: str
    min_citations_per_dimension: int
    allow_synthetic_summary: bool


class DisplayAnnotationPolicySchema(BaseModel):
    """Controls which display annotation forms are permitted."""

    model_config = ConfigDict(extra="forbid")

    allow_canonical_only: bool
    allow_range_annotations: bool
    allow_quality_labels: bool
    canonical_score_field: str
    display_annotation_field: str


class OutputConstraintsSchema(BaseModel):
    """Hard constraints on explanation output."""

    model_config = ConfigDict(extra="forbid")

    max_commentary_length_per_dimension: int
    require_evidence_score_chain: bool
    forbid_free_form_generation: bool


class ExplanationPolicySchema(BaseModel):
    """Full explanation policy: requirements, sections, citation, display."""

    model_config = ConfigDict(extra="forbid")

    policy_id: str
    policy_version: str
    policy_name: str
    description: str
    requirements: ExplanationRequirementsSchema
    render_sections: list[RenderSectionSchema]
    citation_rules: CitationRulesSchema
    display_annotation_policy: DisplayAnnotationPolicySchema
    output_constraints: OutputConstraintsSchema
    metadata: dict[str, Any] = {}


class ExplanationFileSchema(BaseModel):
    """Top-level schema for an explanation policy YAML file."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    explanation_policy: ExplanationPolicySchema


# ── Prompt Template Schemas ───────────────────────────────────────────────────


class PromptMetadataSchema(BaseModel):
    """Metadata for a prompt template file."""

    model_config = ConfigDict(extra="forbid")

    template_version: str
    compatible_dimensions: list[str]


class PromptFileSchema(BaseModel):
    """Top-level schema for a prompt template YAML file."""

    model_config = ConfigDict(extra="forbid")

    prompt_template: str
    metadata: PromptMetadataSchema
