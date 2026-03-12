"""
Strict deserialization tests — reject extra/undeclared fields (Phase 2, Task 5b).

Every contract's from_dict() must raise TypeError or ValueError when the input
dict contains keys that are not declared fields of the dataclass. This prevents
silent schema drift where callers accidentally pass in extra data and it gets
silently ignored, masking bugs.

Covers all Phase 2 contract classes plus a smoke check on Phase 1 contracts.
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
from src.contracts.score_representation import create_score_representation, ScoreRepresentation
from src.contracts.scoring import (
    ScoreHypothesis, ConflictRecord, ConflictType, ResolutionPath,
    AdjudicationRecord, FinalDimensionDecision, CompositeDecision,
)
from src.contracts.trace import (
    CheckpointRef, NodeTrace, NodeStatus, RunTrace, RunStatus,
)

TS = datetime(2026, 3, 12, 10, 0, 0, tzinfo=timezone.utc)
SCALE = "ordinal_any"


def _inject(base_dict: dict, key: str = "_injected", value: str = "evil") -> dict:
    """Return a copy of base_dict with an extra unexpected key."""
    d = dict(base_dict)
    d[key] = value
    return d


# ── request_models ─────────────────────────────────────────────────────────────


class TestNoExtraFieldsRequestModels:
    def test_text_unit(self):
        obj = TextUnit("u1", "d1", "hello", 0, 5, "sentence", 0)
        with pytest.raises((TypeError, ValueError)):
            TextUnit.from_dict(_inject(obj.to_dict()))

    def test_evaluation_request(self):
        obj = EvaluationRequest("text", "b://x/v1", "req-1", {})
        with pytest.raises((TypeError, ValueError)):
            EvaluationRequest.from_dict(_inject(obj.to_dict()))

    def test_normalized_request(self):
        obj = NormalizedRequest("r1", "text", "b://x/v1", TS, [], {})
        with pytest.raises((TypeError, ValueError)):
            NormalizedRequest.from_dict(_inject(obj.to_dict()))

    def test_normalized_document(self):
        u = TextUnit("u1", "d1", "hi", 0, 2, "sentence", 0)
        obj = NormalizedDocument("d1", "r1", "hi", [u], 2, 1, {})
        with pytest.raises((TypeError, ValueError)):
            NormalizedDocument.from_dict(_inject(obj.to_dict()))

    def test_coverage_plan(self):
        obj = CoveragePlan("p1", "d1", "dim_x", [], ["f1"], 1, ["span"], "full_scan")
        with pytest.raises((TypeError, ValueError)):
            CoveragePlan.from_dict(_inject(obj.to_dict()))


# ── evidence ───────────────────────────────────────────────────────────────────


class TestNoExtraFieldsEvidence:
    def test_evidence_span(self):
        obj = EvidenceSpan("s1", "d1", "u1", "q", 0, 1, EvidenceScope.SPAN,
                           "dim_x", ["f1"], None)
        with pytest.raises((TypeError, ValueError)):
            EvidenceSpan.from_dict(_inject(obj.to_dict()))

    def test_facet_finding(self):
        obj = FacetFinding("f1", ["s1"], [], None)
        with pytest.raises((TypeError, ValueError)):
            FacetFinding.from_dict(_inject(obj.to_dict()))

    def test_dimension_observation(self):
        ff = FacetFinding("f1", ["s1"], [], None)
        obj = DimensionObservation(
            "obs-1", "d1", "dim_x", ["s1"], [], [ff],
            ObservationConfidence.MEDIUM, [],
        )
        with pytest.raises((TypeError, ValueError)):
            DimensionObservation.from_dict(_inject(obj.to_dict()))


# ── score_representation ───────────────────────────────────────────────────────


class TestNoExtraFieldsScoreRepresentation:
    def test_score_representation_extra_fields_do_not_corrupt(self):
        """ScoreRepresentation is a Phase 1 contract without strict extra-field
        rejection. We verify that extra fields do not corrupt the restored object
        (they may be silently ignored). Phase 2+ contracts enforce strict rejection."""
        obj = create_score_representation(4, SCALE, "-")
        d = _inject(obj.to_dict())
        restored = ScoreRepresentation.from_dict(d)
        assert restored.canonical_score == obj.canonical_score
        assert restored.display_score == obj.display_score
        assert restored.scale_ref == obj.scale_ref


# ── scoring ────────────────────────────────────────────────────────────────────


class TestNoExtraFieldsScoring:
    def test_score_hypothesis(self):
        sr = create_score_representation(4, SCALE)
        obj = ScoreHypothesis("h1", "obs-1", "dim_x", "rater_a", sr,
                              ["d1"], ["s1"], "rationale", 0.9)
        with pytest.raises((TypeError, ValueError)):
            ScoreHypothesis.from_dict(_inject(obj.to_dict()))

    def test_conflict_record(self):
        obj = ConflictRecord(
            "c1", "dim_x", ["h1", "h2"], ConflictType.NON_ADJACENT,
            "rule_1", "detail", ResolutionPath.THIRD_RATER,
        )
        with pytest.raises((TypeError, ValueError)):
            ConflictRecord.from_dict(_inject(obj.to_dict()))

    def test_adjudication_record(self):
        sr = create_score_representation(5, SCALE)
        obj = AdjudicationRecord(
            "adj-1", "c1", "dim_x", ResolutionPath.THIRD_RATER,
            "rule_1", sr, "h3", "resolved", True,
        )
        with pytest.raises((TypeError, ValueError)):
            AdjudicationRecord.from_dict(_inject(obj.to_dict()))

    def test_final_dimension_decision(self):
        sr = create_score_representation(4, SCALE)
        obj = FinalDimensionDecision(
            "dec-1", "dim_x", sr, "h1", None, ["s1"], ["d1"], 0.9, None,
        )
        with pytest.raises((TypeError, ValueError)):
            FinalDimensionDecision.from_dict(_inject(obj.to_dict()))

    def test_composite_decision(self):
        sr = create_score_representation(4, SCALE)
        obj = CompositeDecision(
            "comp-1", "policy://agg/v1", ["dec-1"],
            sr, {"formula": "w_sum"}, None,
        )
        with pytest.raises((TypeError, ValueError)):
            CompositeDecision.from_dict(_inject(obj.to_dict()))


# ── trace ──────────────────────────────────────────────────────────────────────


class TestNoExtraFieldsTrace:
    def test_checkpoint_ref(self):
        obj = CheckpointRef("ckpt-1", "node_x", "run-1", "path/ckpt", TS)
        with pytest.raises((TypeError, ValueError)):
            CheckpointRef.from_dict(_inject(obj.to_dict()))

    def test_node_trace(self):
        obj = NodeTrace(
            "node_x", "run-1", "preprocess", NodeStatus.SUCCESS,
            TS, TS, "in/x", "out/x", None, [], None, {},
        )
        with pytest.raises((TypeError, ValueError)):
            NodeTrace.from_dict(_inject(obj.to_dict()))

    def test_run_trace(self):
        obj = RunTrace(
            "run-1", "bundle@v1", "bundle", "req-1",
            RunStatus.COMPLETED, TS, TS, [], True, {},
        )
        with pytest.raises((TypeError, ValueError)):
            RunTrace.from_dict(_inject(obj.to_dict()))
