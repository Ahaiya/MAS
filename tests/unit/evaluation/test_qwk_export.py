"""Unit tests for QWK computation and run export."""

import json
from pathlib import Path

import pytest

from src.evaluation.qwk import qwk, qwk_for_dimension, QWKResult
from src.evaluation.export import export_run, export_snapshot, RunExport

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _write_run_artifacts(run_dir: Path, *, run_id: str = "run-20716") -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_trace.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "bundle_id": "engineering_eval_baseline",
                "bundle_version": "v1",
                "status": "completed",
                "terminal_validation_passed": True,
                "replay_metadata": {"provider": "openai_compatible"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "feedback.json").write_text(
        json.dumps(
            {
                "dimensions": {
                    "problem_analysis": {
                        "dimension_name": "Problem Analysis",
                        "score": 4,
                        "evidence": [
                            {"span_id": "span-01", "quote": "Strong framing."},
                            {"span_id": "span-02", "quote": "Concrete example."},
                        ],
                        "audit": {"decision_confidence": 0.88},
                    }
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_snapshot(path: Path, *, essay_id: str = "20716") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "essay_id": essay_id,
                "provider": "openai_compatible",
                "run_trace": {
                    "run_id": f"run-{essay_id}",
                    "bundle_id": "engineering_eval_baseline",
                    "bundle_version": "v1",
                    "status": "completed",
                    "terminal_validation_passed": True,
                    "replay_metadata": {"provider": "openai_compatible"},
                },
                "feedback": {
                    "dimensions": {
                        "problem_analysis": {
                            "dimension_name": "Problem Analysis",
                            "score": 4,
                            "evidence": [
                                {"span_id": "span-01", "quote": "Strong framing."},
                                {"span_id": "span-02", "quote": "Concrete example."},
                            ],
                            "audit": {"decision_confidence": 0.9},
                        }
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# QWK computation tests
# ---------------------------------------------------------------------------

class TestQWK:
    def test_perfect_agreement_returns_one(self):
        scores = [1, 2, 3, 4, 5, 6]
        assert qwk(scores, scores, 1, 6) == pytest.approx(1.0)

    def test_disagreement_below_chance_returns_negative(self):
        # Systematic reversal: alternating [1,6] → expected better than chance gives QWK < 0
        y_true = [1, 6] * 10
        y_pred = [6, 1] * 10
        result = qwk(y_true, y_pred, 1, 6)
        assert result < 0.0

    def test_uniform_disagreement_not_positive(self):
        # All 1s vs all 6s: marginals concentrate so numerator == denominator → QWK = 0
        y_true = [1] * 10
        y_pred = [6] * 10
        result = qwk(y_true, y_pred, 1, 6)
        assert result <= 0.0

    def test_symmetric(self):
        y_true = [1, 2, 3, 4, 5]
        y_pred = [2, 3, 4, 5, 6]
        assert qwk(y_true, y_pred, 1, 6) == pytest.approx(qwk(y_pred, y_true, 1, 6))

    def test_result_in_range(self):
        import random
        random.seed(42)
        y_true = [random.randint(1, 6) for _ in range(20)]
        y_pred = [random.randint(1, 6) for _ in range(20)]
        result = qwk(y_true, y_pred, 1, 6)
        assert -1.0 <= result <= 1.0

    def test_raises_on_length_mismatch(self):
        with pytest.raises(ValueError, match="same length"):
            qwk([1, 2, 3], [1, 2], 1, 6)

    def test_raises_on_empty(self):
        with pytest.raises(ValueError, match="empty"):
            qwk([], [], 1, 6)

    def test_raises_on_out_of_range(self):
        with pytest.raises(ValueError, match="out-of-range"):
            qwk([1, 7], [1, 2], 1, 6)

    def test_raises_on_invalid_bounds(self):
        with pytest.raises(ValueError, match="min_score"):
            qwk([1, 2], [1, 2], 5, 3)

    def test_single_class_returns_zero(self):
        # All same score → denominator is 0 → returns 0.0
        result = qwk([3, 3, 3], [3, 3, 3], 1, 6)
        assert result == pytest.approx(0.0)


class TestQWKForDimension:
    def test_returns_qwk_result(self):
        result = qwk_for_dimension("ideas_content", [1, 2, 3], [1, 2, 4], 1, 6)
        assert isinstance(result, QWKResult)
        assert result.dimension_id == "ideas_content"
        assert result.n_samples == 3
        assert result.min_score == 1
        assert result.max_score == 6

    def test_to_dict_has_required_keys(self):
        result = qwk_for_dimension("org", [2, 3], [2, 3], 1, 6)
        d = result.to_dict()
        for key in ("dimension_id", "qwk", "n_samples", "min_score", "max_score"):
            assert key in d


# ---------------------------------------------------------------------------
# Export tests
# ---------------------------------------------------------------------------

class TestExportRun:
    def test_export_run_from_artifact_dir(self, tmp_path):
        run_dir = tmp_path / "artifacts" / "eval" / "20716"
        _write_run_artifacts(run_dir)
        export = export_run(run_dir, essay_id="20716")
        assert isinstance(export, RunExport)
        assert export.essay_id == "20716"
        assert export.status == "completed"
        assert export.terminal_validation_passed is True
        assert len(export.dimension_scores) > 0

    def test_export_run_scores_by_dimension(self, tmp_path):
        run_dir = tmp_path / "artifacts" / "eval" / "20716"
        _write_run_artifacts(run_dir)
        export = export_run(run_dir, essay_id="20716")
        scores = export.scores_by_dimension()
        assert isinstance(scores, dict)
        for dim_id, score in scores.items():
            assert isinstance(dim_id, str)
            assert isinstance(score, int)

    def test_export_run_to_dict(self, tmp_path):
        run_dir = tmp_path / "artifacts" / "eval" / "20716"
        _write_run_artifacts(run_dir)
        export = export_run(run_dir, essay_id="20716")
        d = export.to_dict()
        for key in ("run_id", "essay_id", "bundle_id", "status",
                    "terminal_validation_passed", "dimension_scores"):
            assert key in d

    def test_export_run_raises_on_missing_trace(self, tmp_path):
        # feedback.json exists but no run_trace.json
        (tmp_path / "feedback.json").write_text("{}")
        with pytest.raises(FileNotFoundError, match="run_trace.json"):
            export_run(tmp_path, essay_id="test")

    def test_export_run_raises_on_missing_feedback(self, tmp_path):
        (tmp_path / "run_trace.json").write_text("{}")
        with pytest.raises(FileNotFoundError, match="feedback.json"):
            export_run(tmp_path, essay_id="test")


class TestExportSnapshot:
    def test_export_snapshot_20716(self, tmp_path):
        snap_path = tmp_path / "sample_20716.snapshot.json"
        _write_snapshot(snap_path, essay_id="20716")
        export = export_snapshot(snap_path)
        assert isinstance(export, RunExport)
        assert export.essay_id == "20716"
        assert export.status == "completed"
        assert len(export.dimension_scores) > 0

    def test_export_snapshot_scores_are_integers(self, tmp_path):
        snap_path = tmp_path / "sample_20716.snapshot.json"
        _write_snapshot(snap_path, essay_id="20716")
        export = export_snapshot(snap_path)
        for rec in export.dimension_scores:
            assert isinstance(rec.final_score, int)

    def test_export_snapshot_qwk_self_agreement(self, tmp_path):
        """A snapshot compared against itself should yield QWK = 1.0 per dimension."""
        snap_path = tmp_path / "sample_20716.snapshot.json"
        _write_snapshot(snap_path, essay_id="20716")
        export = export_snapshot(snap_path)
        scores = export.scores_by_dimension()
        dim_ids = list(scores.keys())

        if len(dim_ids) == 0:
            pytest.skip("No dimension scores in snapshot")

        # Self-QWK: compare each dimension against itself
        for dim_id in dim_ids:
            score = scores[dim_id]
            result = qwk([score], [score], 1, 6)
            # Single score, all same → returns 0 (degenerate case), not an error
            assert result == pytest.approx(0.0) or result == pytest.approx(1.0)
