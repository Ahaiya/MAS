"""
配置工件契约，定义 bundle 编译完成后对外暴露的冻结快照结构。

Artifact Bundle Contracts

Defines the schemas for configuration artifact bundles.
This is the entry point for zero-hardcoding - all runtime configuration
must flow through these contracts.

Core Contracts:
- ArtifactBundle: The loaded, unfrozen collection of config artifacts
- ResolvedArtifactBundle: The frozen, runtime-ready bundle with all refs resolved
- RubricSnapshot: Extracted rubric core data for fast access
- PolicySnapshot: Extracted adjudication/aggregation/explanation policies

All bundle operations must be version-aware, hash-verifiable, and replay-safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List, Any
from enum import Enum


@dataclass(frozen=True)
class ProviderEntryConfig:
    """Config for a single LLM provider endpoint.

    Attributes:
        api_key_env: Name of the environment variable that holds the API key.
        model:       Model identifier (e.g. "deepseek-chat"). Empty string means
                     read LLM_MODEL from env at build time.
        api_base:    API base URL. Empty string means read LLM_API_BASE from env.
        params:      Optional provider-default request params merged into each call
                     (for example temperature, max_tokens, or provider-specific
                     extra_body settings such as reasoning controls).
    """
    api_key_env: str
    model: str = ""
    api_base: str = ""
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderConfig:
    """Per-rater and per-stage provider configuration loaded from the bundle.

    Attributes:
        default:         Fallback provider used when a rater/stage has no specific entry.
        rater_providers: Maps rater_id (e.g. "rater_1") to its ProviderEntryConfig.
        stage_providers: Maps stage name (e.g. "evidence_extraction") to its config.
    """
    default: ProviderEntryConfig
    rater_providers: Dict[str, ProviderEntryConfig] = field(default_factory=dict)
    stage_providers: Dict[str, ProviderEntryConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class OperationalParams:
    """Runtime operational knobs declared by bundle and consumed by pipeline."""

    max_retries: int = 2


class SchemaVersion(Enum):
    """Supported schema versions for artifact bundles."""
    V2_0 = "2.0"


@dataclass(frozen=True)
class ArtifactRef:
    """Reference to a configuration artifact file.

    Attributes:
        ref_uri: URI-style reference (e.g., "rubric://asap_set8_writing_traits/v1")
        source_file: Relative path from configs/ directory
        loaded_data: The parsed YAML/JSON data (None until loaded)
        content_hash: SHA-256 hash of loaded content
    """
    ref_uri: str
    source_file: str
    loaded_data: Optional[Dict[str, Any]] = None
    content_hash: Optional[str] = None


@dataclass(frozen=True)
class ArtifactBundle:
    """A collection of configuration artifacts before resolution.

    This represents the loaded but unfrozen state. The ConfigResolver
    will load all referenced artifacts, compute hashes, and freeze into
    a ResolvedArtifactBundle.

    Attributes:
        bundle_id: Unique identifier for this bundle
        bundle_version: Version string (semantic version or timestamp)
        bundle_name: Human-readable name
        description: Description of what this bundle evaluates
        schema_version: Schema version for bundle structure
        rubric_ref: Reference to rubric core artifact
        adjudication_policy_ref: Reference to adjudication policy artifact
        aggregation_policy_ref: Reference to aggregation policy artifact
        explanation_policy_ref: Reference to explanation policy artifact
        chunking_policy_ref: Optional reference to chunking policy artifact
        scoring_context_ref: Optional reference to scoring context artifact
        prompt_refs: List of prompt template references
        source_documents: List of source documentation files
        freeze_hash: Computed hash of all artifact contents (set during resolution)
        freeze_timestamp: When this bundle was frozen
        validation_rules: Rules to validate the bundle closure
        metadata: Additional metadata tags
    """
    bundle_id: str
    bundle_version: str
    bundle_name: str
    description: str
    schema_version: SchemaVersion

    # Artifact references
    rubric_ref: ArtifactRef
    adjudication_policy_ref: ArtifactRef
    aggregation_policy_ref: ArtifactRef
    prompt_refs: List[ArtifactRef]

    # Source documentation
    source_documents: List[str]

    # Freeze metadata (set during resolution)
    freeze_hash: Optional[str] = None
    freeze_timestamp: Optional[datetime] = None

    # Explanation policy reference (optional: simplified bundles may omit it)
    explanation_policy_ref: Optional[ArtifactRef] = None

    # Validation
    validation_rules: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Raw provider_config dict parsed from bundle YAML (structured by compiler)
    provider_config_raw: Optional[Dict[str, Any]] = None
    # Raw operational_params dict parsed from bundle YAML (structured by compiler)
    operational_params_raw: Optional[Dict[str, Any]] = None
    # Optional chunking policy reference parsed from bundle YAML
    chunking_policy_ref: Optional[ArtifactRef] = None
    # Optional scoring context reference parsed from bundle YAML
    scoring_context_ref: Optional[ArtifactRef] = None

    def is_frozen(self) -> bool:
        """Check if this bundle has been resolved and frozen."""
        return self.freeze_hash is not None

    def get_all_refs(self) -> List[ArtifactRef]:
        """Get all artifact references in this bundle."""
        refs = [
            self.rubric_ref,
            self.adjudication_policy_ref,
            self.aggregation_policy_ref,
            *self.prompt_refs,
        ]
        if self.explanation_policy_ref is not None:
            refs.append(self.explanation_policy_ref)
        if self.chunking_policy_ref is not None:
            refs.append(self.chunking_policy_ref)
        if self.scoring_context_ref is not None:
            refs.append(self.scoring_context_ref)
        return refs


@dataclass(frozen=True)
class RubricSnapshot:
    """Extracted rubric core data for fast runtime access.

    This snapshot contains only the essential rubric data needed
    by agents, avoiding repeated YAML parsing during evaluation.

    Attributes:
        rubric_id: Unique identifier for this rubric
        rubric_version: Version string
        rubric_name: Human-readable name
        dimensions: List of dimension definitions
        scales: List of available score scales
        dimension_by_id: Map of dimension_id -> dimension definition
        dimension_by_code: Map of dimension_code -> dimension definition
        scale_by_id: Map of scale_id -> scale definition
    """
    rubric_id: str
    rubric_version: str
    rubric_name: str
    dimensions: List[Dict[str, Any]]
    scales: List[Dict[str, Any]]
    indicator_description: str = ""
    raw_task_rubric: Dict[str, Any] = field(default_factory=dict)

    # Computed lookup maps for fast access
    dimension_by_id: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    dimension_by_code: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    scale_by_id: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def get_dimension(self, dimension_id: str) -> Optional[Dict[str, Any]]:
        """Get dimension by ID."""
        return self.dimension_by_id.get(dimension_id)

    def get_dimension_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        """Get dimension by code (e.g., 'I', 'O', 'V')."""
        return self.dimension_by_code.get(code)

    def get_scale(self, scale_id: str) -> Optional[Dict[str, Any]]:
        """Get scale by ID."""
        return self.scale_by_id.get(scale_id)

    def validate_score(self, dimension_id: str, score: int) -> bool:
        """Validate score is within scale range for given dimension."""
        dimension = self.get_dimension(dimension_id)
        if not dimension:
            return False
        scale_ref = dimension.get("scale_ref")
        if not scale_ref:
            return False
        scale = self.get_scale(scale_ref)
        if not scale:
            return False
        min_score = scale.get("min", 1)
        max_score = scale.get("max", 6)
        return min_score <= score <= max_score


@dataclass(frozen=True)
class PolicySnapshot:
    """Extracted adjudication, aggregation, and explanation policies.

    This snapshot contains policy data needed by consistency checker,
    adjudicator, and feedback assembler.

    Attributes:
        adjudication_policy: Parsed adjudication policy rules
        aggregation_policy: Parsed aggregation policy formulas
        explanation_policy: Parsed explanation policy requirements
        chunking_policy: Optional chunking/coverage narrowing policy
        policy_version: Combined version string for policy snapshot
    """
    adjudication_policy: Dict[str, Any]
    aggregation_policy: Dict[str, Any]
    explanation_policy: Dict[str, Any]
    policy_version: str
    chunking_policy: Dict[str, Any] = field(default_factory=dict)
    scoring_context: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedArtifactBundle:
    """A frozen, runtime-ready bundle with all refs resolved.

    This is what the ConfigResolver produces and what the orchestrator
    receives at the start of each evaluation. It contains resolved
    snapshots for fast access and guarantees version closure.

    Attributes:
        artifact_bundle: The original ArtifactBundle (now frozen)
        rubric_snapshot: Extracted rubric core data
        policy_snapshot: Extracted adjudication/aggregation/explanation policies
        prompt_templates: Loaded prompt templates
        resolved_at: When this bundle was resolved
        resolver_version: Version of the resolver that created this snapshot
        total_hash: Combined hash of all resolved artifacts
    """
    artifact_bundle: ArtifactBundle
    rubric_snapshot: RubricSnapshot
    policy_snapshot: PolicySnapshot
    prompt_templates: Dict[str, str]

    # Resolution metadata
    resolved_at: datetime
    resolver_version: str
    total_hash: str

    # Structured provider configuration (None if not declared in bundle)
    provider_config: Optional[ProviderConfig] = None
    # Structured operational params (None if not declared in bundle)
    operational_params: Optional[OperationalParams] = None

    def get_version_info(self) -> str:
        """Get formatted version information for logging."""
        return (
            f"{self.artifact_bundle.bundle_id}@{self.artifact_bundle.bundle_version} "
            f"| rubric:{self.rubric_snapshot.rubric_version} "
            f"| policy:{self.policy_snapshot.policy_version}"
        )

    def get_frozen_config_summary(self) -> Dict[str, str]:
        """Get a summary of frozen configuration for trace metadata."""
        return {
            "bundle_id": self.artifact_bundle.bundle_id,
            "bundle_version": self.artifact_bundle.bundle_version,
            "rubric_id": self.rubric_snapshot.rubric_id,
            "rubric_version": self.rubric_snapshot.rubric_version,
            "policy_version": self.policy_snapshot.policy_version,
            "resolved_at": self.resolved_at.isoformat(),
            "total_hash": self.total_hash,
        }


# Factory function for creating empty bundle references
def create_artifact_ref(
    ref_uri: str,
    source_file: str
) -> ArtifactRef:
    """Create an ArtifactRef with unloaded data."""
    return ArtifactRef(
        ref_uri=ref_uri,
        source_file=source_file,
        loaded_data=None,
        content_hash=None
    )
