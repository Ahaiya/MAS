"""
配置 Schema 定义，负责约束所有运行期 YAML 工件的结构与字段。

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

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    facet_descriptions: dict[str, str] = Field(default_factory=dict)


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
    metadata: dict[str, Any] = Field(default_factory=dict)


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
    """Pattern specification for cross-dimension adjudication triggers."""

    model_config = ConfigDict(extra="forbid")

    one_rater_all_scores: list[int] = []
    other_rater_has_one_3_and_three_4s: bool = False
    score_gap: int | None = None
    min_matching_dimensions: int | None = None
    require_same_direction: bool = False


class TriggerSchema(BaseModel):
    """An adjudication trigger (threshold-based or cross-dimension pattern-based)."""

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
    re_score_scope: Literal["all_dimensions", "conflicted_only"] = "all_dimensions"


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
    low_confidence_threshold: float = 0.5


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


# ── Scoring Context Schemas ───────────────────────────────────────────────────


class ScoreAnchorDimensionSchema(BaseModel):
    """Per-dimension anchor annotation used in scoring context."""

    model_config = ConfigDict(extra="forbid")

    score: int
    note: str


class ScoreAnchorSchema(BaseModel):
    """A single scored anchor example used for calibration."""

    model_config = ConfigDict(extra="forbid")

    anchor_id: str
    target_score: int
    title: str
    per_dimension: dict[str, ScoreAnchorDimensionSchema] = Field(default_factory=dict)


class ScoringContextSchema(BaseModel):
    """Dataset-level scoring context injected into scoring prompts."""

    model_config = ConfigDict(extra="forbid")

    context_id: str
    role_description: str
    dataset_notes: str = ""
    score_anchors: list[ScoreAnchorSchema] = Field(default_factory=list)
    calibration_notes: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScoringContextFileSchema(BaseModel):
    """Top-level schema for scoring context YAML files."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    scoring_context: ScoringContextSchema


# ── Chunking Policy Schemas ───────────────────────────────────────────────────


class ChunkingDocumentProcessingSchema(BaseModel):
    """Document-level chunking thresholds and hints."""

    model_config = ConfigDict(extra="forbid")

    token_threshold: int
    target_chunk_size_hint: str


class ChunkingCoverageSchema(BaseModel):
    """Coverage narrowing settings driven by chunking policy."""

    model_config = ConfigDict(extra="forbid")

    default_top_k: int
    fallback_to_full_scan_on_error: bool = True
    per_dimension_top_k: dict[str, int] = Field(default_factory=dict)


class ChunkingPolicySchema(BaseModel):
    """Chunking + coverage planning policy."""

    model_config = ConfigDict(extra="forbid")

    policy_id: str
    document_processing: ChunkingDocumentProcessingSchema
    coverage: ChunkingCoverageSchema
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChunkingPolicyFileSchema(BaseModel):
    """Top-level schema for a chunking policy YAML file."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    chunking_policy: ChunkingPolicySchema


# ── Bundle Schemas ────────────────────────────────────────────────────────────


class ProviderEntryBundleSchema(BaseModel):
    """Provider endpoint config declared inside bundle YAML."""

    model_config = ConfigDict(extra="forbid")

    api_key_env: str
    model: str = ""
    api_base: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class ProviderConfigBundleSchema(BaseModel):
    """Per-rater/per-stage provider routing declared in bundle YAML."""

    model_config = ConfigDict(extra="forbid")

    default: ProviderEntryBundleSchema
    rater_providers: dict[str, ProviderEntryBundleSchema] = Field(default_factory=dict)
    stage_providers: dict[
        Literal["chunking", "coverage_planning", "evidence_extraction", "feedback"],
        ProviderEntryBundleSchema,
    ] = Field(default_factory=dict)


class OperationalParamsBundleSchema(BaseModel):
    """Runtime operational knobs declared in bundle YAML."""

    model_config = ConfigDict(extra="forbid")

    max_retries: int = Field(ge=0)


class BundleValidationRuleSchema(BaseModel):
    """Validation rule record attached to artifact bundle."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    type: str
    description: str


class ArtifactBundleSchema(BaseModel):
    """Schema for the `artifact_bundle` section in bundle YAML."""

    model_config = ConfigDict(extra="forbid")

    bundle_id: str
    bundle_version: str
    bundle_name: str
    description: str

    rubric_core_ref: str
    rubric_source_file: str
    adjudication_policy_ref: str
    adjudication_source_file: str
    aggregation_policy_ref: str
    aggregation_source_file: str
    explanation_policy_ref: str
    explanation_source_file: str

    operational_prompt_recipe_ref: str | None = None
    prompt_templates: list[str] = Field(default_factory=list)

    scoring_context_ref: str | None = None
    scoring_context_source_file: str | None = None

    chunking_policy_ref: str | None = None
    chunking_source_file: str | None = None

    freeze_hash: str | None = None
    freeze_timestamp: str | None = None

    source_documents: list[str] = Field(default_factory=list)
    validation_rules: list[BundleValidationRuleSchema] = Field(default_factory=list)
    provider_config: ProviderConfigBundleSchema | None = None
    operational_params: OperationalParamsBundleSchema | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_optional_ref_pairs(self) -> "ArtifactBundleSchema":
        has_scoring_ref = bool(self.scoring_context_ref)
        has_scoring_file = bool(self.scoring_context_source_file)
        if has_scoring_ref != has_scoring_file:
            raise ValueError(
                "scoring_context_ref and scoring_context_source_file must be set together"
            )

        has_ref = bool(self.chunking_policy_ref)
        has_file = bool(self.chunking_source_file)
        if has_ref != has_file:
            raise ValueError(
                "chunking_policy_ref and chunking_source_file must be set together"
            )
        return self


class BundleFileSchema(BaseModel):
    """Top-level schema for bundle YAML files."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    artifact_bundle: ArtifactBundleSchema


# ── Simplified Task-Specific Schemas (engineering eval format) ────────────────


class SimplifiedBundleFileSchema(BaseModel):
    """Simplified bundle format used by engineering evaluation tasks.

    Replaces the full ArtifactBundleSchema for tasks that use a flat layout
    with active_task_id templating.
    """

    schema_version: str
    bundle_id: str
    active_task_id: str
    rubric: dict     # {source, task}
    context: dict    # {task}
    prompts: dict    # {chunking, evidence_extraction, scoring, explanation}
    policies: dict   # {chunking, adjudication, aggregation}


class TaskRubricFileSchema(BaseModel):
    """Schema for per-task rubric YAML files (configs/rubrics/tasks/)."""

    schema_version: str
    task_id: str
    task_name: str
    indicator_description: str
    scale: dict       # {type, min, max, levels}
    dimensions: list  # [{code, name, anchors}]


class TaskContextFileSchema(BaseModel):
    """Schema for per-task scoring context YAML files (configs/tasks/)."""

    schema_version: str
    task_id: str = ""
    material_context: dict   # {type, description, evidence_focus}
    score_anchors: list = []
    human_instructions: str = ""
    scoring_context: list = []  # [{code, calibration_notes}]


class SimplifiedAdjudicationFileSchema(BaseModel):
    """Simplified schema for adjudication policy YAML files."""

    schema_version: str
    adjudication_policy: dict


class SimplifiedAggregationFileSchema(BaseModel):
    """Simplified schema for aggregation policy YAML files."""

    schema_version: str
    aggregation_policy: dict


class SimplifiedChunkingPolicyFileSchema(BaseModel):
    """Simplified schema for chunking policy YAML files."""

    schema_version: str
    chunking_policy: dict
