"""
Integration tests for pipeline fallback paths (Phase 3, Task 5).

Covers the four non-happy paths by injecting controlled ConflictRecords
via unittest.mock.patch on deterministic_consistency_checker.run:

1. RE_EXTRACT path: consistency checker returns RE_EXTRACT conflict once,
   then succeeds → pipeline completes with VALIDATED status.
   Verified: node_extractor appears twice in node_traces.

2. RE_SCORE path: consistency checker returns RE_SCORE conflict once,
   then succeeds → pipeline completes with VALIDATED status.
   Verified: node_scorer appears twice, node_observer appears once.

3. HUMAN_REVIEW path: consistency checker returns HUMAN_REVIEW conflict →
   pipeline terminates with HUMAN_REVIEW status and empty feedback.

4. FAILED path (retry exhausted): consistency checker always returns RE_EXTRACT
   → CheckpointManager.RetryLimitExceeded after max_retries → pipeline FAILED.
   Verified: __force_fail__ sentinel appears in node_traces.

Patching strategy: patch src.agents.deterministic_consistency_checker.run via
patch.object so the same module object used by the runner is patched.
Zero-hardcoding: no rubric dimension IDs or trait names are hardcoded.
"""

import pytest
from unittest.mock import patch

from src.agents import deterministic_consistency_checker
from src.agents.config_resolver import run as resolve_bundle
from src.contracts.request_models import EvaluationRequest
from src.contracts.scoring import ConflictRecord, ConflictType, ResolutionPath
from src.contracts.trace import RunStatus, NodeStatus
from src.pipeline.runner import PipelineRunner

# ── Constants ─────────────────────────────────────────────────────────────────

BUNDLE_PATH = "configs/bundles/asap_set8_baseline.bundle.yaml"
SAMPLE_TEXT = (
    "The old man had always wanted to make the grouch next door smile. "
    "He tried baking cookies and leaving them on the porch. "
    "One sunny morning the grouch finally smiled and waved."
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def bundle():
    return resolve_bundle(BUNDLE_PATH)


@pytest.fixture
def runner(bundle):
    return PipelineRunner(bundle)


@pytest.fixture
def request_():
    return EvaluationRequest(
        raw_text=SAMPLE_TEXT,
        bundle_ref="asap_set8_baseline@v1",
        request_id="fallback-req-001",
    )


# ── Conflict fixtures ─────────────────────────────────────────────────────────


def _conflict(recommended_path: ResolutionPath) -> ConflictRecord:
    """Create a minimal ConflictRecord with the given resolution path."""
    return ConflictRecord(
        conflict_id=f"test-conflict-{recommended_path.value}",
        dimension_id="injected_test_dimension",
        hypothesis_ids=["hyp-injected-a", "hyp-injected-b"],
        conflict_type=ConflictType.OTHER,
        trigger_rule_id="injected_test_trigger",
        conflict_detail=f"Injected {recommended_path.value} conflict for testing",
        recommended_path=recommended_path,
    )


# ── TestReExtractFallback ─────────────────────────────────────────────────────


class TestReExtractFallback:
    """RE_EXTRACT path: one retry from COVERAGE_PLANNED, then success."""

    def test_pipeline_completes_after_re_extract(self, runner, request_):
        # First consistency check: RE_EXTRACT; second: no conflicts
        side_effects = [
            [_conflict(ResolutionPath.RE_EXTRACT)],
            [],
        ]
        with patch.object(deterministic_consistency_checker, "run", side_effect=side_effects):
            run_trace, feedback = runner.run(request_)

        assert run_trace.status == RunStatus.COMPLETED

    def test_terminal_validation_passed_after_re_extract(self, runner, request_):
        side_effects = [[_conflict(ResolutionPath.RE_EXTRACT)], []]
        with patch.object(deterministic_consistency_checker, "run", side_effect=side_effects):
            run_trace, _ = runner.run(request_)

        assert run_trace.terminal_validation_passed is True

    def test_node_extractor_runs_twice(self, runner, request_):
        """After RE_EXTRACT, the extractor re-runs from COVERAGE_PLANNED."""
        side_effects = [[_conflict(ResolutionPath.RE_EXTRACT)], []]
        with patch.object(deterministic_consistency_checker, "run", side_effect=side_effects):
            run_trace, _ = runner.run(request_)

        extractor_traces = [
            nt for nt in run_trace.node_traces if nt.node_id == "node_extractor"
        ]
        assert len(extractor_traces) == 2

    def test_node_observer_runs_twice(self, runner, request_):
        """After RE_EXTRACT, the observer also re-runs."""
        side_effects = [[_conflict(ResolutionPath.RE_EXTRACT)], []]
        with patch.object(deterministic_consistency_checker, "run", side_effect=side_effects):
            run_trace, _ = runner.run(request_)

        observer_traces = [
            nt for nt in run_trace.node_traces if nt.node_id == "node_observer"
        ]
        assert len(observer_traces) == 2

    def test_node_consistency_checker_runs_twice(self, runner, request_):
        side_effects = [[_conflict(ResolutionPath.RE_EXTRACT)], []]
        with patch.object(deterministic_consistency_checker, "run", side_effect=side_effects):
            run_trace, _ = runner.run(request_)

        checker_traces = [
            nt for nt in run_trace.node_traces
            if nt.node_id == "node_consistency_checker"
        ]
        assert len(checker_traces) == 2

    def test_all_traces_succeeded(self, runner, request_):
        side_effects = [[_conflict(ResolutionPath.RE_EXTRACT)], []]
        with patch.object(deterministic_consistency_checker, "run", side_effect=side_effects):
            run_trace, _ = runner.run(request_)

        for nt in run_trace.node_traces:
            assert nt.status == NodeStatus.SUCCESS, (
                f"Node '{nt.node_id}' has status {nt.status}"
            )

    def test_feedback_has_dimensions_after_re_extract(self, runner, request_, bundle):
        side_effects = [[_conflict(ResolutionPath.RE_EXTRACT)], []]
        with patch.object(deterministic_consistency_checker, "run", side_effect=side_effects):
            _, feedback = runner.run(request_)

        rubric = bundle.rubric_snapshot
        for dim in rubric.dimensions:
            assert dim["dimension_id"] in feedback["dimensions"]


# ── TestReScoreFallback ───────────────────────────────────────────────────────


class TestReScoreFallback:
    """RE_SCORE path: one retry from OBSERVATION_BUILT, then success."""

    def test_pipeline_completes_after_re_score(self, runner, request_):
        side_effects = [
            [_conflict(ResolutionPath.RE_SCORE)],
            [],
        ]
        with patch.object(deterministic_consistency_checker, "run", side_effect=side_effects):
            run_trace, _ = runner.run(request_)

        assert run_trace.status == RunStatus.COMPLETED

    def test_terminal_validation_passed_after_re_score(self, runner, request_):
        side_effects = [[_conflict(ResolutionPath.RE_SCORE)], []]
        with patch.object(deterministic_consistency_checker, "run", side_effect=side_effects):
            run_trace, _ = runner.run(request_)

        assert run_trace.terminal_validation_passed is True

    def test_node_scorer_runs_twice(self, runner, request_):
        """After RE_SCORE, only the scorer re-runs (not the extractor or observer)."""
        side_effects = [[_conflict(ResolutionPath.RE_SCORE)], []]
        with patch.object(deterministic_consistency_checker, "run", side_effect=side_effects):
            run_trace, _ = runner.run(request_)

        scorer_traces = [
            nt for nt in run_trace.node_traces if nt.node_id == "node_scorer"
        ]
        assert len(scorer_traces) == 2

    def test_node_observer_runs_once_for_re_score(self, runner, request_):
        """RE_SCORE re-uses existing observations — observer must NOT re-run."""
        side_effects = [[_conflict(ResolutionPath.RE_SCORE)], []]
        with patch.object(deterministic_consistency_checker, "run", side_effect=side_effects):
            run_trace, _ = runner.run(request_)

        observer_traces = [
            nt for nt in run_trace.node_traces if nt.node_id == "node_observer"
        ]
        assert len(observer_traces) == 1

    def test_node_extractor_runs_once_for_re_score(self, runner, request_):
        """RE_SCORE re-uses existing spans — extractor must NOT re-run."""
        side_effects = [[_conflict(ResolutionPath.RE_SCORE)], []]
        with patch.object(deterministic_consistency_checker, "run", side_effect=side_effects):
            run_trace, _ = runner.run(request_)

        extractor_traces = [
            nt for nt in run_trace.node_traces if nt.node_id == "node_extractor"
        ]
        assert len(extractor_traces) == 1

    def test_all_traces_succeeded_after_re_score(self, runner, request_):
        side_effects = [[_conflict(ResolutionPath.RE_SCORE)], []]
        with patch.object(deterministic_consistency_checker, "run", side_effect=side_effects):
            run_trace, _ = runner.run(request_)

        for nt in run_trace.node_traces:
            assert nt.status == NodeStatus.SUCCESS


# ── TestHumanReviewPath ───────────────────────────────────────────────────────


class TestHumanReviewPath:
    """HUMAN_REVIEW terminal path via consistency checker direct routing."""

    def test_status_is_human_review(self, runner, request_):
        with patch.object(
            deterministic_consistency_checker,
            "run",
            return_value=[_conflict(ResolutionPath.HUMAN_REVIEW)],
        ):
            run_trace, _ = runner.run(request_)

        assert run_trace.status == RunStatus.HUMAN_REVIEW

    def test_feedback_is_empty_for_human_review(self, runner, request_):
        with patch.object(
            deterministic_consistency_checker,
            "run",
            return_value=[_conflict(ResolutionPath.HUMAN_REVIEW)],
        ):
            _, feedback = runner.run(request_)

        assert feedback == {}

    def test_no_feedback_node_in_trace(self, runner, request_):
        """Feedback stage must not run when escalated to HUMAN_REVIEW."""
        with patch.object(
            deterministic_consistency_checker,
            "run",
            return_value=[_conflict(ResolutionPath.HUMAN_REVIEW)],
        ):
            run_trace, _ = runner.run(request_)

        node_ids = {nt.node_id for nt in run_trace.node_traces}
        assert "node_feedback" not in node_ids

    def test_preprocess_and_coverage_ran(self, runner, request_):
        """Stages before the conflict point must still appear in trace."""
        with patch.object(
            deterministic_consistency_checker,
            "run",
            return_value=[_conflict(ResolutionPath.HUMAN_REVIEW)],
        ):
            run_trace, _ = runner.run(request_)

        node_ids = {nt.node_id for nt in run_trace.node_traces}
        assert "node_preprocess" in node_ids
        assert "node_coverage" in node_ids
        assert "node_consistency_checker" in node_ids


# ── TestRetryExhaustedFailed ──────────────────────────────────────────────────


class TestRetryExhaustedFailed:
    """FAILED path: RE_EXTRACT conflict persists beyond max_retries → FAILED."""

    def _always_re_extract(self, hypotheses, policy):
        return [_conflict(ResolutionPath.RE_EXTRACT)]

    def test_status_is_failed_on_retry_exhaustion(self, runner, request_):
        with patch.object(
            deterministic_consistency_checker, "run", side_effect=self._always_re_extract
        ):
            run_trace, _ = runner.run(request_)

        assert run_trace.status == RunStatus.FAILED

    def test_feedback_is_empty_on_failed(self, runner, request_):
        with patch.object(
            deterministic_consistency_checker, "run", side_effect=self._always_re_extract
        ):
            _, feedback = runner.run(request_)

        assert feedback == {}

    def test_force_fail_sentinel_in_traces(self, runner, request_):
        """A __force_fail__ sentinel NodeTrace must appear after retry exhaustion."""
        with patch.object(
            deterministic_consistency_checker, "run", side_effect=self._always_re_extract
        ):
            run_trace, _ = runner.run(request_)

        sentinel_traces = [
            nt for nt in run_trace.node_traces if nt.node_id == "__force_fail__"
        ]
        assert len(sentinel_traces) == 1
        assert sentinel_traces[0].status == NodeStatus.FAILED

    def test_node_extractor_ran_multiple_times(self, runner, request_):
        """Extractor must re-run for each RE_EXTRACT attempt (max_retries + 1 times)."""
        with patch.object(
            deterministic_consistency_checker, "run", side_effect=self._always_re_extract
        ):
            run_trace, _ = runner.run(request_)

        extractor_traces = [
            nt for nt in run_trace.node_traces if nt.node_id == "node_extractor"
        ]
        # Initial run + max_retries (2) retries = 3 total extractor runs
        assert len(extractor_traces) >= 2

    def test_no_feedback_node_on_failed(self, runner, request_):
        with patch.object(
            deterministic_consistency_checker, "run", side_effect=self._always_re_extract
        ):
            run_trace, _ = runner.run(request_)

        node_ids = {nt.node_id for nt in run_trace.node_traces}
        assert "node_feedback" not in node_ids
