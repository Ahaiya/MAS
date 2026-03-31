"""
Integration tests for the mock evaluation pipeline (Phase 3, Task 4).

Covers:
- PipelineRunner.run() on normal path (no unresolvable conflicts)
- RunTrace structure: status COMPLETED, terminal_validation_passed=True
- All expected pipeline stages appear in node_traces
- Feedback dict contains all rubric dimensions (from config, not hardcoded)
- Validators: coverage, observation, hypothesis, decision, terminal

Uses:
- Real bundle: configs/bundles/asap_set8_baseline.bundle.yaml
- Inline sample text (no file I/O in tests)
- Minimal RubricSnapshot/PolicySnapshot fixtures for validator unit tests

Zero-hardcoding: dimension IDs and rater labels are read from the bundle's
RubricSnapshot / PolicySnapshot, never hardcoded in test assertions.
"""

import pytest
from typing import Dict, List, Any

from src.contracts.artifact_bundle import RubricSnapshot, PolicySnapshot
from src.contracts.request_models import EvaluationRequest, CoveragePlan
from src.contracts.evidence import DimensionObservation, ObservationConfidence
from src.contracts.scoring import (
    ScoreHypothesis, FinalDimensionDecision, ConflictRecord,
)
from src.contracts.score_representation import create_score_representation
from src.contracts.trace import RunTrace, RunStatus, NodeStatus

from src.agents import (
    config_resolver,
    coverage,
    deterministic_adjudicator,
    deterministic_chunker,
    deterministic_consistency_checker,
    deterministic_extractor,
    observer,
)
from src.pipeline.runner import PipelineRunner
from src.pipeline.validators import (
    validate_coverage_plans,
    validate_observations,
    validate_hypotheses,
    validate_final_decisions,
    terminal_validation,
)

# ── Constants ─────────────────────────────────────────────────────────────────

BUNDLE_PATH = "configs/bundles/asap_set8_baseline.bundle.yaml"
SAMPLE_TEXT = (
    "The old man had always wanted to make the grouch next door smile. "
    "He tried baking cookies and leaving them on the porch. "
    "One sunny morning the grouch finally smiled and waved. "
    "It was the best day of the old man's life."
)

# ── Module-scoped fixtures (load bundle once for all tests) ───────────────────


@pytest.fixture(scope="module")
def bundle():
    return config_resolver.run(BUNDLE_PATH)


@pytest.fixture(scope="module")
def runner(bundle):
    return PipelineRunner(bundle)


@pytest.fixture(scope="module")
def pipeline_result(runner):
    request = EvaluationRequest(
        raw_text=SAMPLE_TEXT,
        bundle_ref="asap_set8_baseline@v1",
        request_id="test-req-integration-001",
    )
    return runner.run(request)


# ── TestPipelineNormalPath ─────────────────────────────────────────────────────


class TestPipelineNormalPath:
    def test_returns_tuple_of_run_trace_and_feedback(self, pipeline_result):
        assert isinstance(pipeline_result, tuple)
        assert len(pipeline_result) == 2
        run_trace, feedback = pipeline_result
        assert isinstance(run_trace, RunTrace)
        assert isinstance(feedback, dict)

    def test_run_trace_status_completed(self, pipeline_result):
        run_trace, _ = pipeline_result
        assert run_trace.status == RunStatus.COMPLETED

    def test_terminal_validation_passed(self, pipeline_result):
        run_trace, _ = pipeline_result
        assert run_trace.terminal_validation_passed is True

    def test_run_trace_has_run_id(self, pipeline_result):
        run_trace, _ = pipeline_result
        assert run_trace.run_id is not None
        assert len(run_trace.run_id) > 0

    def test_run_trace_has_bundle_id(self, pipeline_result, bundle):
        run_trace, _ = pipeline_result
        assert run_trace.bundle_id == bundle.artifact_bundle.bundle_id

    def test_run_trace_has_node_traces(self, pipeline_result):
        run_trace, _ = pipeline_result
        assert len(run_trace.node_traces) > 0

    def test_all_node_traces_have_success_status(self, pipeline_result):
        run_trace, _ = pipeline_result
        for nt in run_trace.node_traces:
            assert nt.status == NodeStatus.SUCCESS, (
                f"Node '{nt.node_id}' has status {nt.status}, expected SUCCESS"
            )

    def test_preprocess_node_in_traces(self, pipeline_result):
        run_trace, _ = pipeline_result
        node_ids = [nt.node_id for nt in run_trace.node_traces]
        assert "node_preprocess" in node_ids

    def test_coverage_node_in_traces(self, pipeline_result):
        run_trace, _ = pipeline_result
        node_ids = [nt.node_id for nt in run_trace.node_traces]
        assert "node_coverage" in node_ids

    def test_extractor_node_in_traces(self, pipeline_result):
        run_trace, _ = pipeline_result
        node_ids = [nt.node_id for nt in run_trace.node_traces]
        assert "node_extractor" in node_ids

    def test_observer_node_in_traces(self, pipeline_result):
        run_trace, _ = pipeline_result
        node_ids = [nt.node_id for nt in run_trace.node_traces]
        assert "node_observer" in node_ids

    def test_scorer_node_in_traces(self, pipeline_result):
        run_trace, _ = pipeline_result
        node_ids = [nt.node_id for nt in run_trace.node_traces]
        assert "node_scorer" in node_ids

    def test_consistency_checker_node_in_traces(self, pipeline_result):
        run_trace, _ = pipeline_result
        node_ids = [nt.node_id for nt in run_trace.node_traces]
        assert "node_consistency_checker" in node_ids

    def test_feedback_node_in_traces(self, pipeline_result):
        run_trace, _ = pipeline_result
        node_ids = [nt.node_id for nt in run_trace.node_traces]
        assert "node_feedback" in node_ids

    def test_feedback_has_dimensions_key(self, pipeline_result):
        _, feedback = pipeline_result
        assert "dimensions" in feedback

    def test_feedback_covers_all_rubric_dimensions(self, pipeline_result, bundle):
        _, feedback = pipeline_result
        rubric = bundle.rubric_snapshot
        for dim in rubric.dimensions:
            dim_id = dim["dimension_id"]
            assert dim_id in feedback["dimensions"], (
                f"Dimension '{dim_id}' missing from feedback output"
            )

    def test_feedback_dimension_has_score(self, pipeline_result, bundle):
        _, feedback = pipeline_result
        rubric = bundle.rubric_snapshot
        for dim in rubric.dimensions:
            entry = feedback["dimensions"][dim["dimension_id"]]
            assert "final_score" in entry
            assert isinstance(entry["final_score"], int)

    def test_feedback_dimension_has_stage_m_fields(self, pipeline_result, bundle):
        _, feedback = pipeline_result
        rubric = bundle.rubric_snapshot
        for dim in rubric.dimensions:
            entry = feedback["dimensions"][dim["dimension_id"]]
            assert "scorer_rationale" in entry
            assert isinstance(entry["scorer_rationale"], str)
            assert "was_adjudicated" in entry
            assert isinstance(entry["was_adjudicated"], bool)

    def test_feedback_scores_within_scale_range(self, pipeline_result, bundle):
        _, feedback = pipeline_result
        rubric = bundle.rubric_snapshot
        for dim in rubric.dimensions:
            score = feedback["dimensions"][dim["dimension_id"]]["final_score"]
            assert rubric.validate_score(dim["dimension_id"], score), (
                f"Score {score} out of range for dimension '{dim['dimension_id']}'"
            )

    def test_replay_metadata_has_provider(self, pipeline_result):
        run_trace, _ = pipeline_result
        assert run_trace.replay_metadata.get("provider") == "mock"

    def test_run_trace_is_serializable(self, pipeline_result):
        """RunTrace must round-trip through to_dict/from_dict."""
        run_trace, _ = pipeline_result
        restored = RunTrace.from_dict(run_trace.to_dict())
        assert restored.run_id == run_trace.run_id
        assert restored.status == run_trace.status
        assert len(restored.node_traces) == len(run_trace.node_traces)

    def test_second_run_has_different_run_id(self, runner):
        """Each invocation of run() must produce a unique run_id."""
        request = EvaluationRequest(
            raw_text=SAMPLE_TEXT,
            bundle_ref="asap_set8_baseline@v1",
            request_id="test-req-integration-002",
        )
        rt1, _ = runner.run(request)
        rt2, _ = runner.run(request)
        assert rt1.run_id != rt2.run_id

    def test_same_input_produces_same_node_id_sequence(self, runner):
        """Structural determinism: same input → same ordered node_ids."""
        request = EvaluationRequest(
            raw_text=SAMPLE_TEXT,
            bundle_ref="asap_set8_baseline@v1",
            request_id="test-req-det",
        )
        rt1, _ = runner.run(request)
        rt2, _ = runner.run(request)
        assert [nt.node_id for nt in rt1.node_traces] == [
            nt.node_id for nt in rt2.node_traces
        ]

    def test_mock_preprocess_output_ref_contains_chunk_summary(self, pipeline_result):
        run_trace, _ = pipeline_result
        preprocess = next(nt for nt in run_trace.node_traces if nt.node_id == "node_preprocess")
        assert preprocess.output_ref is not None
        assert "|chunks:" in preprocess.output_ref
        assert "|method:rule" in preprocess.output_ref

    def test_mock_coverage_output_ref_contains_narrowing_summary(self, pipeline_result):
        run_trace, _ = pipeline_result
        coverage_node = next(nt for nt in run_trace.node_traces if nt.node_id == "node_coverage")
        assert coverage_node.output_ref is not None
        assert "coverage:" in coverage_node.output_ref
        assert "->" in coverage_node.output_ref

    def test_mock_extractor_output_ref_contains_match_stats(self, pipeline_result):
        run_trace, _ = pipeline_result
        extractor_node = next(nt for nt in run_trace.node_traces if nt.node_id == "node_extractor")
        assert extractor_node.output_ref is not None
        assert extractor_node.output_ref.startswith("spans:")
        assert "(exact:" in extractor_node.output_ref
        assert "fuzzy:" in extractor_node.output_ref
        assert "unmatched:" in extractor_node.output_ref


class TestMockDefaults:
    def test_normalized_document_new_fields_have_defaults(self):
        request = EvaluationRequest(
            raw_text=SAMPLE_TEXT,
            bundle_ref="asap_set8_baseline@v1",
            request_id="test-doc-defaults",
        )
        _, document = deterministic_chunker.run(request)
        assert document.document_type == "unknown"
        assert document.token_estimate == 0
        assert all(unit.chunk_title is None for unit in document.text_units)
        assert all(unit.chunk_method == "rule" for unit in document.text_units)

    def test_mock_coverage_is_full_scan_with_empty_relevance_scores(self, bundle):
        request = EvaluationRequest(
            raw_text=SAMPLE_TEXT,
            bundle_ref="asap_set8_baseline@v1",
            request_id="test-coverage-defaults",
        )
        _, document = deterministic_chunker.run(request)
        plans = coverage.run(document, bundle.rubric_snapshot)
        all_unit_ids = [u.unit_id for u in document.text_units]

        for plan in plans:
            assert plan.coverage_strategy == "full_scan"
            assert plan.target_unit_ids == all_unit_ids
            assert plan.relevance_scores == {}

    def test_dimension_observation_new_field_has_default(self, bundle):
        request = EvaluationRequest(
            raw_text=SAMPLE_TEXT,
            bundle_ref="asap_set8_baseline@v1",
            request_id="test-observation-defaults",
        )
        _, document = deterministic_chunker.run(request)
        plans = coverage.run(document, bundle.rubric_snapshot)
        first_plan = plans[0]
        spans = deterministic_extractor.run(first_plan, document)
        obs = observer.run(spans, first_plan)
        assert obs.coverage_miss_span_ids == []

    def test_mock_evidence_span_fields_are_legal(self, runner):
        request = EvaluationRequest(
            raw_text=SAMPLE_TEXT,
            bundle_ref="asap_set8_baseline@v1",
            request_id="test-span-fields-legal",
        )
        runner.run(request)
        spans = runner.last_spans
        assert spans
        for span in spans:
            assert span.extraction_note is not None
            assert span.support_type in {"supporting", "counter", "neutral"}
            if span.scope.value == "span":
                assert span.unit_id is not None
            else:
                assert span.unit_id is None

    def test_mock_hypothesis_evidence_ids_are_valid(self, runner):
        request = EvaluationRequest(
            raw_text=SAMPLE_TEXT,
            bundle_ref="asap_set8_baseline@v1",
            request_id="test-hyp-evidence-ids",
        )
        runner.run(request)

        valid_span_ids = {span.span_id for span in runner.last_spans}
        assert valid_span_ids
        assert runner.last_hypotheses
        for hyp in runner.last_hypotheses:
            assert all(span_id in valid_span_ids for span_id in hyp.evidence_span_ids)

    def test_mock_reconciliation_matches_deterministic_checker(self, runner):
        request = EvaluationRequest(
            raw_text=SAMPLE_TEXT,
            bundle_ref="asap_set8_baseline@v1",
            request_id="test-reconciliation-checker",
        )
        runner.run(request)
        expected = deterministic_consistency_checker.run(
            runner.last_hypotheses,
            runner._bundle.policy_snapshot,  # noqa: SLF001 - integration assertion
        )
        assert [c.to_dict() for c in runner.last_conflicts] == [
            c.to_dict() for c in expected
        ]

    def test_mock_reconciliation_decisions_match_deterministic_adjudicator(self, runner):
        request = EvaluationRequest(
            raw_text=SAMPLE_TEXT,
            bundle_ref="asap_set8_baseline@v1",
            request_id="test-reconciliation-adjudicator",
        )
        runner.run(request)
        expected_adj, expected_decisions = deterministic_adjudicator.run(
            runner.last_conflicts,
            runner.last_hypotheses,
            runner._bundle.policy_snapshot,  # noqa: SLF001 - integration assertion
        )
        assert [r.to_dict() for r in runner.last_adjudication_records] == [
            r.to_dict() for r in expected_adj
        ]
        assert [d.to_dict() for d in runner.last_decisions] == [
            d.to_dict() for d in expected_decisions
        ]


# ── TestValidators ─────────────────────────────────────────────────────────────
#
# These tests use a minimal RubricSnapshot fixture (2 dims) so they run without
# touching the real bundle — fast, isolated, zero-hardcoding.

SCALE_ID = "test_scale"
SCALE_DEF = {"scale_id": SCALE_ID, "min": 1, "max": 4}
DIM1 = {
    "dimension_id": "dim_001", "code": "X", "name": "Dim One",
    "scale_ref": SCALE_ID,
    "observation_schema": {"required_facets": ["f1", "f2"]},
}
DIM2 = {
    "dimension_id": "dim_002", "code": "Y", "name": "Dim Two",
    "scale_ref": SCALE_ID,
    "observation_schema": {"required_facets": ["f3"]},
}


@pytest.fixture
def mini_rubric():
    dims = [DIM1, DIM2]
    return RubricSnapshot(
        rubric_id="mini", rubric_version="v1", rubric_name="Mini Rubric",
        dimensions=dims, scales=[SCALE_DEF],
        dimension_by_id={d["dimension_id"]: d for d in dims},
        dimension_by_code={d["code"]: d for d in dims},
        scale_by_id={SCALE_ID: SCALE_DEF},
    )


@pytest.fixture
def mini_plans(mini_rubric):
    return [
        CoveragePlan(
            plan_id=f"plan-{d['dimension_id']}",
            document_id="doc-001",
            dimension_id=d["dimension_id"],
            target_unit_ids=["unit-0"],
            required_facets=d["observation_schema"]["required_facets"],
            minimum_evidence_units=1,
            allowed_evidence_scopes=["global"],
            coverage_strategy="full_scan",
        )
        for d in [DIM1, DIM2]
    ]


def _make_obs(dim_id: str) -> DimensionObservation:
    return DimensionObservation(
        observation_id=f"obs-{dim_id}",
        document_id="doc-001",
        dimension_id=dim_id,
        supporting_span_ids=[],
        counter_span_ids=[],
        facet_findings=[],
        observation_confidence=ObservationConfidence.HIGH,
        uncertainty_notes=[],
    )


def _make_hyp(dim_id: str, rater_id: str, score_val: int) -> ScoreHypothesis:
    score = create_score_representation(score_val, SCALE_ID)
    return ScoreHypothesis(
        hypothesis_id=f"hyp-{dim_id}-{rater_id}",
        observation_id=f"obs-{dim_id}",
        dimension_id=dim_id,
        rater_id=rater_id,
        score=score,
        descriptor_refs=[],
        evidence_span_ids=[],
        rationale="fixture",
        confidence=0.8,
    )


def _make_dec(dim_id: str, score_val: int) -> FinalDimensionDecision:
    score = create_score_representation(score_val, SCALE_ID)
    return FinalDimensionDecision(
        decision_id=f"dec-{dim_id}",
        dimension_id=dim_id,
        final_score=score,
        primary_hypothesis_id=f"hyp-{dim_id}-rater_1",
        adjudication_id=None,
        evidence_span_ids=[],
        descriptor_refs=[],
        decision_confidence=0.85,
        decision_note=None,
    )


class TestValidateCoveragePlans:
    def test_passes_when_all_dimensions_covered(self, mini_plans, mini_rubric):
        validate_coverage_plans(mini_plans, mini_rubric)  # should not raise

    def test_raises_when_dimension_missing(self, mini_rubric):
        only_one = [
            CoveragePlan(
                plan_id="plan-dim_001", document_id="doc-001",
                dimension_id="dim_001", target_unit_ids=[],
                required_facets=[], minimum_evidence_units=1,
                allowed_evidence_scopes=["global"], coverage_strategy="full_scan",
            )
        ]
        with pytest.raises(ValueError, match="dim_002"):
            validate_coverage_plans(only_one, mini_rubric)

    def test_raises_when_plans_empty(self, mini_rubric):
        with pytest.raises(ValueError):
            validate_coverage_plans([], mini_rubric)


class TestValidateObservations:
    def test_passes_when_all_plans_observed(self, mini_plans):
        observations = [_make_obs("dim_001"), _make_obs("dim_002")]
        validate_observations(observations, mini_plans)  # should not raise

    def test_raises_when_observation_missing(self, mini_plans):
        observations = [_make_obs("dim_001")]  # missing dim_002
        with pytest.raises(ValueError, match="dim_002"):
            validate_observations(observations, mini_plans)

    def test_passes_with_empty_plans(self):
        validate_observations([], [])  # vacuously true, should not raise


class TestValidateHypotheses:
    def test_passes_when_all_raters_present(self, mini_plans):
        hyps = [
            _make_hyp("dim_001", "rater_1", 3),
            _make_hyp("dim_001", "rater_2", 3),
            _make_hyp("dim_002", "rater_1", 2),
            _make_hyp("dim_002", "rater_2", 2),
        ]
        validate_hypotheses(hyps, mini_plans, ["rater_1", "rater_2"])

    def test_raises_when_rater_missing_for_dimension(self, mini_plans):
        hyps = [
            _make_hyp("dim_001", "rater_1", 3),
            # missing dim_001 rater_2
            _make_hyp("dim_002", "rater_1", 2),
            _make_hyp("dim_002", "rater_2", 2),
        ]
        with pytest.raises(ValueError):
            validate_hypotheses(hyps, mini_plans, ["rater_1", "rater_2"])

    def test_raises_when_dimension_not_scored(self, mini_plans):
        hyps = [
            _make_hyp("dim_001", "rater_1", 3),
            _make_hyp("dim_001", "rater_2", 3),
            # missing dim_002 entirely
        ]
        with pytest.raises(ValueError):
            validate_hypotheses(hyps, mini_plans, ["rater_1", "rater_2"])


class TestValidateFinalDecisions:
    def test_passes_when_all_dimensions_decided(self, mini_plans):
        decisions = [_make_dec("dim_001", 3), _make_dec("dim_002", 2)]
        validate_final_decisions(decisions, mini_plans)

    def test_raises_when_decision_missing(self, mini_plans):
        decisions = [_make_dec("dim_001", 3)]  # missing dim_002
        with pytest.raises(ValueError, match="dim_002"):
            validate_final_decisions(decisions, mini_plans)

    def test_passes_with_empty_plans(self):
        validate_final_decisions([], [])


class TestTerminalValidation:
    def test_returns_true_when_all_valid(self, mini_plans, mini_rubric):
        decisions = [_make_dec("dim_001", 2), _make_dec("dim_002", 3)]
        assert terminal_validation(decisions, mini_plans, mini_rubric) is True

    def test_returns_false_when_score_out_of_range(self, mini_plans, mini_rubric):
        # Score 5 is out of range for test_scale max=4
        decisions = [_make_dec("dim_001", 5), _make_dec("dim_002", 2)]
        assert terminal_validation(decisions, mini_plans, mini_rubric) is False

    def test_returns_false_when_dimension_missing(self, mini_plans, mini_rubric):
        decisions = [_make_dec("dim_001", 2)]  # missing dim_002
        assert terminal_validation(decisions, mini_plans, mini_rubric) is False

    def test_returns_true_for_boundary_scores(self, mini_plans, mini_rubric):
        # min=1 and max=4 are both valid
        decisions = [_make_dec("dim_001", 1), _make_dec("dim_002", 4)]
        assert terminal_validation(decisions, mini_plans, mini_rubric) is True
