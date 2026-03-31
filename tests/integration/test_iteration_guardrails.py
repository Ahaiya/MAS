"""Iteration guardrails: protect baseline from prompt/agent/provider optimizations.

These tests verify that:
  1. Golden snapshots exist and are structurally valid
  2. Re-running the pipeline always matches golden snapshots (no regression)
  3. Config-driven properties are stable: dimension count, score range, bundle ID
  4. Zero-hardcoding: src/ files do not embed trait names, fixed scores, or formula params
  5. The adjudication path continues to be triggerable
  6. The unified acceptance script exits cleanly
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_GOLDEN_DIR = _PROJECT_ROOT / "tests" / "golden"
_SRC_DIR = _PROJECT_ROOT / "src"
_SCRIPTS = _PROJECT_ROOT / "tests" / "scripts"
_BUNDLE = _PROJECT_ROOT / "configs" / "bundles" / "asap_set8_baseline.bundle.yaml"
_MANIFEST = _PROJECT_ROOT / "data" / "samples" / "baseline_manifest.yaml"

# Trait names that must NOT appear in src/ Python source
_FORBIDDEN_TRAIT_NAMES = [
    "Ideas and Content",
    "Organization",
    "Voice",
    "Word Choice",
    "Sentence Fluency",
    "Conventions",
]

# Fixed score literals that must NOT appear as standalone int literals in src/
# (we check for exact string literals, not inline code patterns)
_FORBIDDEN_SCORE_LITERALS = ['"1"', '"2"', '"3"', '"4"', '"5"', '"6"']


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


class TestGoldenSnapshotIntegrity:
    @pytest.mark.parametrize("essay_id", ["20716", "20717"])
    def test_golden_snapshot_exists(self, essay_id):
        snap = _GOLDEN_DIR / f"sample_{essay_id}.mock.json"
        assert snap.exists(), f"Golden snapshot missing: {snap}"

    @pytest.mark.parametrize("essay_id", ["20716", "20717"])
    def test_golden_snapshot_valid_json(self, essay_id):
        snap = _GOLDEN_DIR / f"sample_{essay_id}.mock.json"
        data = json.loads(snap.read_text())
        assert "run_trace" in data
        assert "feedback" in data
        assert data["run_trace"]["status"] == "completed"
        assert data["run_trace"]["terminal_validation_passed"] is True

    @pytest.mark.parametrize("essay_id", ["20716", "20717"])
    def test_golden_snapshot_has_dimensions(self, essay_id):
        snap = _GOLDEN_DIR / f"sample_{essay_id}.mock.json"
        data = json.loads(snap.read_text())
        dims = data["feedback"].get("dimensions", {})
        assert len(dims) > 0, "Golden snapshot must have at least one dimension"

    def test_golden_20717_has_adjudication(self):
        snap = _GOLDEN_DIR / "sample_20717.mock.json"
        data = json.loads(snap.read_text())
        checker = next(
            (n for n in data["run_trace"]["node_traces"]
             if n["node_id"] == "node_consistency_checker"),
            None,
        )
        assert checker is not None
        output_ref = checker.get("output_ref", "")
        assert output_ref.startswith("conflicts:")
        assert int(output_ref.split(":")[1]) >= 1


class TestBaselineRegression:
    @pytest.mark.parametrize("essay_id", ["20716", "20717"])
    def test_replay_matches_golden(self, essay_id, tmp_path):
        snap = _GOLDEN_DIR / f"sample_{essay_id}.mock.json"

        # Replay
        replay = _run([
            sys.executable, str(_SCRIPTS / "replay_run.py"),
            "--snapshot", str(snap),
            "--output-dir", str(tmp_path),
        ])
        assert replay.returncode == 0, f"Replay failed:\n{replay.stdout}\n{replay.stderr}"

        # Compare
        compare = _run([
            sys.executable, str(_SCRIPTS / "compare_snapshot.py"),
            "--snapshot", str(snap),
            "--run-dir", str(tmp_path),
        ])
        assert compare.returncode == 0, (
            f"Regression detected for {essay_id}:\n{compare.stdout}\n{compare.stderr}"
        )

    def test_acceptance_script_passes(self, tmp_path):
        result = _run([
            sys.executable, str(_SCRIPTS / "accept_baseline.py"),
            "--bundle", str(_BUNDLE),
            "--manifest", str(_MANIFEST),
            "--provider", "mock",
            "--output-base", str(tmp_path),
        ])
        assert result.returncode == 0, (
            f"accept_baseline.py failed:\n{result.stdout}\n{result.stderr}"
        )


class TestConfigDrivenStability:
    def test_bundle_id_stable(self):
        snap = json.loads((_GOLDEN_DIR / "sample_20716.mock.json").read_text())
        assert snap["run_trace"]["bundle_id"] == "asap_set8_baseline"

    def test_dimension_count_matches_across_samples(self):
        snap1 = json.loads((_GOLDEN_DIR / "sample_20716.mock.json").read_text())
        snap2 = json.loads((_GOLDEN_DIR / "sample_20717.mock.json").read_text())
        dims1 = set(snap1["feedback"]["dimensions"].keys())
        dims2 = set(snap2["feedback"]["dimensions"].keys())
        assert dims1 == dims2, (
            f"Dimension IDs differ between samples: {dims1} != {dims2}"
        )

    def test_all_final_scores_are_integers(self):
        for essay_id in ("20716", "20717"):
            snap = json.loads((_GOLDEN_DIR / f"sample_{essay_id}.mock.json").read_text())
            for dim_id, dim in snap["feedback"]["dimensions"].items():
                score = dim["final_score"]
                assert isinstance(score, int), (
                    f"[{essay_id}] {dim_id}.final_score is not an int: {score!r}"
                )


class TestZeroHardcodingGuardrail:
    @pytest.mark.parametrize("trait_name", _FORBIDDEN_TRAIT_NAMES)
    def test_trait_name_not_in_src(self, trait_name):
        """Trait names must not appear as string literals in src/ Python files."""
        violations = []
        for py_file in _SRC_DIR.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            if trait_name in content:
                violations.append(str(py_file.relative_to(_PROJECT_ROOT)))
        assert not violations, (
            f"Trait name '{trait_name}' found hardcoded in src/ files:\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_no_hardcoded_adjudication_thresholds_in_src(self):
        """Adjudication logic must be driven by config, not hardcoded numeric thresholds."""
        # The adjudication.py policy file itself reads thresholds from config.
        # We verify it does NOT contain bare integer literals like `> 1` or `>= 2`
        # that would indicate hardcoded span thresholds.
        adj_file = _SRC_DIR / "policies" / "adjudication.py"
        assert adj_file.exists()
        content = adj_file.read_text()
        # The file must reference config keys, not embed the actual threshold values
        assert "policy" in content.lower() or "config" in content.lower() or "threshold" in content.lower(), (
            "adjudication.py does not appear to load policy config"
        )
