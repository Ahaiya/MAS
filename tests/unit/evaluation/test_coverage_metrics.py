"""
Unit tests for scripts/compute_coverage_metrics.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.compute_coverage_metrics import compute_metrics_for_essay


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_compute_metrics_basic_recall_precision_boundary(tmp_path: Path):
    essay_dir = tmp_path / "essay-1"
    essay_dir.mkdir(parents=True)

    _write_json(
        essay_dir / "evidence_spans.json",
        {
            "run_id": "run-1",
            "evidence_spans": [
                {
                    "span_id": "s1",
                    "dimension_id": "dim_a",
                    "unit_id": "u1",
                    "text_quote": "alpha",
                },
                {
                    "span_id": "s2",
                    "dimension_id": "dim_a",
                    "unit_id": "u3",
                    "text_quote": "beta",
                },
                {
                    "span_id": "s3",
                    "dimension_id": "dim_a",
                    "unit_id": None,
                    "text_quote": None,
                },
            ],
        },
    )
    _write_json(
        essay_dir / "observations.json",
        {
            "run_id": "run-1",
            "observations": [
                {"dimension_id": "dim_a", "coverage_miss_span_ids": ["s2"]},
            ],
            "coverage_plans": [
                {"dimension_id": "dim_a", "target_unit_ids": ["u1", "u2"]},
            ],
            "text_units": [
                {"unit_id": "u1", "text": "alpha text"},
                {"unit_id": "u2", "text": "middle text"},
                {"unit_id": "u3", "text": "beta text"},
            ],
        },
    )

    metrics = compute_metrics_for_essay(essay_dir)
    dim = metrics["per_dimension"]["dim_a"]

    assert dim["coverage_recall_rate"]["rate"] == 0.5
    assert dim["coverage_precision_rate"]["rate"] == 0.5
    assert dim["chunk_boundary_quality"]["cross_chunk_span_ratio"] == 0.0


def test_compute_metrics_detects_cross_chunk_and_unmatched_quotes(tmp_path: Path):
    essay_dir = tmp_path / "essay-2"
    essay_dir.mkdir(parents=True)

    _write_json(
        essay_dir / "evidence_spans.json",
        {
            "run_id": "run-2",
            "evidence_spans": [
                {
                    "span_id": "s1",
                    "dimension_id": "dim_a",
                    "unit_id": "u1",
                    "text_quote": "beta",
                },
                {
                    "span_id": "s2",
                    "dimension_id": "dim_a",
                    "unit_id": "u1",
                    "text_quote": "missing-quote",
                },
            ],
        },
    )
    _write_json(
        essay_dir / "observations.json",
        {
            "run_id": "run-2",
            "observations": [{"dimension_id": "dim_a", "coverage_miss_span_ids": []}],
            "coverage_plans": [{"dimension_id": "dim_a", "target_unit_ids": ["u1"]}],
            "text_units": [
                {"unit_id": "u1", "text": "alpha text"},
                {"unit_id": "u2", "text": "beta text"},
            ],
        },
    )

    metrics = compute_metrics_for_essay(essay_dir)
    dim = metrics["per_dimension"]["dim_a"]["chunk_boundary_quality"]

    assert dim["cross_chunk_spans"] == 1
    assert dim["unmatched_quote_spans"] == 1
    assert dim["cross_chunk_span_ratio"] == 0.5
    assert dim["boundary_failure_ratio"] == 1.0
