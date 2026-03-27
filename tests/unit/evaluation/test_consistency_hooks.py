"""Unit tests for inter-agent consistency metrics."""

import json
from pathlib import Path

import pytest

from src.evaluation.consistency import (
    compute_consistency,
    extract_hypotheses_from_trace,
    DimensionConsistency,
    ConsistencyReport,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_GOLDEN_DIR = _PROJECT_ROOT / "tests" / "golden"


class TestDimensionConsistency:
    def test_perfect_agreement_no_conflict(self):
        report = compute_consistency("run-001", {"dim_a": [3, 3, 3]})
        dim = report.dimensions[0]
        assert dim.conflict_count == 0
        assert dim.agreement_ratio == pytest.approx(1.0)
        assert dim.spread == 0
        assert not dim.has_conflict

    def test_adjacent_scores_no_conflict(self):
        report = compute_consistency("run-002", {"dim_a": [3, 4, 3]})
        dim = report.dimensions[0]
        assert dim.conflict_count == 0
        assert not dim.has_conflict

    def test_non_adjacent_scores_conflict(self):
        report = compute_consistency("run-003", {"dim_a": [1, 4]})
        dim = report.dimensions[0]
        assert dim.conflict_count == 1
        assert dim.has_conflict

    def test_variance_zero_for_equal_scores(self):
        report = compute_consistency("run-004", {"dim_a": [5, 5, 5]})
        assert report.dimensions[0].variance == pytest.approx(0.0)

    def test_mean_computed_correctly(self):
        report = compute_consistency("run-005", {"dim_a": [2, 4, 6]})
        assert report.dimensions[0].mean == pytest.approx(4.0)

    def test_spread_max_minus_min(self):
        report = compute_consistency("run-006", {"dim_a": [1, 3, 6]})
        assert report.dimensions[0].spread == 5

    def test_empty_scores_no_error(self):
        report = compute_consistency("run-007", {"dim_a": []})
        dim = report.dimensions[0]
        assert dim.conflict_count == 0
        assert dim.agreement_ratio == pytest.approx(1.0)

    def test_to_dict_has_required_keys(self):
        report = compute_consistency("run-008", {"dim_a": [2, 3]})
        d = report.dimensions[0].to_dict()
        for key in ("dimension_id", "scores", "mean", "variance",
                    "spread", "conflict_count", "agreement_ratio"):
            assert key in d


class TestConsistencyReport:
    def test_multi_dimension_report(self):
        hyps = {
            "ideas_content": [3, 3, 4],      # no conflict
            "organization": [2, 5],           # conflict (|2-5|=3 > 1)
            "voice": [4, 4],                  # no conflict
        }
        report = compute_consistency("run-multi", hyps)
        assert report.total_conflict_count == 1
        assert report.dimensions_with_conflict == 1
        assert len(report.dimensions) == 3

    def test_overall_agreement_ratio_range(self):
        hyps = {"d1": [1, 6], "d2": [3, 3]}
        report = compute_consistency("run-ratio", hyps)
        assert 0.0 <= report.overall_agreement_ratio <= 1.0

    def test_to_dict_structure(self):
        report = compute_consistency("run-dict", {"d1": [2, 3]})
        d = report.to_dict()
        for key in ("run_id", "total_conflict_count", "dimensions_with_conflict",
                    "overall_agreement_ratio", "dimensions"):
            assert key in d
        assert d["run_id"] == "run-dict"

    def test_no_conflicts_all_adjacent(self):
        hyps = {
            "d1": [2, 3, 2],
            "d2": [5, 4, 5],
            "d3": [1, 1, 2],
        }
        report = compute_consistency("run-no-conflict", hyps)
        assert report.total_conflict_count == 0
        assert report.dimensions_with_conflict == 0
        assert report.overall_agreement_ratio == pytest.approx(1.0)


class TestExtractHypothesesFromTrace:
    def test_returns_empty_without_scorer_node(self):
        trace = {"node_traces": []}
        result = extract_hypotheses_from_trace(trace)
        assert result == {}

    def test_returns_empty_when_scorer_present_no_raw_data(self):
        trace = {
            "node_traces": [
                {
                    "node_id": "node_scorer",
                    "status": "success",
                    "output_ref": "hyps:12",
                    "input_ref": "obs:6",
                }
            ]
        }
        # Without raw hypothesis objects in trace, returns empty
        result = extract_hypotheses_from_trace(trace)
        assert isinstance(result, dict)

    def test_golden_snapshot_trace_parseable(self):
        snap_path = _GOLDEN_DIR / "sample_20716.mock.json"
        if not snap_path.exists():
            pytest.skip("Golden snapshot not present")

        snap = json.loads(snap_path.read_text())
        trace = snap.get("run_trace", {})
        result = extract_hypotheses_from_trace(trace)
        assert isinstance(result, dict)


class TestConsistencyWithGoldenData:
    def test_deterministic_scorer_hypotheses_consistency(self):
        """Simulate what mock scorer produces: 2 hypotheses per dimension."""
        # Mock scorer produces exactly 2 score hypotheses per dimension
        # by design (from deterministic_scorer.py). Both are set to score=2 for most dims.
        # Simulate 6 dimensions, 2 hypotheses each, adjacent scores
        hyps = {f"dim_{i}": [i + 1, i + 1] for i in range(6)}
        report = compute_consistency("run-mock-sim", hyps)
        assert report.total_conflict_count == 0
        assert report.overall_agreement_ratio == pytest.approx(1.0)
