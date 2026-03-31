"""
配置编译器，负责把 bundle、rubric、policy 和 provider 配置编译成冻结快照。

Config Compiler: Orchestrates bundle loading, artifact resolution, and freeze.

Produces a frozen ResolvedArtifactBundle ready for use by the orchestrator.
The compiler is the single entry point for turning a bundle YAML file path
into a runtime-ready, hash-verified configuration snapshot.

Responsibilities:
- Load and parse the bundle via ConfigResolver
- Resolve all artifact references (load + validate + hash)
- Build RubricSnapshot and PolicySnapshot from loaded data
- Compute total bundle hash via freeze.py
- Return a fully frozen ResolvedArtifactBundle

Does NOT contain: rubric semantics, trait names, score values, adjudication
thresholds, aggregation formulas, or prompt text. All such data flows
exclusively through the loaded config artifacts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.freeze import compute_bundle_hash
from src.config.resolver import ConfigResolver, ResolverError
from src.contracts.artifact_bundle import (
    ArtifactBundle,
    PolicySnapshot,
    ProviderConfig,
    ProviderEntryConfig,
    ResolvedArtifactBundle,
    RubricSnapshot,
)

COMPILER_VERSION = "0.1.0"


class ConfigCompileError(Exception):
    """Raised when bundle compilation fails at any stage."""


class ConfigCompiler:
    """Compiles a bundle YAML file into a frozen ResolvedArtifactBundle.

    Args:
        configs_root: Root directory for config files (default: 'configs/').
    """

    def __init__(self, configs_root: Path | str | None = None) -> None:
        self.configs_root = Path(configs_root or "configs")
        self._resolver = ConfigResolver(self.configs_root)

    def compile(self, bundle_path: Path | str) -> ResolvedArtifactBundle:
        """Compile a bundle file into a frozen ResolvedArtifactBundle.

        Steps:
        1. Load bundle YAML → ArtifactBundle (refs unloaded)
        2. Resolve all artifact refs (load + validate + hash each)
        3. Build RubricSnapshot and PolicySnapshot
        4. Compute total_hash from all artifact content hashes
        5. Build frozen ArtifactBundle and ResolvedArtifactBundle

        Args:
            bundle_path: Path to the bundle YAML file.

        Returns:
            A fully frozen ResolvedArtifactBundle.

        Raises:
            ConfigCompileError: If loading, resolution, or validation fails.
        """
        bundle_path = Path(bundle_path)

        # Step 1: Load bundle
        try:
            bundle = self._resolver.load_bundle_file(bundle_path)
        except ResolverError as exc:
            raise ConfigCompileError(f"Failed to load bundle '{bundle_path}': {exc}") from exc

        # Step 2: Resolve all artifact refs
        try:
            loaded_rubric = self._resolver.load_artifact(bundle.rubric_ref)
            loaded_adj = self._resolver.load_artifact(bundle.adjudication_policy_ref)
            loaded_agg = self._resolver.load_artifact(bundle.aggregation_policy_ref)
            loaded_exp = self._resolver.load_artifact(bundle.explanation_policy_ref)
            loaded_prompts = [
                self._resolver.load_artifact(ref) for ref in bundle.prompt_refs
            ]
            loaded_chunking = (
                self._resolver.load_artifact(bundle.chunking_policy_ref)
                if bundle.chunking_policy_ref is not None
                else None
            )
            loaded_scoring_context = (
                self._resolver.load_artifact(bundle.scoring_context_ref)
                if bundle.scoring_context_ref is not None
                else None
            )
        except ResolverError as exc:
            raise ConfigCompileError(f"Failed to resolve artifact: {exc}") from exc

        # Step 3a: Build RubricSnapshot from rubric core data
        rubric_snapshot = _build_rubric_snapshot(loaded_rubric.loaded_data)

        # Step 3b: Build PolicySnapshot from policy data
        policy_snapshot = _build_policy_snapshot(
            loaded_adj.loaded_data,
            loaded_agg.loaded_data,
            loaded_exp.loaded_data,
            loaded_chunking.loaded_data if loaded_chunking is not None else None,
            loaded_scoring_context.loaded_data if loaded_scoring_context is not None else None,
        )

        # Step 3c: Build prompt templates dict {source_file: template_string}
        prompt_templates = {
            p.source_file: p.loaded_data["prompt_template"]
            for p in loaded_prompts
        }

        # Step 4: Compute total bundle hash
        all_content_hashes = [
            loaded_rubric.content_hash,
            loaded_adj.content_hash,
            loaded_agg.content_hash,
            loaded_exp.content_hash,
            *[p.content_hash for p in loaded_prompts],
        ]
        if loaded_chunking is not None:
            all_content_hashes.append(loaded_chunking.content_hash)
        if loaded_scoring_context is not None:
            all_content_hashes.append(loaded_scoring_context.content_hash)
        total_hash = compute_bundle_hash(all_content_hashes)

        # Step 4b: Build ProviderConfig from raw dict (if present in bundle)
        provider_config = _build_provider_config(bundle.provider_config_raw)

        # Step 5: Build frozen ArtifactBundle (with freeze_hash set)
        resolved_at = datetime.now(timezone.utc)
        frozen_bundle = ArtifactBundle(
            bundle_id=bundle.bundle_id,
            bundle_version=bundle.bundle_version,
            bundle_name=bundle.bundle_name,
            description=bundle.description,
            schema_version=bundle.schema_version,
            rubric_ref=loaded_rubric,
            adjudication_policy_ref=loaded_adj,
            aggregation_policy_ref=loaded_agg,
            explanation_policy_ref=loaded_exp,
            prompt_refs=loaded_prompts,
            source_documents=bundle.source_documents,
            freeze_hash=total_hash,
            freeze_timestamp=resolved_at,
            validation_rules=bundle.validation_rules,
            metadata=bundle.metadata,
            provider_config_raw=bundle.provider_config_raw,
            chunking_policy_ref=loaded_chunking,
            scoring_context_ref=loaded_scoring_context,
        )

        return ResolvedArtifactBundle(
            artifact_bundle=frozen_bundle,
            rubric_snapshot=rubric_snapshot,
            policy_snapshot=policy_snapshot,
            prompt_templates=prompt_templates,
            provider_config=provider_config,
            resolved_at=resolved_at,
            resolver_version=COMPILER_VERSION,
            total_hash=total_hash,
        )


def _parse_provider_entry(raw: dict[str, Any]) -> ProviderEntryConfig:
    """Parse a single provider entry dict into a ProviderEntryConfig."""
    return ProviderEntryConfig(
        api_key_env=raw["api_key_env"],
        model=raw.get("model", "") or "",
        api_base=raw.get("api_base", "") or "",
        params=dict(raw.get("params") or {}),
    )


def _build_provider_config(raw: dict[str, Any] | None) -> ProviderConfig | None:
    """Parse the provider_config section of a bundle YAML into a ProviderConfig.

    Returns None if no provider_config section is present.
    Raises ConfigCompileError if the section is malformed.
    """
    if raw is None:
        return None
    try:
        default = _parse_provider_entry(raw["default"])
        rater_providers = {
            rater_id: _parse_provider_entry(entry)
            for rater_id, entry in (raw.get("rater_providers") or {}).items()
        }
        stage_providers = {
            stage: _parse_provider_entry(entry)
            for stage, entry in (raw.get("stage_providers") or {}).items()
        }
        return ProviderConfig(
            default=default,
            rater_providers=rater_providers,
            stage_providers=stage_providers,
        )
    except (KeyError, TypeError) as exc:
        raise ConfigCompileError(f"Malformed provider_config in bundle: {exc}") from exc


def _build_rubric_snapshot(rubric_file_data: dict[str, Any]) -> RubricSnapshot:
    """Build a RubricSnapshot with lookup maps from rubric file data."""
    core = rubric_file_data["rubric_core"]
    dimensions: list[dict[str, Any]] = core["dimensions"]
    scales: list[dict[str, Any]] = core["scales"]
    return RubricSnapshot(
        rubric_id=core["rubric_id"],
        rubric_version=core["rubric_version"],
        rubric_name=core["rubric_name"],
        dimensions=dimensions,
        scales=scales,
        dimension_by_id={d["dimension_id"]: d for d in dimensions},
        dimension_by_code={d["code"]: d for d in dimensions},
        scale_by_id={s["scale_id"]: s for s in scales},
    )


def _build_policy_snapshot(
    adj_file_data: dict[str, Any],
    agg_file_data: dict[str, Any],
    exp_file_data: dict[str, Any],
    chunking_file_data: dict[str, Any] | None = None,
    scoring_context_file_data: dict[str, Any] | None = None,
) -> PolicySnapshot:
    """Build a PolicySnapshot from the inner policy content of each file."""
    adj_policy = adj_file_data["adjudication_policy"]
    agg_policy = agg_file_data["aggregation_policy"]
    exp_policy = exp_file_data["explanation_policy"]
    chunking_policy = {}
    if isinstance(chunking_file_data, dict):
        chunking_policy = dict(chunking_file_data.get("chunking_policy") or {})

    scoring_context = {}
    if isinstance(scoring_context_file_data, dict):
        scoring_context = dict(scoring_context_file_data.get("scoring_context") or {})

    policy_version = (
        f"adj:{adj_policy.get('policy_version', 'unknown')}"
        f"|agg:{agg_policy.get('policy_version', 'unknown')}"
        f"|exp:{exp_policy.get('policy_version', 'unknown')}"
    )

    return PolicySnapshot(
        adjudication_policy=adj_policy,
        aggregation_policy=agg_policy,
        explanation_policy=exp_policy,
        policy_version=policy_version,
        chunking_policy=chunking_policy,
        scoring_context=scoring_context,
    )
