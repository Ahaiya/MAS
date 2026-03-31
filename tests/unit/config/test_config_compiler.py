"""
Tests for Config Compiler, Resolver, and Freeze (P1T4)

TDD: Tests written first. These tests define the expected interfaces for:
- src/config/freeze.py: compute_content_hash, compute_bundle_hash
- src/config/resolver.py: ConfigResolver (load_bundle_file, load_artifact)
- src/config/compiler.py: ConfigCompiler (compile), ConfigCompileError

No business facts (trait names, score values) are hardcoded here.
All domain-specific assertions derive from what is present in the actual config files.
"""

import pytest
import yaml
from datetime import datetime
from pathlib import Path

from src.config.freeze import compute_content_hash, compute_bundle_hash
from src.config.resolver import ConfigResolver, ResolverError
from src.config.compiler import ConfigCompiler, ConfigCompileError
from src.contracts.artifact_bundle import (
    ArtifactBundle,
    ArtifactRef,
    ResolvedArtifactBundle,
    SchemaVersion,
)

CONFIGS_ROOT = Path("configs")
BUNDLE_PATH = CONFIGS_ROOT / "bundles/asap_set8_baseline.bundle.yaml"


# ── Freeze Tests ──────────────────────────────────────────────────────────────


class TestComputeContentHash:
    def test_returns_64_char_hex_string(self):
        h = compute_content_hash("hello world")
        assert isinstance(h, str)
        assert len(h) == 64

    def test_deterministic(self):
        h1 = compute_content_hash("hello world")
        h2 = compute_content_hash("hello world")
        assert h1 == h2

    def test_different_inputs_produce_different_hashes(self):
        h1 = compute_content_hash("content A")
        h2 = compute_content_hash("content B")
        assert h1 != h2

    def test_empty_string_produces_valid_hash(self):
        h = compute_content_hash("")
        assert isinstance(h, str)
        assert len(h) == 64

    def test_unicode_content(self):
        h = compute_content_hash("日本語テスト")
        assert len(h) == 64


class TestComputeBundleHash:
    def test_returns_64_char_hex_string(self):
        h = compute_bundle_hash(["hash1", "hash2"])
        assert isinstance(h, str)
        assert len(h) == 64

    def test_deterministic_same_input(self):
        hashes = ["abc", "def", "ghi"]
        assert compute_bundle_hash(hashes) == compute_bundle_hash(hashes)

    def test_order_independent(self):
        """Bundle hash must be order-independent to avoid fragile ordering."""
        h1 = compute_bundle_hash(["hash_a", "hash_b", "hash_c"])
        h2 = compute_bundle_hash(["hash_c", "hash_a", "hash_b"])
        assert h1 == h2

    def test_different_inputs_different_result(self):
        h1 = compute_bundle_hash(["hash_a", "hash_b"])
        h2 = compute_bundle_hash(["hash_x", "hash_y"])
        assert h1 != h2

    def test_empty_list(self):
        h = compute_bundle_hash([])
        assert isinstance(h, str)
        assert len(h) == 64

    def test_single_hash(self):
        h = compute_bundle_hash(["only_one"])
        assert len(h) == 64


# ── ConfigResolver Tests ──────────────────────────────────────────────────────


class TestConfigResolverLoadBundleFile:
    @pytest.fixture
    def resolver(self):
        return ConfigResolver(CONFIGS_ROOT)

    def test_returns_artifact_bundle(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert isinstance(bundle, ArtifactBundle)

    def test_bundle_id(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert bundle.bundle_id == "asap_set8_baseline"

    def test_bundle_version(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert bundle.bundle_version == "2026-03-12"

    def test_schema_version(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert bundle.schema_version == SchemaVersion.V2_0

    def test_rubric_ref_uri(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert bundle.rubric_ref.ref_uri == "rubric://asap_set8_writing_traits/v1"

    def test_rubric_ref_source_file(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert bundle.rubric_ref.source_file == "rubrics/asap_set8_baseline.yaml"

    def test_adjudication_ref_uri(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert bundle.adjudication_policy_ref.ref_uri == "policy://asap_set8_default_adjudication/v1"

    def test_aggregation_ref_uri(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert bundle.aggregation_policy_ref.ref_uri == "policy://asap_set8_composite/v1"

    def test_explanation_ref_uri(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert bundle.explanation_policy_ref.ref_uri == "explain://evidence_grounded_v1"

    def test_prompt_refs_count(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert len(bundle.prompt_refs) == 5

    def test_chunking_policy_ref_loaded(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert bundle.chunking_policy_ref is not None
        assert bundle.chunking_policy_ref.source_file == "policies/chunking/asap_set8_chunking.yaml"

    def test_scoring_context_ref_loaded(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert bundle.scoring_context_ref is not None
        assert bundle.scoring_context_ref.source_file == "prompts/scoring_context.yaml"

    def test_prompt_refs_are_artifact_refs(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        for ref in bundle.prompt_refs:
            assert isinstance(ref, ArtifactRef)
            assert ref.source_file.startswith("prompts/")

    def test_source_documents_count(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert len(bundle.source_documents) == 3

    def test_bundle_not_yet_frozen(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert not bundle.is_frozen()

    def test_nonexistent_file_raises_resolver_error(self, resolver):
        with pytest.raises(ResolverError):
            resolver.load_bundle_file(Path("nonexistent.bundle.yaml"))


class TestConfigResolverLoadArtifact:
    @pytest.fixture
    def resolver(self):
        return ConfigResolver(CONFIGS_ROOT)

    @pytest.fixture
    def bundle(self, resolver):
        return resolver.load_bundle_file(BUNDLE_PATH)

    def test_rubric_artifact_loaded_data_not_none(self, resolver, bundle):
        loaded = resolver.load_artifact(bundle.rubric_ref)
        assert loaded.loaded_data is not None

    def test_rubric_artifact_content_hash_is_64_char_hex(self, resolver, bundle):
        loaded = resolver.load_artifact(bundle.rubric_ref)
        assert loaded.content_hash is not None
        assert len(loaded.content_hash) == 64

    def test_rubric_artifact_hash_deterministic(self, resolver, bundle):
        h1 = resolver.load_artifact(bundle.rubric_ref).content_hash
        h2 = resolver.load_artifact(bundle.rubric_ref).content_hash
        assert h1 == h2

    def test_rubric_artifact_preserves_ref_uri(self, resolver, bundle):
        loaded = resolver.load_artifact(bundle.rubric_ref)
        assert loaded.ref_uri == bundle.rubric_ref.ref_uri

    def test_adjudication_artifact_loaded(self, resolver, bundle):
        loaded = resolver.load_artifact(bundle.adjudication_policy_ref)
        assert loaded.loaded_data is not None
        assert "adjudication_policy" in loaded.loaded_data

    def test_aggregation_artifact_loaded(self, resolver, bundle):
        loaded = resolver.load_artifact(bundle.aggregation_policy_ref)
        assert loaded.loaded_data is not None
        assert "aggregation_policy" in loaded.loaded_data

    def test_explanation_artifact_loaded(self, resolver, bundle):
        loaded = resolver.load_artifact(bundle.explanation_policy_ref)
        assert loaded.loaded_data is not None
        assert "explanation_policy" in loaded.loaded_data

    def test_prompt_artifacts_loaded(self, resolver, bundle):
        for ref in bundle.prompt_refs:
            loaded = resolver.load_artifact(ref)
            assert loaded.loaded_data is not None
            assert "prompt_template" in loaded.loaded_data

    def test_all_refs_resolvable(self, resolver, bundle):
        """Version closure: every ref in the bundle must resolve."""
        for ref in bundle.get_all_refs():
            loaded = resolver.load_artifact(ref)
            assert loaded.loaded_data is not None
            assert loaded.content_hash is not None

    def test_missing_artifact_raises_resolver_error(self, resolver):
        missing_ref = ArtifactRef(
            ref_uri="rubric://nonexistent/v1",
            source_file="rubrics/nonexistent.yaml",
        )
        with pytest.raises(ResolverError):
            resolver.load_artifact(missing_ref)

    def test_schema_validation_runs_for_rubric(self, resolver, bundle):
        """Rubric artifact must pass schema validation without raising."""
        loaded = resolver.load_artifact(bundle.rubric_ref)
        # Confirms schema validation succeeded (no ResolverError raised)
        assert loaded.loaded_data["rubric_core"]["rubric_id"] == "asap_set8_writing_traits"

    def test_schema_validation_runs_for_adjudication(self, resolver, bundle):
        loaded = resolver.load_artifact(bundle.adjudication_policy_ref)
        policy = loaded.loaded_data["adjudication_policy"]
        assert "triggers" in policy

    def test_schema_validation_runs_for_aggregation(self, resolver, bundle):
        loaded = resolver.load_artifact(bundle.aggregation_policy_ref)
        policy = loaded.loaded_data["aggregation_policy"]
        assert "composite_formula" in policy

    def test_schema_validation_runs_for_explanation(self, resolver, bundle):
        loaded = resolver.load_artifact(bundle.explanation_policy_ref)
        policy = loaded.loaded_data["explanation_policy"]
        assert "requirements" in policy


# ── ConfigCompiler Tests ──────────────────────────────────────────────────────


class TestConfigCompilerCompile:
    @pytest.fixture
    def compiler(self):
        return ConfigCompiler(CONFIGS_ROOT)

    def test_returns_resolved_artifact_bundle(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        assert isinstance(result, ResolvedArtifactBundle)

    def test_bundle_id_preserved(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        assert result.artifact_bundle.bundle_id == "asap_set8_baseline"

    def test_bundle_is_frozen_after_compile(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        assert result.artifact_bundle.is_frozen()

    def test_rubric_snapshot_not_none(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        assert result.rubric_snapshot is not None

    def test_rubric_snapshot_rubric_id(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        assert result.rubric_snapshot.rubric_id == "asap_set8_writing_traits"

    def test_rubric_snapshot_dimensions_count(self, compiler):
        """Rubric snapshot must contain all dimensions from config (not hardcoded)."""
        result = compiler.compile(BUNDLE_PATH)
        # Derive expected count from actual config file
        rubric_data = yaml.safe_load(
            (CONFIGS_ROOT / "rubrics/asap_set8_baseline.yaml").read_text()
        )
        expected_count = len(rubric_data["rubric_core"]["dimensions"])
        assert len(result.rubric_snapshot.dimensions) == expected_count

    def test_rubric_snapshot_dimension_by_id_populated(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        assert len(result.rubric_snapshot.dimension_by_id) > 0
        # Every dimension accessible by its dimension_id
        for dim in result.rubric_snapshot.dimensions:
            assert dim["dimension_id"] in result.rubric_snapshot.dimension_by_id

    def test_rubric_snapshot_dimension_by_code_populated(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        assert len(result.rubric_snapshot.dimension_by_code) > 0
        for dim in result.rubric_snapshot.dimensions:
            assert dim["code"] in result.rubric_snapshot.dimension_by_code

    def test_rubric_snapshot_scale_by_id_populated(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        assert len(result.rubric_snapshot.scale_by_id) > 0
        for scale in result.rubric_snapshot.scales:
            assert scale["scale_id"] in result.rubric_snapshot.scale_by_id

    def test_policy_snapshot_not_none(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        assert result.policy_snapshot is not None

    def test_policy_snapshot_adjudication_has_triggers(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        assert "triggers" in result.policy_snapshot.adjudication_policy

    def test_policy_snapshot_aggregation_has_formula(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        assert "composite_formula" in result.policy_snapshot.aggregation_policy

    def test_policy_snapshot_explanation_has_requirements(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        assert "requirements" in result.policy_snapshot.explanation_policy

    def test_policy_snapshot_version_string_not_empty(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        assert result.policy_snapshot.policy_version
        assert len(result.policy_snapshot.policy_version) > 0

    def test_policy_snapshot_has_chunking_policy(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        policy = result.policy_snapshot.chunking_policy
        assert policy.get("policy_id") == "asap_set8_chunking_v1"
        assert policy.get("coverage", {}).get("default_top_k") == 5

    def test_policy_snapshot_has_scoring_context(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        context = result.policy_snapshot.scoring_context
        assert context.get("context_id") == "asap_set8_scoring_context_v1"
        assert len(context.get("score_anchors", [])) >= 1

    def test_provider_params_preserved_in_compiled_bundle(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        assert result.provider_config is not None
        r2 = result.provider_config.rater_providers["rater_2"]
        assert r2.params["temperature"] == 0.0
        assert r2.params["max_tokens"] == 2048
        assert r2.params["extra_body"]["enable_thinking"] is True
        assert r2.params["extra_body"]["thinking_budget"] == 512

    def test_prompt_templates_count(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        assert len(result.prompt_templates) == 5

    def test_prompt_templates_values_are_strings(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        for key, template in result.prompt_templates.items():
            assert isinstance(template, str)
            assert len(template) > 0

    def test_total_hash_is_64_char_hex(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        assert result.total_hash is not None
        assert len(result.total_hash) == 64

    def test_total_hash_deterministic(self, compiler):
        """Same config files must produce same hash (replay safety)."""
        r1 = compiler.compile(BUNDLE_PATH)
        r2 = compiler.compile(BUNDLE_PATH)
        assert r1.total_hash == r2.total_hash

    def test_freeze_hash_equals_total_hash(self, compiler):
        """Freeze hash on the bundle must match the total_hash on the resolved bundle."""
        result = compiler.compile(BUNDLE_PATH)
        assert result.artifact_bundle.freeze_hash == result.total_hash

    def test_resolved_at_is_datetime(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        assert isinstance(result.resolved_at, datetime)

    def test_resolver_version_not_empty(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        assert result.resolver_version
        assert len(result.resolver_version) > 0

    def test_get_version_info_contains_bundle_id(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        version_info = result.get_version_info()
        assert "asap_set8_baseline" in version_info

    def test_frozen_config_summary_keys(self, compiler):
        result = compiler.compile(BUNDLE_PATH)
        summary = result.get_frozen_config_summary()
        assert "bundle_id" in summary
        assert "bundle_version" in summary
        assert "rubric_id" in summary
        assert "rubric_version" in summary
        assert "policy_version" in summary
        assert "total_hash" in summary
        assert "resolved_at" in summary

    def test_nonexistent_bundle_raises_compile_error(self, compiler):
        with pytest.raises(ConfigCompileError):
            compiler.compile(Path("nonexistent.bundle.yaml"))


class TestConfigCompileError:
    def test_is_exception_subclass(self):
        err = ConfigCompileError("test error")
        assert isinstance(err, Exception)

    def test_message_preserved(self):
        err = ConfigCompileError("test error message")
        assert "test error message" in str(err)
