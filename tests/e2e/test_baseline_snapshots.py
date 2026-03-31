"""Golden snapshot regression tests for mock baseline.

Each test re-runs the pipeline in mock mode and compares the output
against the stored golden snapshot. The comparison is structural:
  - same set of dimension IDs and final_score values
  - same status and terminal_validation_passed
  - same nodes visited (by node_id, ignoring timestamps and run_id)
  - adjudication: snapshot for 20717 must record conflict_count >= 1
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_GOLDEN_DIR = _PROJECT_ROOT / "tests" / "golden"
_BUNDLE = _PROJECT_ROOT / "configs" / "bundles" / "asap_set8_baseline.bundle.yaml"
_SAMPLES_DIR = _PROJECT_ROOT / "data" / "samples"


def _load_golden(essay_id: str) -> dict:
    path = _GOLDEN_DIR / f"sample_{essay_id}.mock.json"
    assert path.exists(), f"Golden snapshot not found: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _run_pipeline(essay_id: str, output_dir: Path) -> tuple[dict, dict]:
    input_file = _SAMPLES_DIR / f"sample_{essay_id}.txt"
    result = subprocess.run(
        [
            sys.executable,
            str(_PROJECT_ROOT / "tests" / "scripts" / "run_baseline.py"),
            "--bundle", str(_BUNDLE),
            "--provider", "mock",
            "--input-file", str(input_file),
            "--output-dir", str(output_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"tests/scripts/run_baseline.py failed for {essay_id}:\n{result.stdout}\n{result.stderr}"
    )
    trace = json.loads((output_dir / "run_trace.json").read_text())
    feedback = json.loads((output_dir / "feedback.json").read_text())
    return trace, feedback


class TestBaselineSnapshots:
    @pytest.mark.parametrize("essay_id", ["20716", "20717"])
    def test_snapshot_status_and_terminal_validation(self, essay_id, tmp_path):
        golden = _load_golden(essay_id)
        trace, feedback = _run_pipeline(essay_id, tmp_path)

        assert trace["status"] == golden["run_trace"]["status"], (
            f"Status mismatch for {essay_id}"
        )
        assert trace["terminal_validation_passed"] == golden["run_trace"]["terminal_validation_passed"], (
            f"terminal_validation_passed mismatch for {essay_id}"
        )

    @pytest.mark.parametrize("essay_id", ["20716", "20717"])
    def test_snapshot_nodes_visited(self, essay_id, tmp_path):
        golden = _load_golden(essay_id)
        trace, _ = _run_pipeline(essay_id, tmp_path)

        golden_nodes = [n["node_id"] for n in golden["run_trace"]["node_traces"]]
        current_nodes = [n["node_id"] for n in trace["node_traces"]]
        assert current_nodes == golden_nodes, (
            f"Node sequence mismatch for {essay_id}: {current_nodes} != {golden_nodes}"
        )

    @pytest.mark.parametrize("essay_id", ["20716", "20717"])
    def test_snapshot_dimension_ids_and_scores(self, essay_id, tmp_path):
        golden = _load_golden(essay_id)
        _, feedback = _run_pipeline(essay_id, tmp_path)

        golden_dims = golden["feedback"]["dimensions"]
        current_dims = feedback["dimensions"]

        assert set(current_dims.keys()) == set(golden_dims.keys()), (
            f"Dimension ID mismatch for {essay_id}"
        )
        for dim_id in golden_dims:
            assert current_dims[dim_id]["final_score"] == golden_dims[dim_id]["final_score"], (
                f"Score mismatch for {essay_id} dimension '{dim_id}': "
                f"{current_dims[dim_id]['final_score']} != {golden_dims[dim_id]['final_score']}"
            )

    def test_adjudication_snapshot_has_conflicts(self, tmp_path):
        """sample_20717 golden must show adjudication was triggered."""
        essay_id = "20717"
        golden = _load_golden(essay_id)
        checker_node = next(
            n for n in golden["run_trace"]["node_traces"]
            if n["node_id"] == "node_consistency_checker"
        )
        output_ref = checker_node.get("output_ref", "")
        assert output_ref.startswith("conflicts:"), (
            f"Expected conflicts output_ref, got: '{output_ref}'"
        )
        conflict_count = int(output_ref.split(":")[1])
        assert conflict_count >= 1, f"Expected >= 1 conflict in golden, got {conflict_count}"

    def test_adjudication_live_run_has_conflicts(self, tmp_path):
        """Live re-run of sample_20717 must also trigger adjudication."""
        trace, _ = _run_pipeline("20717", tmp_path)
        checker_node = next(
            n for n in trace["node_traces"]
            if n["node_id"] == "node_consistency_checker"
        )
        output_ref = checker_node.get("output_ref", "")
        assert output_ref.startswith("conflicts:")
        conflict_count = int(output_ref.split(":")[1])
        assert conflict_count >= 1, f"Expected >= 1 conflict in live run, got {conflict_count}"
