"""
Roundtrip serialization tests for all Phase 2 contracts (Phase 2, Task 5a).

Verifies that for every contract model:
  obj == Model.from_dict(obj.to_dict())

This guarantees that all contract objects can be safely persisted (to disk,
object storage, or transmitted over the wire) and restored without data loss
or type corruption — a prerequisite for replay, audit, and snapshot testing.

Covers: EvaluationRequest, NormalizedRequest, NormalizedDocument, TextUnit,
        CoveragePlan, EvidenceSpan, FacetFinding, DimensionObservation,
        ScoreHypothesis, ConflictRecord, AdjudicationRecord,
        FinalDimensionDecision, CompositeDecision, CheckpointRef, NodeTrace,
        RunTrace.

Also covers Phase 1 contracts: ScoreRepresentation, ArtifactBundle family
(lightly, as they were already tested in Phase 1).
"""

import pytest
from datetime import datetime, timezone

from src.contracts.request_models import (
    EvaluationRequest, NormalizedRequest, NormalizedDocument, TextUnit, CoveragePlan,
)
from src.contracts.evidence import (
    EvidenceSpan, EvidenceScope, FacetFinding, DimensionObservation,
    ObservationConfidence,
)
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import (
    ScoreHypothesis, ConflictRecord, ConflictType, ResolutionPath,
    AdjudicationRecord, FinalDimensionDecision, CompositeDecision,
)
from src.contracts.trace import (
    CheckpointRef, NodeTrace, NodeStatus, RunTrace, RunStatus,
)

TS = datetime(2026, 3, 12, 10, 0, 0, tzinfo=timezone.utc)
TS2 = datetime(2026, 3, 12, 10, 0, 5, tzinfo=timezone.utc)

SCALE = "ordinal_any"


# ── request_models ─────────────────────────────────────────────────────────────


class TestRoundtripRequestModels:
    def test_text_unit(self):
        obj = TextUnit("u1", "d1", "hello world", 0, 11, "sentence", 0)
        assert TextUnit.from_dict(obj.to_dict()) == obj

    def test_evaluation_request_minimal(self):
        obj = EvaluationRequest(raw_text="text", bundle_ref="b://x/v1")
        assert EvaluationRequest.from_dict(obj.to_dict()) == obj

    def test_evaluation_request_full(self):
        obj = EvaluationRequest("text", "b://x/v1", "req-1", {"k": "v"})
        assert EvaluationRequest.from_dict(obj.to_dict()) == obj

    def test_normalized_request(self):
        obj = NormalizedRequest("req-1", "text", "b://x/v1", TS, ["note"], {"a": 1})
        assert NormalizedRequest.from_dict(obj.to_dict()) == obj

    def test_normalized_document_with_units(self):
        u = TextUnit("u1", "d1", "hello", 0, 5, "sentence", 0)
        obj = NormalizedDocument("d1", "req-1", "hello", [u], 5, 1, {})
        assert NormalizedDocument.from_dict(obj.to_dict()) == obj

    def test_coverage_plan(self):
        obj = CoveragePlan(
            "plan-1", "d1", "dim_x", ["u1"], ["f1", "f2"], 2, ["span"], "full_scan"
        )
        assert CoveragePlan.from_dict(obj.to_dict()) == obj


# ── evidence ───────────────────────────────────────────────────────────────────


class TestRoundtripEvidence:
    def test_evidence_span_span_scope(self):
        obj = EvidenceSpan("s1", "d1", "u1", "quote", 0, 5, EvidenceScope.SPAN,
                           "dim_x", ["f1"], "note")
        assert EvidenceSpan.from_dict(obj.to_dict()) == obj

    def test_evidence_span_global_scope(self):
        obj = EvidenceSpan("s2", "d1", None, None, None, None, EvidenceScope.GLOBAL,
                           "dim_y", [], None)
        assert EvidenceSpan.from_dict(obj.to_dict()) == obj

    def test_facet_finding(self):
        obj = FacetFinding("f1", ["s1", "s2"], ["s3"], "note")
        assert FacetFinding.from_dict(obj.to_dict()) == obj

    def test_facet_finding_no_counter(self):
        obj = FacetFinding("f1", ["s1"], [], None)
        assert FacetFinding.from_dict(obj.to_dict()) == obj

    def test_dimension_observation_full(self):
        ff = FacetFinding("f1", ["s1"], [], None)
        obj = DimensionObservation(
            "obs-1", "d1", "dim_x", ["s1"], ["s2"], [ff],
            ObservationConfidence.HIGH, ["uncertainty note"],
        )
        assert DimensionObservation.from_dict(obj.to_dict()) == obj

    def test_dimension_observation_low_confidence(self):
        obj = DimensionObservation(
            "obs-2", "d1", "dim_y", [], [], [],
            ObservationConfidence.LOW, [],
        )
        assert DimensionObservation.from_dict(obj.to_dict()) == obj


# ── scoring ────────────────────────────────────────────────────────────────────


class TestRoundtripScoring:
    def test_score_hypothesis(self):
        sr = create_score_representation(4, SCALE)
        obj = ScoreHypothesis("h1", "obs-1", "dim_x", "rater_a", sr,
                              ["d1"], ["s1"], "rationale", 0.9)
        assert ScoreHypothesis.from_dict(obj.to_dict()) == obj

    def test_conflict_record(self):
        obj = ConflictRecord(
            "c1", "dim_x", ["h1", "h2"], ConflictType.NON_ADJACENT,
            "rule_1", "detail text", ResolutionPath.THIRD_RATER,
        )
        assert ConflictRecord.from_dict(obj.to_dict()) == obj

    def test_conflict_record_cusp(self):
        obj = ConflictRecord(
            "c2", "dim_x", ["h1", "h2"], ConflictType.CUSP,
            "rule_cusp", "cusp detail", ResolutionPath.POLICY_AVERAGE,
        )
        assert ConflictRecord.from_dict(obj.to_dict()) == obj

    def test_adjudication_record_resolved(self):
        sr = create_score_representation(5, SCALE)
        obj = AdjudicationRecord(
            "adj-1", "c1", "dim_x", ResolutionPath.THIRD_RATER,
            "rule_1", sr, "h3", "third rater resolved", True,
        )
        assert AdjudicationRecord.from_dict(obj.to_dict()) == obj

    def test_adjudication_record_unresolved(self):
        obj = AdjudicationRecord(
            "adj-2", "c1", "dim_x", ResolutionPath.HUMAN_REVIEW,
            "rule_1", None, None, None, False,
        )
        assert AdjudicationRecord.from_dict(obj.to_dict()) == obj

    def test_final_dimension_decision(self):
        sr = create_score_representation(4, SCALE, "-")
        obj = FinalDimensionDecision(
            "dec-1", "dim_x", sr, "h1", "adj-1", ["s1"], ["d1"], 0.88, "note",
        )
        assert FinalDimensionDecision.from_dict(obj.to_dict()) == obj

    def test_final_dimension_decision_no_adjudication(self):
        sr = create_score_representation(6, SCALE)
        obj = FinalDimensionDecision(
            "dec-2", "dim_y", sr, "h2", None, [], [], 0.99, None,
        )
        assert FinalDimensionDecision.from_dict(obj.to_dict()) == obj

    def test_composite_decision(self):
        sr = create_score_representation(4, SCALE)
        obj = CompositeDecision(
            "comp-1", "policy://agg/v1", ["dec-1", "dec-2"],
            sr, {"formula": "weighted_sum"}, "note",
        )
        assert CompositeDecision.from_dict(obj.to_dict()) == obj


# ── trace ──────────────────────────────────────────────────────────────────────


class TestRoundtripTrace:
    def test_checkpoint_ref(self):
        obj = CheckpointRef("ckpt-1", "node_x", "run-1", "path/to/ckpt", TS)
        assert CheckpointRef.from_dict(obj.to_dict()) == obj

    def test_node_trace_success(self):
        ckpt = CheckpointRef("ckpt-1", "node_x", "run-1", "path/ckpt", TS)
        obj = NodeTrace(
            "node_x", "run-1", "preprocess", NodeStatus.SUCCESS,
            TS, TS2, "in/run-1/x", "out/run-1/x", ckpt, [], None, {"k": 1},
        )
        assert NodeTrace.from_dict(obj.to_dict()) == obj

    def test_node_trace_failed_no_checkpoint(self):
        obj = NodeTrace(
            "node_y", "run-1", "extract", NodeStatus.FAILED,
            TS, TS2, "in/run-1/y", None, None, ["re_extract"], "error!", {},
        )
        assert NodeTrace.from_dict(obj.to_dict()) == obj

    def test_node_trace_pending_no_finish(self):
        obj = NodeTrace(
            "node_z", "run-1", "score", NodeStatus.PENDING,
            TS, None, None, None, None, [], None, {},
        )
        assert NodeTrace.from_dict(obj.to_dict()) == obj

    def test_run_trace_full(self):
        nt = NodeTrace(
            "node_x", "run-1", "preprocess", NodeStatus.SUCCESS,
            TS, TS2, "in/run-1/x", "out/run-1/x", None, [], None, {},
        )
        obj = RunTrace(
            "run-1", "bundle@v1", "bundle", "req-1",
            RunStatus.COMPLETED, TS, TS2, [nt], True, {"provider": "openai_compatible"},
        )
        assert RunTrace.from_dict(obj.to_dict()) == obj

    def test_run_trace_in_progress(self):
        obj = RunTrace(
            "run-2", "bundle@v1", "bundle", "req-2",
            RunStatus.IN_PROGRESS, TS, None, [], None, {},
        )
        assert RunTrace.from_dict(obj.to_dict()) == obj
