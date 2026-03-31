"""
Tests for chunking policy config wiring (Stage G4).
"""

from __future__ import annotations

from pathlib import Path

from src.agents.config_resolver import run as resolve_bundle
from src.config.resolver import ConfigResolver

CONFIGS_ROOT = Path("configs")
BUNDLE_PATH = CONFIGS_ROOT / "bundles" / "asap_set8_baseline.bundle.yaml"


def test_chunking_policy_file_resolves_from_bundle_ref():
    resolver = ConfigResolver(CONFIGS_ROOT)
    bundle = resolver.load_bundle_file(BUNDLE_PATH)

    assert bundle.chunking_policy_ref is not None
    loaded = resolver.load_artifact(bundle.chunking_policy_ref)
    assert loaded.loaded_data is not None
    assert loaded.loaded_data.get("chunking_policy", {}).get("policy_id")


def test_chunking_policy_per_dimension_top_k_is_readable_from_policy_snapshot():
    resolved = resolve_bundle(BUNDLE_PATH)
    chunking_policy = resolved.policy_snapshot.chunking_policy
    coverage_cfg = chunking_policy.get("coverage", {})
    per_dim_top_k = coverage_cfg.get("per_dimension_top_k", {})
    default_top_k = coverage_cfg.get("default_top_k", 0)

    assert isinstance(default_top_k, int)
    assert default_top_k > 0
    assert isinstance(per_dim_top_k, dict)

    # Every rubric dimension should have a positive effective top_k.
    for dim in resolved.rubric_snapshot.dimensions:
        dim_id = dim["dimension_id"]
        effective_top_k = per_dim_top_k.get(dim_id, default_top_k)
        assert isinstance(effective_top_k, int)
        assert effective_top_k > 0
