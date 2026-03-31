"""Integration tests: replay pipeline runs and check for regressions against golden snapshots.

Tests:
  - replay_run.py replays a golden snapshot and produces valid artifacts
  - compare_snapshot.py finds no regressions between replayed run and golden
  - compare_snapshot.py detects artificial score regressions
  - replay works for both normal and adjudication-path samples
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_GOLDEN_DIR = _PROJECT_ROOT / "tests" / "golden"
_SCRIPTS = _PROJECT_ROOT / "tests" / "scripts"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


class TestReplayRun:
    @pytest.mark.parametrize("essay_id", ["20716", "20717"])
    def test_replay_produces_valid_artifacts(self, essay_id, tmp_path):
        snapshot = _GOLDEN_DIR / f"sample_{essay_id}.mock.json"
        assert snapshot.exists(), f"Golden snapshot missing: {snapshot}"

        result = _run([
            sys.executable, str(_SCRIPTS / "replay_run.py"),
            "--snapshot", str(snapshot),
            "--output-dir", str(tmp_path),
        ])
        assert result.returncode == 0, (
            f"replay_run.py failed for {essay_id}:\n{result.stdout}\n{result.stderr}"
        )

        # Artifacts must be present
        assert (tmp_path / "run_trace.json").exists()
        assert (tmp_path / "feedback.json").exists()

        # Trace must show completed status
        trace = json.loads((tmp_path / "run_trace.json").read_text())
        assert trace["status"] == "completed"
        assert trace["terminal_validation_passed"] is True

    @pytest.mark.parametrize("essay_id", ["20716", "20717"])
    def test_replay_no_regressions(self, essay_id, tmp_path):
        snapshot = _GOLDEN_DIR / f"sample_{essay_id}.mock.json"

        # Replay
        _run([
            sys.executable, str(_SCRIPTS / "replay_run.py"),
            "--snapshot", str(snapshot),
            "--output-dir", str(tmp_path),
        ])

        # Compare
        result = _run([
            sys.executable, str(_SCRIPTS / "compare_snapshot.py"),
            "--snapshot", str(snapshot),
            "--run-dir", str(tmp_path),
        ])
        assert result.returncode == 0, (
            f"compare_snapshot detected regression for {essay_id}:\n{result.stdout}\n{result.stderr}"
        )
        assert "PASS" in result.stdout, f"Expected PASS in output:\n{result.stdout}"

    def test_compare_detects_score_regression(self, tmp_path):
        """Artificially modify a replayed run's score and verify compare_snapshot catches it."""
        essay_id = "20716"
        snapshot = _GOLDEN_DIR / f"sample_{essay_id}.mock.json"

        # Replay first
        _run([
            sys.executable, str(_SCRIPTS / "replay_run.py"),
            "--snapshot", str(snapshot),
            "--output-dir", str(tmp_path),
        ])

        # Tamper with feedback: change first dimension score
        feedback_path = tmp_path / "feedback.json"
        feedback = json.loads(feedback_path.read_text())
        dims = feedback.get("dimensions", {})
        if dims:
            first_key = next(iter(dims))
            original = dims[first_key]["final_score"]
            dims[first_key]["final_score"] = (original % 6) + 1  # change score
        feedback_path.write_text(json.dumps(feedback, ensure_ascii=False))

        # Compare should now fail
        result = _run([
            sys.executable, str(_SCRIPTS / "compare_snapshot.py"),
            "--snapshot", str(snapshot),
            "--run-dir", str(tmp_path),
        ])
        assert result.returncode != 0, (
            "compare_snapshot should have detected the score regression but returned 0"
        )
        assert "FAIL" in (result.stdout + result.stderr)

    def test_adjudication_path_replay_preserves_conflict_polarity(self, tmp_path):
        """Replay of adjudication sample must preserve conflict polarity."""
        essay_id = "20717"
        snapshot = _GOLDEN_DIR / f"sample_{essay_id}.mock.json"

        _run([
            sys.executable, str(_SCRIPTS / "replay_run.py"),
            "--snapshot", str(snapshot),
            "--output-dir", str(tmp_path),
        ])

        trace = json.loads((tmp_path / "run_trace.json").read_text())
        checker = next(
            n for n in trace["node_traces"] if n["node_id"] == "node_consistency_checker"
        )
        output_ref = checker.get("output_ref", "")
        assert output_ref.startswith("conflicts:")
        conflict_count = int(output_ref.split(":")[1])
        assert conflict_count >= 1, (
            f"Replayed adjudication sample should have >= 1 conflict, got {conflict_count}"
        )
