"""
Tests for scoring and adjudication contracts (Phase 2, Task 3).

Covers:
- ScoreHypothesis: multi-rater score proposal with evidence and descriptor refs
- ConflictRecord: disagreement between score hypotheses
- AdjudicationRecord: policy-driven conflict resolution result
- FinalDimensionDecision: single authoritative decision per dimension
- CompositeDecision: optional aggregated result (depends on aggregation policy)
- Roundtrip to_dict() / from_dict() for all models
- Strict deserialization: reject extra/undeclared fields
- Zero-hardcoding: dimension IDs, descriptor refs, rule IDs all opaque strings;
  score values come from ScoreRepresentation (which references config scale_ref)

No trait names, dimension codes, fixed scale ranges, adjudication thresholds,
or composite formula details are hardcoded in these tests.
"""

import pytest

from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import (
    ScoreHypothesis,
    ConflictRecord,
    ConflictType,
    ResolutionPath,
    AdjudicationRecord,
    FinalDimensionDecision,
    CompositeDecision,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def score_repr_4():
    return create_score_representation(4, "ordinal_any", display_annotation=None)


@pytest.fixture
def score_repr_6():
    return create_score_representation(6, "ordinal_any", display_annotation=None)


@pytest.fixture
def hypothesis_a(score_repr_4):
    return ScoreHypothesis(
        hypothesis_id="hyp-001",
        observation_id="obs-001",
        dimension_id="dim_alpha",
        rater_id="rater_a",
        score=score_repr_4,
        descriptor_refs=["desc_ref_3", "desc_ref_4"],
        evidence_span_ids=["span-001", "span-002"],
        rationale="Rater A rationale text.",
        confidence=0.85,
    )


@pytest.fixture
def hypothesis_b(score_repr_6):
    return ScoreHypothesis(
        hypothesis_id="hyp-002",
        observation_id="obs-001",
        dimension_id="dim_alpha",
        rater_id="rater_b",
        score=score_repr_6,
        descriptor_refs=["desc_ref_5", "desc_ref_6"],
        evidence_span_ids=["span-001"],
        rationale="Rater B rationale text.",
        confidence=0.90,
    )


@pytest.fixture
def conflict_record(hypothesis_a, hypothesis_b):
    return ConflictRecord(
        conflict_id="conflict-001",
        dimension_id="dim_alpha",
        hypothesis_ids=["hyp-001", "hyp-002"],
        conflict_type=ConflictType.NON_ADJACENT,
        trigger_rule_id="rule_non_adjacent",
        conflict_detail="Scores differ by more than allowed threshold.",
        recommended_path=ResolutionPath.THIRD_RATER,
    )


@pytest.fixture
def adjudication_record(conflict_record, score_repr_4):
    return AdjudicationRecord(
        adjudication_id="adj-001",
        conflict_id="conflict-001",
        dimension_id="dim_alpha",
        resolution_path=ResolutionPath.THIRD_RATER,
        policy_rule_id="rule_non_adjacent",
        resolved_score=score_repr_4,
        resolver_hypothesis_id="hyp-003",
        resolution_note="Third rater resolved conflict.",
        is_resolved=True,
    )


@pytest.fixture
def final_decision(score_repr_4):
    return FinalDimensionDecision(
        decision_id="dec-001",
        dimension_id="dim_alpha",
        final_score=score_repr_4,
        primary_hypothesis_id="hyp-001",
        adjudication_id="adj-001",
        evidence_span_ids=["span-001", "span-002"],
        descriptor_refs=["desc_ref_3", "desc_ref_4"],
        decision_confidence=0.87,
        decision_note=None,
    )


# ── ScoreHypothesis ────────────────────────────────────────────────────────────


class TestScoreHypothesis:
    def test_creation(self, hypothesis_a):
        assert hypothesis_a.hypothesis_id == "hyp-001"
        assert hypothesis_a.dimension_id == "dim_alpha"
        assert hypothesis_a.score.canonical_score == 4
        assert len(hypothesis_a.descriptor_refs) == 2
        assert 0.0 <= hypothesis_a.confidence <= 1.0

    def test_dimension_id_is_opaque(self, hypothesis_a):
        assert isinstance(hypothesis_a.dimension_id, str)

    def test_confidence_out_of_range_rejected(self, score_repr_4):
        with pytest.raises((ValueError, TypeError)):
            ScoreHypothesis(
                hypothesis_id="bad",
                observation_id="obs-001",
                dimension_id="dim_alpha",
                rater_id="rater_a",
                score=score_repr_4,
                descriptor_refs=[],
                evidence_span_ids=[],
                rationale="test",
                confidence=1.5,  # > 1.0 — invalid
            )

    def test_immutable(self, hypothesis_a):
        with pytest.raises((AttributeError, TypeError)):
            hypothesis_a.hypothesis_id = "mutated"  # type: ignore[misc]

    def test_roundtrip_serialization(self, hypothesis_a):
        restored = ScoreHypothesis.from_dict(hypothesis_a.to_dict())
        assert restored == hypothesis_a

    def test_from_dict_rejects_extra_fields(self, hypothesis_a):
        d = hypothesis_a.to_dict()
        d["surprise"] = "bad"
        with pytest.raises((TypeError, ValueError)):
            ScoreHypothesis.from_dict(d)


# ── ConflictRecord ─────────────────────────────────────────────────────────────


class TestConflictRecord:
    def test_creation(self, conflict_record):
        assert conflict_record.conflict_id == "conflict-001"
        assert conflict_record.conflict_type == ConflictType.NON_ADJACENT
        assert conflict_record.recommended_path == ResolutionPath.THIRD_RATER
        assert len(conflict_record.hypothesis_ids) == 2

    def test_conflict_type_values_exist(self):
        assert ConflictType.NON_ADJACENT is not None
        assert ConflictType.CUSP is not None

    def test_resolution_path_values_exist(self):
        assert ResolutionPath.THIRD_RATER is not None
        assert ResolutionPath.POLICY_AVERAGE is not None
        assert ResolutionPath.HUMAN_REVIEW is not None

    def test_immutable(self, conflict_record):
        with pytest.raises((AttributeError, TypeError)):
            conflict_record.conflict_id = "mutated"  # type: ignore[misc]

    def test_roundtrip_serialization(self, conflict_record):
        restored = ConflictRecord.from_dict(conflict_record.to_dict())
        assert restored == conflict_record

    def test_from_dict_rejects_extra_fields(self, conflict_record):
        d = conflict_record.to_dict()
        d["hidden"] = "bad"
        with pytest.raises((TypeError, ValueError)):
            ConflictRecord.from_dict(d)


# ── AdjudicationRecord ─────────────────────────────────────────────────────────


class TestAdjudicationRecord:
    def test_creation(self, adjudication_record):
        assert adjudication_record.adjudication_id == "adj-001"
        assert adjudication_record.is_resolved is True
        assert adjudication_record.resolved_score.canonical_score == 4

    def test_unresolved_has_no_score(self):
        rec = AdjudicationRecord(
            adjudication_id="adj-unresolved",
            conflict_id="conflict-001",
            dimension_id="dim_alpha",
            resolution_path=ResolutionPath.HUMAN_REVIEW,
            policy_rule_id="rule_non_adjacent",
            resolved_score=None,
            resolver_hypothesis_id=None,
            resolution_note="Escalated to human review.",
            is_resolved=False,
        )
        assert rec.is_resolved is False
        assert rec.resolved_score is None

    def test_immutable(self, adjudication_record):
        with pytest.raises((AttributeError, TypeError)):
            adjudication_record.adjudication_id = "mutated"  # type: ignore[misc]

    def test_roundtrip_serialization(self, adjudication_record):
        restored = AdjudicationRecord.from_dict(adjudication_record.to_dict())
        assert restored == adjudication_record

    def test_roundtrip_unresolved(self):
        rec = AdjudicationRecord(
            adjudication_id="adj-unresolved",
            conflict_id="conflict-001",
            dimension_id="dim_alpha",
            resolution_path=ResolutionPath.HUMAN_REVIEW,
            policy_rule_id="rule_policy",
            resolved_score=None,
            resolver_hypothesis_id=None,
            resolution_note=None,
            is_resolved=False,
        )
        restored = AdjudicationRecord.from_dict(rec.to_dict())
        assert restored == rec

    def test_from_dict_rejects_extra_fields(self, adjudication_record):
        d = adjudication_record.to_dict()
        d["injected"] = "bad"
        with pytest.raises((TypeError, ValueError)):
            AdjudicationRecord.from_dict(d)


# ── FinalDimensionDecision ─────────────────────────────────────────────────────


class TestFinalDimensionDecision:
    def test_creation(self, final_decision):
        assert final_decision.decision_id == "dec-001"
        assert final_decision.dimension_id == "dim_alpha"
        assert final_decision.final_score.canonical_score == 4
        assert final_decision.adjudication_id == "adj-001"

    def test_no_adjudication_path(self, score_repr_4):
        """A dimension with no conflict has no adjudication_id."""
        dec = FinalDimensionDecision(
            decision_id="dec-simple",
            dimension_id="dim_beta",
            final_score=score_repr_4,
            primary_hypothesis_id="hyp-001",
            adjudication_id=None,
            evidence_span_ids=["span-001"],
            descriptor_refs=["desc_ref_3"],
            decision_confidence=0.95,
            decision_note=None,
        )
        assert dec.adjudication_id is None

    def test_immutable(self, final_decision):
        with pytest.raises((AttributeError, TypeError)):
            final_decision.decision_id = "mutated"  # type: ignore[misc]

    def test_roundtrip_serialization(self, final_decision):
        restored = FinalDimensionDecision.from_dict(final_decision.to_dict())
        assert restored == final_decision

    def test_from_dict_rejects_extra_fields(self, final_decision):
        d = final_decision.to_dict()
        d["extra"] = "bad"
        with pytest.raises((TypeError, ValueError)):
            FinalDimensionDecision.from_dict(d)


# ── CompositeDecision ──────────────────────────────────────────────────────────


class TestCompositeDecision:
    def test_creation(self, score_repr_4):
        cd = CompositeDecision(
            composite_id="comp-001",
            aggregation_policy_ref="policy://composite_score/v1",
            contributing_decision_ids=["dec-001", "dec-002"],
            composite_score=score_repr_4,
            aggregation_detail={"formula": "weighted_sum", "weights": {}},
            composite_note=None,
        )
        assert cd.composite_id == "comp-001"
        assert cd.composite_score.canonical_score == 4
        # aggregation policy ref is opaque — not a hardcoded formula
        assert isinstance(cd.aggregation_policy_ref, str)

    def test_immutable(self, score_repr_4):
        cd = CompositeDecision(
            composite_id="comp-001",
            aggregation_policy_ref="policy://composite_score/v1",
            contributing_decision_ids=["dec-001"],
            composite_score=score_repr_4,
            aggregation_detail={},
            composite_note=None,
        )
        with pytest.raises((AttributeError, TypeError)):
            cd.composite_id = "mutated"  # type: ignore[misc]

    def test_roundtrip_serialization(self, score_repr_4):
        cd = CompositeDecision(
            composite_id="comp-001",
            aggregation_policy_ref="policy://composite_score/v1",
            contributing_decision_ids=["dec-001", "dec-003"],
            composite_score=score_repr_4,
            aggregation_detail={"formula": "weighted_sum"},
            composite_note="optional note",
        )
        restored = CompositeDecision.from_dict(cd.to_dict())
        assert restored == cd

    def test_from_dict_rejects_extra_fields(self, score_repr_4):
        cd = CompositeDecision(
            composite_id="comp-001",
            aggregation_policy_ref="policy://composite_score/v1",
            contributing_decision_ids=[],
            composite_score=score_repr_4,
            aggregation_detail={},
            composite_note=None,
        )
        d = cd.to_dict()
        d["leak"] = "bad"
        with pytest.raises((TypeError, ValueError)):
            CompositeDecision.from_dict(d)
