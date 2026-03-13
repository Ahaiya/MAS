"""
E2E tests for the mock baseline normal path (Phase 3, Task 5).

Uses the real sample essay data/samples/sample_20716.txt and the real
bundle configs/bundles/asap_set8_baseline.bundle.yaml. No mocking.

Covers:
- Full pipeline run from EvaluationRequest to RunTrace with VALIDATED status
- All rubric dimensions appear in feedback (count and IDs from config, not hardcoded)
- All final scores are within the scale range defined by the rubric config
- Expected pipeline node sequence is present in node_traces
- All node traces carry SUCCESS status
- RunTrace roundtrip serialization
- Structural determinism: same input → same ordered node_id sequence

Zero-hardcoding: dimension IDs and rater labels are read from the
bundle's RubricSnapshot, never asserted as literals in test assertions.
"""

import pytest
from pathlib import Path

from src.agents.mock_config_resolver import run as resolve_bundle
from src.contracts.request_models import EvaluationRequest
from src.contracts.trace import RunStatus, NodeStatus, RunTrace
from src.pipeline.runner import PipelineRunner

# ── Fixtures ──────────────────────────────────────────────────────────────────

BUNDLE_PATH = "configs/bundles/asap_set8_baseline.bundle.yaml"
SAMPLE_FILE = "data/samples/sample_20716.txt"

# Expected pipeline node sequence for the normal path (no adjudication).
# When there happen to be scoring conflicts in the mock, node_adjudicator
# may also appear — we assert on presence, not exact position.
REQUIRED_NODES = [
    "node_preprocess",
    "node_coverage",
    "node_extractor",
    "node_observer",
    "node_scorer",
    "node_consistency_checker",
    "node_feedback",
]


@pytest.fixture(scope="module")
def bundle():
    return resolve_bundle(BUNDLE_PATH)


@pytest.fixture(scope="module")
def sample_text():
    return Path(SAMPLE_FILE).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def runner(bundle):
    return PipelineRunner(bundle)


@pytest.fixture(scope="module")
def pipeline_result(runner, sample_text):
    request = EvaluationRequest(
        raw_text=sample_text,
        bundle_ref="asap_set8_baseline@v1",
        request_id="e2e-req-sample20716",
    )
    return runner.run(request)


# ── TestStatusAndTermination ──────────────────────────────────────────────────


class TestStatusAndTermination:
    def test_status_is_completed(self, pipeline_result):
        run_trace, _ = pipeline_result
        assert run_trace.status == RunStatus.COMPLETED

    def test_terminal_validation_passed(self, pipeline_result):
        run_trace, _ = pipeline_result
        assert run_trace.terminal_validation_passed is True

    def test_replay_metadata_provider_is_mock(self, pipeline_result):
        run_trace, _ = pipeline_result
        assert run_trace.replay_metadata.get("provider") == "mock"

    def test_finished_after_started(self, pipeline_result):
        run_trace, _ = pipeline_result
        assert run_trace.finished_at > run_trace.started_at

    def test_run_id_is_non_empty(self, pipeline_result):
        run_trace, _ = pipeline_result
        assert run_trace.run_id and run_trace.run_id.startswith("run-")


# ── TestNodeTraces ────────────────────────────────────────────────────────────


class TestNodeTraces:
    def test_node_traces_non_empty(self, pipeline_result):
        run_trace, _ = pipeline_result
        assert len(run_trace.node_traces) > 0

    def test_all_node_traces_succeeded(self, pipeline_result):
        run_trace, _ = pipeline_result
        for nt in run_trace.node_traces:
            assert nt.status == NodeStatus.SUCCESS, (
                f"Node '{nt.node_id}' has status {nt.status}"
            )

    def test_all_required_nodes_present(self, pipeline_result):
        run_trace, _ = pipeline_result
        node_ids = {nt.node_id for nt in run_trace.node_traces}
        for expected in REQUIRED_NODES:
            assert expected in node_ids, f"Missing expected node: '{expected}'"

    def test_preprocess_node_first(self, pipeline_result):
        run_trace, _ = pipeline_result
        assert run_trace.node_traces[0].node_id == "node_preprocess"

    def test_feedback_node_last(self, pipeline_result):
        run_trace, _ = pipeline_result
        assert run_trace.node_traces[-1].node_id == "node_feedback"

    def test_node_traces_have_timestamps(self, pipeline_result):
        run_trace, _ = pipeline_result
        for nt in run_trace.node_traces:
            assert nt.started_at is not None
            assert nt.finished_at is not None
            assert nt.finished_at >= nt.started_at

    def test_node_traces_have_run_id(self, pipeline_result):
        run_trace, _ = pipeline_result
        for nt in run_trace.node_traces:
            assert nt.run_id == run_trace.run_id


# ── TestFeedbackOutput ────────────────────────────────────────────────────────


class TestFeedbackOutput:
    def test_feedback_has_dimensions_key(self, pipeline_result):
        _, feedback = pipeline_result
        assert "dimensions" in feedback

    def test_feedback_dimension_count_matches_rubric(self, pipeline_result, bundle):
        _, feedback = pipeline_result
        rubric = bundle.rubric_snapshot
        assert len(feedback["dimensions"]) == len(rubric.dimensions)

    def test_feedback_contains_all_rubric_dimension_ids(self, pipeline_result, bundle):
        _, feedback = pipeline_result
        rubric = bundle.rubric_snapshot
        for dim in rubric.dimensions:
            dim_id = dim["dimension_id"]
            assert dim_id in feedback["dimensions"], (
                f"Dimension '{dim_id}' missing from feedback"
            )

    def test_feedback_scores_are_integers(self, pipeline_result, bundle):
        _, feedback = pipeline_result
        rubric = bundle.rubric_snapshot
        for dim in rubric.dimensions:
            score = feedback["dimensions"][dim["dimension_id"]]["final_score"]
            assert isinstance(score, int)

    def test_feedback_scores_within_scale_range(self, pipeline_result, bundle):
        _, feedback = pipeline_result
        rubric = bundle.rubric_snapshot
        for dim in rubric.dimensions:
            dim_id = dim["dimension_id"]
            score = feedback["dimensions"][dim_id]["final_score"]
            assert rubric.validate_score(dim_id, score), (
                f"Score {score} out of scale range for dimension '{dim_id}'"
            )

    def test_feedback_dimension_names_from_config(self, pipeline_result, bundle):
        _, feedback = pipeline_result
        rubric = bundle.rubric_snapshot
        for dim in rubric.dimensions:
            dim_id = dim["dimension_id"]
            name_in_feedback = feedback["dimensions"][dim_id]["dimension_name"]
            name_in_config = dim["name"]
            assert name_in_feedback == name_in_config

    def test_feedback_has_generated_at(self, pipeline_result):
        _, feedback = pipeline_result
        assert "generated_at" in feedback


# ── TestSerialization ─────────────────────────────────────────────────────────


class TestSerialization:
    def test_run_trace_roundtrip(self, pipeline_result):
        run_trace, _ = pipeline_result
        restored = RunTrace.from_dict(run_trace.to_dict())
        assert restored.run_id == run_trace.run_id
        assert restored.status == run_trace.status
        assert restored.terminal_validation_passed == run_trace.terminal_validation_passed
        assert len(restored.node_traces) == len(run_trace.node_traces)

    def test_run_trace_node_ids_preserved_after_roundtrip(self, pipeline_result):
        run_trace, _ = pipeline_result
        restored = RunTrace.from_dict(run_trace.to_dict())
        original_ids = [nt.node_id for nt in run_trace.node_traces]
        restored_ids = [nt.node_id for nt in restored.node_traces]
        assert original_ids == restored_ids


# ── TestDeterminism ───────────────────────────────────────────────────────────


class TestDeterminism:
    def test_two_runs_have_different_run_ids(self, runner, sample_text):
        request = EvaluationRequest(
            raw_text=sample_text,
            bundle_ref="asap_set8_baseline@v1",
            request_id="e2e-det-001",
        )
        rt1, _ = runner.run(request)
        rt2, _ = runner.run(request)
        assert rt1.run_id != rt2.run_id

    def test_two_runs_produce_same_node_id_sequence(self, runner, sample_text):
        request = EvaluationRequest(
            raw_text=sample_text,
            bundle_ref="asap_set8_baseline@v1",
            request_id="e2e-det-002",
        )
        rt1, _ = runner.run(request)
        rt2, _ = runner.run(request)
        assert [nt.node_id for nt in rt1.node_traces] == [
            nt.node_id for nt in rt2.node_traces
        ]

    def test_two_runs_produce_same_feedback_scores(self, runner, sample_text):
        request = EvaluationRequest(
            raw_text=sample_text,
            bundle_ref="asap_set8_baseline@v1",
            request_id="e2e-det-003",
        )
        _, fb1 = runner.run(request)
        _, fb2 = runner.run(request)
        assert fb1["dimensions"] == fb2["dimensions"]
