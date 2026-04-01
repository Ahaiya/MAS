"""
Zero-Hardcoding Firewall Tests (P1T6)

These tests prove that all business facts — dimension count, trait names,
scale range, composite formula weights, display annotation policy, and
adjudication thresholds — flow exclusively through configs/, never hardcoded
in src/ code.

Two-pronged approach:
1. Behavioral: compile baseline bundle and alt bundle, prove different configs
   produce structurally different results. If any value were hardcoded in code,
   swapping the config would not change that value.
2. Static: scan src/**/*.py for known baseline business-fact strings.
   If a baseline-specific identifier (e.g., a dimension ID) appears in src/,
   it is hardcoded — a direct firewall violation.

If ANY test in this file fails:
  → A business fact has leaked from configs/ into src/ code.
  → Remediation: Phase revert, refactor to read from configs/.
"""

import ast
import re
from pathlib import Path

import pytest
import yaml

from src.config.compiler import ConfigCompiler
from src.contracts.artifact_bundle import ResolvedArtifactBundle

# ── Paths ─────────────────────────────────────────────────────────────────────

CONFIGS_ROOT = Path("configs")
FIXTURES_ROOT = Path("tests/fixtures/configs")
BASELINE_BUNDLE = CONFIGS_ROOT / "bundles/asap_set8_baseline.bundle.yaml"
ALT_BUNDLE = FIXTURES_ROOT / "bundles/alt_bundle.yaml"
SRC_DIR = Path("src")


# ── Shared Compiled Bundles ───────────────────────────────────────────────────


@pytest.fixture(scope="module")
def compiled_base() -> ResolvedArtifactBundle:
    """Compile the baseline bundle once for the whole module."""
    return ConfigCompiler(configs_root=CONFIGS_ROOT).compile(BASELINE_BUNDLE)


@pytest.fixture(scope="module")
def compiled_alt() -> ResolvedArtifactBundle:
    """Compile the alt bundle once for the whole module."""
    return ConfigCompiler(configs_root=FIXTURES_ROOT).compile(ALT_BUNDLE)


# ── Behavioral: Dimension Count from Config ───────────────────────────────────


class TestDimensionCountFromConfig:
    def test_baseline_dimension_count_matches_rubric_yaml(self, compiled_base):
        """Dimension count must come from rubric config, not be hardcoded."""
        rubric_data = yaml.safe_load(
            (CONFIGS_ROOT / "rubrics/asap_set8_baseline.yaml").read_text()
        )
        expected = len(rubric_data["rubric_core"]["dimensions"])
        assert len(compiled_base.rubric_snapshot.dimensions) == expected

    def test_alt_dimension_count_matches_alt_rubric_yaml(self, compiled_alt):
        rubric_data = yaml.safe_load(
            (FIXTURES_ROOT / "rubrics/alt_rubric.yaml").read_text()
        )
        expected = len(rubric_data["rubric_core"]["dimensions"])
        assert len(compiled_alt.rubric_snapshot.dimensions) == expected

    def test_baseline_and_alt_dimension_counts_differ(self, compiled_base, compiled_alt):
        """If code hardcoded dimension count, both would return the same value."""
        base_count = len(compiled_base.rubric_snapshot.dimensions)
        alt_count = len(compiled_alt.rubric_snapshot.dimensions)
        assert base_count != alt_count, (
            f"Baseline ({base_count}) and alt ({alt_count}) must have different "
            f"dimension counts to prove count is config-driven."
        )

    def test_baseline_and_alt_lookup_maps_have_different_keys(self, compiled_base, compiled_alt):
        """Lookup map keys come from config dimension IDs, not hardcoded."""
        base_ids = set(compiled_base.rubric_snapshot.dimension_by_id.keys())
        alt_ids = set(compiled_alt.rubric_snapshot.dimension_by_id.keys())
        assert base_ids != alt_ids


# ── Behavioral: Trait Names from Config ──────────────────────────────────────


class TestTraitNamesFromConfig:
    def test_baseline_dimension_ids_match_config(self, compiled_base):
        """Dimension IDs in snapshot must match what is in the rubric YAML."""
        rubric_data = yaml.safe_load(
            (CONFIGS_ROOT / "rubrics/asap_set8_baseline.yaml").read_text()
        )
        expected_ids = {
            d["dimension_id"] for d in rubric_data["rubric_core"]["dimensions"]
        }
        actual_ids = set(compiled_base.rubric_snapshot.dimension_by_id.keys())
        assert actual_ids == expected_ids

    def test_alt_dimension_ids_match_alt_config(self, compiled_alt):
        rubric_data = yaml.safe_load(
            (FIXTURES_ROOT / "rubrics/alt_rubric.yaml").read_text()
        )
        expected_ids = {
            d["dimension_id"] for d in rubric_data["rubric_core"]["dimensions"]
        }
        actual_ids = set(compiled_alt.rubric_snapshot.dimension_by_id.keys())
        assert actual_ids == expected_ids

    def test_baseline_dimension_names_match_config(self, compiled_base):
        rubric_data = yaml.safe_load(
            (CONFIGS_ROOT / "rubrics/asap_set8_baseline.yaml").read_text()
        )
        expected_names = {
            d["name"] for d in rubric_data["rubric_core"]["dimensions"]
        }
        actual_names = {
            d["name"] for d in compiled_base.rubric_snapshot.dimensions
        }
        assert actual_names == expected_names

    def test_baseline_and_alt_dimension_names_fully_disjoint(self, compiled_base, compiled_alt):
        """If names were hardcoded, the alt config couldn't override them."""
        base_names = {d["name"] for d in compiled_base.rubric_snapshot.dimensions}
        alt_names = {d["name"] for d in compiled_alt.rubric_snapshot.dimensions}
        assert base_names.isdisjoint(alt_names), (
            f"Expected no overlap, but found: {base_names & alt_names}"
        )

    def test_baseline_dimension_codes_match_config(self, compiled_base):
        rubric_data = yaml.safe_load(
            (CONFIGS_ROOT / "rubrics/asap_set8_baseline.yaml").read_text()
        )
        expected_codes = {d["code"] for d in rubric_data["rubric_core"]["dimensions"]}
        actual_codes = set(compiled_base.rubric_snapshot.dimension_by_code.keys())
        assert actual_codes == expected_codes


# ── Behavioral: Scale Range from Config ──────────────────────────────────────


class TestScaleRangeFromConfig:
    def test_baseline_scale_id_matches_config(self, compiled_base):
        rubric_data = yaml.safe_load(
            (CONFIGS_ROOT / "rubrics/asap_set8_baseline.yaml").read_text()
        )
        expected_scale_ids = {
            s["scale_id"] for s in rubric_data["rubric_core"]["scales"]
        }
        actual_scale_ids = set(compiled_base.rubric_snapshot.scale_by_id.keys())
        assert actual_scale_ids == expected_scale_ids

    def test_baseline_scale_max_matches_config(self, compiled_base):
        rubric_data = yaml.safe_load(
            (CONFIGS_ROOT / "rubrics/asap_set8_baseline.yaml").read_text()
        )
        config_max = rubric_data["rubric_core"]["scales"][0]["max"]
        snapshot_max = compiled_base.rubric_snapshot.scales[0]["max"]
        assert snapshot_max == config_max

    def test_alt_scale_max_matches_alt_config(self, compiled_alt):
        rubric_data = yaml.safe_load(
            (FIXTURES_ROOT / "rubrics/alt_rubric.yaml").read_text()
        )
        config_max = rubric_data["rubric_core"]["scales"][0]["max"]
        snapshot_max = compiled_alt.rubric_snapshot.scales[0]["max"]
        assert snapshot_max == config_max

    def test_baseline_and_alt_scale_max_differ(self, compiled_base, compiled_alt):
        """If max scale were hardcoded, both would return the same value."""
        base_max = compiled_base.rubric_snapshot.scales[0]["max"]
        alt_max = compiled_alt.rubric_snapshot.scales[0]["max"]
        assert base_max != alt_max, (
            f"Baseline max={base_max} and alt max={alt_max} must differ."
        )

    def test_baseline_scale_validates_within_range(self, compiled_base):
        """Score validation uses scale from config, not hardcoded range."""
        # Get scale bounds from snapshot (not hardcoded)
        scale = compiled_base.rubric_snapshot.scales[0]
        scale_min = scale["min"]
        scale_max = scale["max"]
        first_dim_id = compiled_base.rubric_snapshot.dimensions[0]["dimension_id"]
        # Boundary valid scores
        assert compiled_base.rubric_snapshot.validate_score(first_dim_id, scale_min) is True
        assert compiled_base.rubric_snapshot.validate_score(first_dim_id, scale_max) is True
        # Out-of-range
        assert compiled_base.rubric_snapshot.validate_score(first_dim_id, scale_min - 1) is False
        assert compiled_base.rubric_snapshot.validate_score(first_dim_id, scale_max + 1) is False

    def test_alt_scale_rejects_score_above_its_max(self, compiled_base, compiled_alt):
        """A score valid in baseline may be out-of-range for alt scale."""
        base_max = compiled_base.rubric_snapshot.scales[0]["max"]
        alt_first_dim = compiled_alt.rubric_snapshot.dimensions[0]["dimension_id"]
        # base_max (e.g. 6) should be invalid for alt scale (max 4)
        assert compiled_alt.rubric_snapshot.validate_score(alt_first_dim, base_max) is False


# ── Behavioral: Composite Formula from Config ─────────────────────────────────


class TestCompositeFormulaFromConfig:
    def test_baseline_formula_variants_match_config(self, compiled_base):
        agg_data = yaml.safe_load(
            (CONFIGS_ROOT / "policies/aggregation/asap_set8_composite.yaml").read_text()
        )
        expected_variants = len(agg_data["aggregation_policy"]["composite_formula"])
        actual_variants = len(
            compiled_base.policy_snapshot.aggregation_policy["composite_formula"]
        )
        assert actual_variants == expected_variants

    def test_alt_formula_variants_match_alt_config(self, compiled_alt):
        agg_data = yaml.safe_load(
            (FIXTURES_ROOT / "policies/aggregation/alt_aggregation.yaml").read_text()
        )
        expected_variants = len(agg_data["aggregation_policy"]["composite_formula"])
        actual_variants = len(
            compiled_alt.policy_snapshot.aggregation_policy["composite_formula"]
        )
        assert actual_variants == expected_variants

    def test_baseline_and_alt_formula_variant_counts_differ(self, compiled_base, compiled_alt):
        base_count = len(
            compiled_base.policy_snapshot.aggregation_policy["composite_formula"]
        )
        alt_count = len(
            compiled_alt.policy_snapshot.aggregation_policy["composite_formula"]
        )
        assert base_count != alt_count

    def test_baseline_formula_weights_match_config(self, compiled_base):
        """Formula weights come from config, not hardcoded."""
        agg_data = yaml.safe_load(
            (CONFIGS_ROOT / "policies/aggregation/asap_set8_composite.yaml").read_text()
        )
        config_weights = agg_data["aggregation_policy"]["composite_formula"][0]["weights"]
        snapshot_weights = compiled_base.policy_snapshot.aggregation_policy[
            "composite_formula"
        ][0]["weights"]
        assert snapshot_weights == config_weights

    def test_baseline_and_alt_formula_weights_differ(self, compiled_base, compiled_alt):
        """If weights were hardcoded, swapping config wouldn't change them."""
        base_weights = set(
            compiled_base.policy_snapshot.aggregation_policy["composite_formula"][0][
                "weights"
            ].values()
        )
        alt_weights = set(
            compiled_alt.policy_snapshot.aggregation_policy["composite_formula"][0][
                "weights"
            ].values()
        )
        assert base_weights != alt_weights, (
            "Baseline and alt formula weights must differ to prove config-driven behavior."
        )

    def test_alt_weights_are_uniform(self, compiled_alt):
        """Alt config specifies equal weights; snapshot must reflect this."""
        weights = compiled_alt.policy_snapshot.aggregation_policy["composite_formula"][0][
            "weights"
        ]
        unique_values = set(weights.values())
        assert len(unique_values) == 1, (
            f"Alt config has equal weights but got multiple values: {weights}"
        )

    def test_baseline_weights_are_not_uniform(self, compiled_base):
        """Baseline config has varied weights (0, 2, 4); snapshot must reflect this."""
        weights = compiled_base.policy_snapshot.aggregation_policy["composite_formula"][0][
            "weights"
        ]
        unique_values = set(weights.values())
        assert len(unique_values) > 1, (
            f"Baseline config has varied weights but got uniform values: {weights}"
        )


# ── Behavioral: Display Annotation Policy from Config ─────────────────────────


class TestDisplayAnnotationFromConfig:
    def test_baseline_annotation_policy_matches_config(self, compiled_base):
        exp_data = yaml.safe_load(
            (CONFIGS_ROOT / "policies/explanation/engineering_eval_explanation.yaml").read_text()
        )
        config_policy = exp_data["explanation_policy"]["display_annotation_policy"]
        snapshot_policy = compiled_base.policy_snapshot.explanation_policy[
            "display_annotation_policy"
        ]
        assert snapshot_policy == config_policy

    def test_baseline_allows_range_annotations(self, compiled_base):
        """Baseline config sets allow_range_annotations: true."""
        policy = compiled_base.policy_snapshot.explanation_policy[
            "display_annotation_policy"
        ]
        assert policy["allow_range_annotations"] is True

    def test_alt_disallows_range_annotations(self, compiled_alt):
        """Alt config sets allow_range_annotations: false."""
        policy = compiled_alt.policy_snapshot.explanation_policy[
            "display_annotation_policy"
        ]
        assert policy["allow_range_annotations"] is False

    def test_baseline_and_alt_annotation_policy_differ(self, compiled_base, compiled_alt):
        """If annotation policy were hardcoded, swapping config wouldn't change it."""
        base_allows = compiled_base.policy_snapshot.explanation_policy[
            "display_annotation_policy"
        ]["allow_range_annotations"]
        alt_allows = compiled_alt.policy_snapshot.explanation_policy[
            "display_annotation_policy"
        ]["allow_range_annotations"]
        assert base_allows != alt_allows


# ── Behavioral: Adjudication Policy from Config ───────────────────────────────


class TestAdjudicationPolicyFromConfig:
    def test_baseline_trigger_count_matches_config(self, compiled_base):
        adj_data = yaml.safe_load(
            (CONFIGS_ROOT / "policies/adjudication/asap_set8_default.yaml").read_text()
        )
        expected = len(adj_data["adjudication_policy"]["triggers"])
        actual = len(compiled_base.policy_snapshot.adjudication_policy["triggers"])
        assert actual == expected

    def test_alt_trigger_count_matches_alt_config(self, compiled_alt):
        adj_data = yaml.safe_load(
            (FIXTURES_ROOT / "policies/adjudication/alt_adjudication.yaml").read_text()
        )
        expected = len(adj_data["adjudication_policy"]["triggers"])
        actual = len(compiled_alt.policy_snapshot.adjudication_policy["triggers"])
        assert actual == expected

    def test_baseline_and_alt_trigger_counts_differ(self, compiled_base, compiled_alt):
        base_count = len(compiled_base.policy_snapshot.adjudication_policy["triggers"])
        alt_count = len(compiled_alt.policy_snapshot.adjudication_policy["triggers"])
        assert base_count != alt_count

    def test_baseline_threshold_matches_config(self, compiled_base):
        adj_data = yaml.safe_load(
            (CONFIGS_ROOT / "policies/adjudication/asap_set8_default.yaml").read_text()
        )
        config_threshold = adj_data["adjudication_policy"]["triggers"][0]["threshold"]["value"]
        snapshot_threshold = compiled_base.policy_snapshot.adjudication_policy["triggers"][0][
            "threshold"
        ]["value"]
        assert snapshot_threshold == config_threshold

    def test_baseline_and_alt_thresholds_differ(self, compiled_base, compiled_alt):
        base_thresh = compiled_base.policy_snapshot.adjudication_policy["triggers"][0][
            "threshold"
        ]["value"]
        alt_thresh = compiled_alt.policy_snapshot.adjudication_policy["triggers"][0][
            "threshold"
        ]["value"]
        assert base_thresh != alt_thresh


# ── Static: No Business Facts in src/ Code ───────────────────────────────────


class TestNoBusinessFactsInSourceCode:
    """
    Scan src/**/*.py for known baseline business-fact identifiers.
    If any are found, that string is hardcoded — a firewall violation.

    The identifiers checked here are derived from the baseline rubric config
    at test time, NOT hardcoded in this test file itself.
    """

    @pytest.fixture(scope="class")
    def baseline_business_identifiers(self, compiled_base):
        """Extract unique, unambiguous identifiers from baseline config."""
        ids = []
        # Dimension IDs (snake_case, unambiguous)
        for dim in compiled_base.rubric_snapshot.dimensions:
            dim_id = dim["dimension_id"]
            # Only check multi-word IDs (single-word ones like "voice" are too generic)
            if "_" in dim_id:
                ids.append(dim_id)
        # Dimension names with spaces (very specific to this rubric)
        for dim in compiled_base.rubric_snapshot.dimensions:
            name = dim["name"]
            if " " in name:  # multi-word names are unambiguous
                ids.append(name)
        return ids

    @pytest.fixture(scope="class")
    def src_python_files(self):
        return list(SRC_DIR.rglob("*.py"))

    def test_baseline_dimension_ids_not_in_src(
        self, baseline_business_identifiers, src_python_files
    ):
        """No baseline dimension IDs should appear as literals in src/ code."""
        violations = []
        for py_file in src_python_files:
            content = py_file.read_text()
            for identifier in baseline_business_identifiers:
                if identifier in content:
                    violations.append(f"{py_file}: '{identifier}'")
        assert not violations, (
            "Hardcoded business facts found in src/ code.\n"
            "These identifiers must live in configs/, not code:\n"
            + "\n".join(violations)
        )

    def test_no_hardcoded_scale_id_in_src(self):
        """The baseline scale ID 'ordinal_6' must not appear in src/ code."""
        baseline_scale_id = "ordinal_6"
        for py_file in SRC_DIR.rglob("*.py"):
            content = py_file.read_text()
            assert baseline_scale_id not in content, (
                f"Hardcoded scale ID '{baseline_scale_id}' found in {py_file}. "
                f"Scale IDs must come from rubric config."
            )

    def test_no_hardcoded_policy_ids_in_src(self, compiled_base):
        """Policy IDs from config must not appear hardcoded in src/ code."""
        policy_ids = [
            compiled_base.policy_snapshot.adjudication_policy["policy_id"],
            compiled_base.policy_snapshot.aggregation_policy["policy_id"],
            compiled_base.policy_snapshot.explanation_policy["policy_id"],
        ]
        for py_file in SRC_DIR.rglob("*.py"):
            content = py_file.read_text()
            for policy_id in policy_ids:
                assert policy_id not in content, (
                    f"Hardcoded policy ID '{policy_id}' found in {py_file}."
                )

    def test_no_hardcoded_bundle_id_in_src(self, compiled_base):
        """Bundle ID from config must not appear hardcoded in src/ code."""
        bundle_id = compiled_base.artifact_bundle.bundle_id
        for py_file in SRC_DIR.rglob("*.py"):
            content = py_file.read_text()
            assert bundle_id not in content, (
                f"Hardcoded bundle ID '{bundle_id}' found in {py_file}."
            )
