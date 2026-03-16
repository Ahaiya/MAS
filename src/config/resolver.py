"""
Config Resolver: Loads and validates artifact references from bundle YAML files.

Responsibilities:
- Parse bundle YAML file into ArtifactBundle dataclass
- Load each referenced artifact file from configs/
- Validate each artifact against its Pydantic schema
- Compute content hash for each artifact
- Return ArtifactRef with loaded_data and content_hash populated

Does NOT contain: rubric semantics, adjudication logic, aggregation formulas,
or any business facts. All schema validation is structural only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from src.config.freeze import compute_content_hash
from src.config.schema import (
    AdjudicationFileSchema,
    AggregationFileSchema,
    ExplanationFileSchema,
    PromptFileSchema,
    RubricFileSchema,
)
from src.contracts.artifact_bundle import (
    ArtifactBundle,
    ArtifactRef,
    SchemaVersion,
)


class ResolverError(Exception):
    """Raised when artifact loading or schema validation fails."""


# Maps source file path prefix patterns to their Pydantic schema classes.
# Order matters: more specific patterns first.
_SCHEMA_ROUTE: list[tuple[str, type]] = [
    ("rubrics/", RubricFileSchema),
    ("policies/adjudication/", AdjudicationFileSchema),
    ("policies/aggregation/", AggregationFileSchema),
    ("policies/explanation/", ExplanationFileSchema),
    ("prompts/", PromptFileSchema),
]


def _resolve_schema_class(source_file: str) -> type | None:
    """Return the schema class for a given source file path, or None if unknown."""
    for prefix, schema_cls in _SCHEMA_ROUTE:
        if source_file.startswith(prefix):
            return schema_cls
    return None


class ConfigResolver:
    """Loads bundle YAML files and resolves artifact references.

    Args:
        configs_root: Root directory for all config files (default: 'configs/').
    """

    def __init__(self, configs_root: Path | str = "configs") -> None:
        self.configs_root = Path(configs_root)

    def load_bundle_file(self, bundle_path: Path | str) -> ArtifactBundle:
        """Parse a bundle YAML file into an ArtifactBundle.

        The returned bundle has unloaded refs (no loaded_data or content_hash).
        Call load_artifact() on each ref to populate those fields.

        Args:
            bundle_path: Absolute or relative path to the bundle YAML file.

        Returns:
            ArtifactBundle with all refs parsed but not yet loaded.

        Raises:
            ResolverError: If file does not exist or YAML parse fails.
        """
        bundle_path = Path(bundle_path)
        if not bundle_path.exists():
            raise ResolverError(f"Bundle file not found: {bundle_path}")

        try:
            raw = yaml.safe_load(bundle_path.read_text())
        except yaml.YAMLError as exc:
            raise ResolverError(f"Failed to parse bundle YAML {bundle_path}: {exc}") from exc

        try:
            schema_version = SchemaVersion(str(raw["schema_version"]))
            ab = raw["artifact_bundle"]

            rubric_ref = ArtifactRef(
                ref_uri=ab["rubric_core_ref"],
                source_file=ab["rubric_source_file"],
            )
            adj_ref = ArtifactRef(
                ref_uri=ab["adjudication_policy_ref"],
                source_file=ab["adjudication_source_file"],
            )
            agg_ref = ArtifactRef(
                ref_uri=ab["aggregation_policy_ref"],
                source_file=ab["aggregation_source_file"],
            )
            exp_ref = ArtifactRef(
                ref_uri=ab["explanation_policy_ref"],
                source_file=ab["explanation_source_file"],
            )

            prompt_files: list[str] = ab.get("prompt_templates", []) or []
            prompt_refs = [
                ArtifactRef(
                    ref_uri=f"ops://prompts/{Path(pf).stem}/v1",
                    source_file=pf,
                )
                for pf in prompt_files
            ]

            return ArtifactBundle(
                bundle_id=ab["bundle_id"],
                bundle_version=str(ab["bundle_version"]),
                bundle_name=ab["bundle_name"],
                description=ab["description"],
                schema_version=schema_version,
                rubric_ref=rubric_ref,
                adjudication_policy_ref=adj_ref,
                aggregation_policy_ref=agg_ref,
                explanation_policy_ref=exp_ref,
                prompt_refs=prompt_refs,
                source_documents=ab.get("source_documents", []) or [],
                validation_rules=ab.get("validation_rules", []) or [],
                metadata=ab.get("metadata", {}) or {},
                provider_config_raw=ab.get("provider_config") or None,
            )
        except (KeyError, ValueError) as exc:
            raise ResolverError(f"Malformed bundle file {bundle_path}: {exc}") from exc

    def load_artifact(self, ref: ArtifactRef) -> ArtifactRef:
        """Load an artifact file, validate its schema, and return a populated ref.

        Args:
            ref: An ArtifactRef with source_file set (loaded_data may be None).

        Returns:
            A new ArtifactRef with loaded_data and content_hash populated.

        Raises:
            ResolverError: If file is missing, YAML parse fails, or schema validation fails.
        """
        artifact_path = self.configs_root / ref.source_file
        if not artifact_path.exists():
            raise ResolverError(f"Artifact file not found: {artifact_path}")

        try:
            content = artifact_path.read_text(encoding="utf-8")
            data: dict[str, Any] = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ResolverError(
                f"Failed to parse artifact YAML {artifact_path}: {exc}"
            ) from exc

        content_hash = compute_content_hash(content)

        schema_cls = _resolve_schema_class(ref.source_file)
        if schema_cls is not None:
            try:
                schema_cls(**data)
            except ValidationError as exc:
                raise ResolverError(
                    f"Schema validation failed for {ref.source_file}: {exc}"
                ) from exc

        return ArtifactRef(
            ref_uri=ref.ref_uri,
            source_file=ref.source_file,
            loaded_data=data,
            content_hash=content_hash,
        )
