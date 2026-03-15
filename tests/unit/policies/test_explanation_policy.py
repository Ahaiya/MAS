"""
Tests for Explanation Policy Enforcement (Phase 4, Task 4).

Verifies that:
1. explanation.py renders DimensionExplanation from config-driven policy.
2. Citation chain validation (descriptor_ref → evidence_id → score) is enforced.
3. Policy requirement flags (require_descriptor_alignment, require_evidence_links,
   require_score_citation) control which violations are raised.
4. feedback.py (policy-aware) assembles a structured dict using explanation policy.
5. Uncertainty notes render when confidence is below threshold or adjudication used.
6. max_commentary_length_per_dimension is respected.

Zero-hardcoding: no trait names, no fixed score values, no rater IDs.
All fixtures use synthetic identifiers.
"""

import pytest

from src.contracts.artifact_bundle import PolicySnapshot, RubricSnapshot
from src.contracts.evidence import DimensionObservation, EvidenceScope, EvidenceSpan, ObservationConfidence
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import FinalDimensionDecision
from src.policies.explanation import (
    DimensionExplanation,
    ExplanationViolation,
    enforce_explanation_policy,
    render_dimension_explanation,
    validate_citation_chain,
)
from src.agents import feedback


# ── Synthetic fixtures ────────────────────────────────────────────────────────

SCALE_T = {"scale_id": "scale_t", "min": 1, "max": 5}

DIM_P = {
    "dimension_id": "dim_p",
    "code": "P",
    "name": "Phi Dimension",
    "scale_ref": "scale_t",
    "observation_schema": {"required_facets": ["facet_p1"]},
    "evidence_requirements": {"minimum_evidence_units": 1},
    "levels": [
        {"rank": 1, "summary": "Low phi", "descriptors": ["phi desc 1"]},
        {"rank": 3, "summary": "Mid phi", "descriptors": ["phi desc 3"]},
        {"rank": 5, "summary": "High phi", "descriptors": ["phi desc 5a", "phi desc 5b"]},
    ],
}

DIM_Q = {
    "dimension_id": "dim_q",
    "code": "Q",
    "name": "Psi Dimension",
    "scale_ref": "scale_t",
    "observation_schema": {"required_facets": ["facet_q1", "facet_q2"]},
    "evidence_requirements": {"minimum_evidence_units": 2},
    "levels": [
        {"rank": 2, "summary": "Low psi", "descriptors": ["psi desc 2"]},
        {"rank": 4, "summary": "High psi", "descriptors": ["psi desc 4"]},
    ],
}


@pytest.fixture
def rubric():
    dims = [DIM_P, DIM_Q]
    return RubricSnapshot(
        rubric_id="test_rubric",
        rubric_version="v1",
        rubric_name="Test Rubric",
        dimensions=dims,
        scales=[SCALE_T],
        dimension_by_id={d["dimension_id"]: d for d in dims},
        dimension_by_code={d["code"]: d for d in dims},
        scale_by_id={"scale_t": SCALE_T},
    )


_STRICT_POLICY = {
    "policy_id": "strict_v1",
    "requirements": {
        "require_descriptor_alignment": True,
        "require_evidence_links": True,
        "forbid_unreferenced_claims": True,
        "require_score_citation": True,
    },
    "citation_rules": {
        "min_citations_per_dimension": 1,
    },
    "output_constraints": {
        "max_commentary_length_per_dimension": 150,
        "require_evidence_score_chain": True,
    },
    "render_sections": [],
}

_PERMISSIVE_POLICY = {
    "policy_id": "permissive_v1",
    "requirements": {
        "require_descriptor_alignment": False,
        "require_evidence_links": False,
        "forbid_unreferenced_claims": False,
        "require_score_citation": False,
    },
    "citation_rules": {
        "min_citations_per_dimension": 0,
    },
    "output_constraints": {
        "max_commentary_length_per_dimension": 500,
        "require_evidence_score_chain": False,
    },
    "render_sections": [],
}


@pytest.fixture
def strict_policy():
    return PolicySnapshot(
        adjudication_policy={"triggers": []},
        aggregation_policy={},
        explanation_policy=_STRICT_POLICY,
        policy_version="test-strict",
    )


@pytest.fixture
def permissive_policy():
    return PolicySnapshot(
        adjudication_policy={"triggers": []},
        aggregation_policy={},
        explanation_policy=_PERMISSIVE_POLICY,
        policy_version="test-permissive",
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _score(val: int):
    return create_score_representation(val, "scale_t")


def _decision(dim_id: str, score_val: int, descriptor_refs=None, evidence_ids=None, adj_id=None, confidence=0.9):
    return FinalDimensionDecision(
        decision_id=f"dec-{dim_id}",
        dimension_id=dim_id,
        final_score=_score(score_val),
        primary_hypothesis_id=f"hyp-{dim_id}",
        adjudication_id=adj_id,
        evidence_span_ids=list(evidence_ids) if evidence_ids is not None else ["span-1"],
        descriptor_refs=list(descriptor_refs) if descriptor_refs is not None else ["phi desc 3"],
        decision_confidence=confidence,
        decision_note=None,
    )


def _span(dim_id: str, span_id: str = "span-1", quote: str = "some text") -> EvidenceSpan:
    return EvidenceSpan(
        span_id=span_id,
        document_id="doc-1",
        unit_id="u1",
        text_quote=quote,
        start_offset=0,
        end_offset=len(quote),
        scope=EvidenceScope.SPAN,
        dimension_id=dim_id,
        facet_ids=["facet_p1"],
        extraction_note=None,
    )


def _observation(dim_id: str, confidence=ObservationConfidence.HIGH) -> DimensionObservation:
    return DimensionObservation(
        observation_id=f"obs-{dim_id}",
        document_id="doc-1",
        dimension_id=dim_id,
        supporting_span_ids=["span-1"],
        counter_span_ids=[],
        facet_findings=[],
        observation_confidence=confidence,
        uncertainty_notes=[],
    )


# ── Tests: render_dimension_explanation ──────────────────────────────────────


class TestRenderDimensionExplanation:
    def test_returns_dimension_explanation(self, rubric, strict_policy):
        decision = _decision("dim_p", 3)
        spans = [_span("dim_p")]
        result = render_dimension_explanation(decision, spans, rubric, strict_policy)
        assert isinstance(result, DimensionExplanation)

    def test_dimension_id_matches(self, rubric, strict_policy):
        decision = _decision("dim_p", 3)
        result = render_dimension_explanation(decision, [_span("dim_p")], rubric, strict_policy)
        assert result.dimension_id == "dim_p"

    def test_dimension_name_from_rubric_config(self, rubric, strict_policy):
        # Name comes from rubric, not hardcoded
        decision = _decision("dim_p", 3)
        result = render_dimension_explanation(decision, [_span("dim_p")], rubric, strict_policy)
        assert result.dimension_name == "Phi Dimension"

    def test_canonical_score_matches_decision(self, rubric, strict_policy):
        decision = _decision("dim_p", 5)
        result = render_dimension_explanation(decision, [_span("dim_p")], rubric, strict_policy)
        assert result.canonical_score == 5

    def test_display_score_matches_decision(self, rubric, strict_policy):
        decision = _decision("dim_p", 3)
        result = render_dimension_explanation(decision, [_span("dim_p")], rubric, strict_policy)
        assert result.display_score == decision.final_score.display_score

    def test_descriptor_refs_from_decision(self, rubric, strict_policy):
        decision = _decision("dim_p", 3, descriptor_refs=["phi desc 3", "phi desc 1"])
        result = render_dimension_explanation(decision, [_span("dim_p")], rubric, strict_policy)
        assert "phi desc 3" in result.descriptor_refs

    def test_evidence_span_ids_from_decision(self, rubric, strict_policy):
        decision = _decision("dim_p", 3, evidence_ids=["span-1", "span-2"])
        result = render_dimension_explanation(decision, [_span("dim_p")], rubric, strict_policy)
        assert "span-1" in result.evidence_span_ids

    def test_commentary_within_max_length(self, rubric, strict_policy):
        decision = _decision("dim_p", 3)
        result = render_dimension_explanation(decision, [_span("dim_p")], rubric, strict_policy)
        max_len = strict_policy.explanation_policy["output_constraints"]["max_commentary_length_per_dimension"]
        assert len(result.commentary) <= max_len

    def test_no_uncertainty_note_when_high_confidence(self, rubric, strict_policy):
        decision = _decision("dim_p", 3, confidence=0.9)
        result = render_dimension_explanation(decision, [_span("dim_p")], rubric, strict_policy)
        assert result.uncertainty_note is None

    def test_uncertainty_note_when_low_confidence(self, rubric, strict_policy):
        # Decision with very low confidence should produce an uncertainty note
        decision = _decision("dim_p", 3, confidence=0.1)
        result = render_dimension_explanation(decision, [_span("dim_p")], rubric, strict_policy)
        assert result.uncertainty_note is not None

    def test_uncertainty_note_when_adjudication_used(self, rubric, strict_policy):
        decision = _decision("dim_p", 3, adj_id="adj-dim_p")
        result = render_dimension_explanation(decision, [_span("dim_p")], rubric, strict_policy)
        assert result.uncertainty_note is not None

    def test_result_is_frozen(self, rubric, strict_policy):
        decision = _decision("dim_p", 3)
        result = render_dimension_explanation(decision, [_span("dim_p")], rubric, strict_policy)
        with pytest.raises(AttributeError):
            result.dimension_id = "modified"  # type: ignore[misc]

    def test_different_rubric_produces_different_name(self, strict_policy):
        dims = [DIM_Q]
        rubric_q = RubricSnapshot(
            rubric_id="r2",
            rubric_version="v1",
            rubric_name="Q Rubric",
            dimensions=dims,
            scales=[SCALE_T],
            dimension_by_id={d["dimension_id"]: d for d in dims},
            dimension_by_code={d["code"]: d for d in dims},
            scale_by_id={"scale_t": SCALE_T},
        )
        decision = _decision("dim_q", 4, descriptor_refs=["psi desc 4"])
        result = render_dimension_explanation(decision, [_span("dim_q")], rubric_q, strict_policy)
        assert result.dimension_name == "Psi Dimension"
        assert result.canonical_score == 4


# ── Tests: validate_citation_chain ────────────────────────────────────────────


class TestValidateCitationChain:
    def _make_explanation(self, dim_id="dim_p", descriptor_refs=None, evidence_ids=None, score=3):
        return DimensionExplanation(
            dimension_id=dim_id,
            dimension_name="Phi Dimension",
            canonical_score=score,
            display_score=str(score),
            scale_ref="scale_t",
            descriptor_refs=list(descriptor_refs if descriptor_refs is not None else ["phi desc 3"]),
            evidence_span_ids=list(evidence_ids if evidence_ids is not None else ["span-1"]),
            commentary="Observed evidence supports this score.",
            uncertainty_note=None,
        )

    def test_no_violations_when_all_present(self, strict_policy):
        exp = self._make_explanation()
        violations = validate_citation_chain(exp, strict_policy)
        assert violations == []

    def test_violation_when_descriptor_refs_empty(self, strict_policy):
        exp = self._make_explanation(descriptor_refs=[])
        violations = validate_citation_chain(exp, strict_policy)
        viol_types = [v.violation_type for v in violations]
        assert any("descriptor" in vt for vt in viol_types)

    def test_violation_when_evidence_ids_empty(self, strict_policy):
        exp = self._make_explanation(evidence_ids=[])
        violations = validate_citation_chain(exp, strict_policy)
        viol_types = [v.violation_type for v in violations]
        assert any("evidence" in vt for vt in viol_types)

    def test_no_violation_for_empty_evidence_when_not_required(self, permissive_policy):
        exp = self._make_explanation(evidence_ids=[])
        violations = validate_citation_chain(exp, permissive_policy)
        # permissive policy: require_evidence_links=False → no violation
        assert violations == []

    def test_no_violation_for_empty_descriptor_when_not_required(self, permissive_policy):
        exp = self._make_explanation(descriptor_refs=[])
        violations = validate_citation_chain(exp, permissive_policy)
        assert violations == []

    def test_violation_contains_dimension_id(self, strict_policy):
        exp = self._make_explanation(dim_id="dim_p", descriptor_refs=[])
        violations = validate_citation_chain(exp, strict_policy)
        assert all(v.dimension_id == "dim_p" for v in violations)

    def test_multiple_violations_at_once(self, strict_policy):
        exp = self._make_explanation(descriptor_refs=[], evidence_ids=[])
        violations = validate_citation_chain(exp, strict_policy)
        # Should report both missing descriptor AND missing evidence
        assert len(violations) >= 2

    def test_min_citations_enforced(self, strict_policy):
        # min_citations_per_dimension = 1: having evidence_ids=[] should trigger
        exp = self._make_explanation(evidence_ids=[])
        violations = validate_citation_chain(exp, strict_policy)
        assert len(violations) >= 1


# ── Tests: enforce_explanation_policy ─────────────────────────────────────────


class TestEnforceExplanationPolicy:
    def _valid_exp(self, dim_id: str) -> DimensionExplanation:
        return DimensionExplanation(
            dimension_id=dim_id,
            dimension_name="Test Dim",
            canonical_score=3,
            display_score="3",
            scale_ref="scale_t",
            descriptor_refs=["desc-1"],
            evidence_span_ids=["span-1"],
            commentary="Valid commentary.",
            uncertainty_note=None,
        )

    def _invalid_exp(self, dim_id: str) -> DimensionExplanation:
        return DimensionExplanation(
            dimension_id=dim_id,
            dimension_name="Test Dim",
            canonical_score=3,
            display_score="3",
            scale_ref="scale_t",
            descriptor_refs=[],          # missing
            evidence_span_ids=[],        # missing
            commentary="Ungrounded claim.",
            uncertainty_note=None,
        )

    def test_no_violations_for_all_valid(self, strict_policy):
        exps = [self._valid_exp("dim_p"), self._valid_exp("dim_q")]
        violations = enforce_explanation_policy(exps, strict_policy)
        assert violations == []

    def test_violations_collected_across_multiple_dimensions(self, strict_policy):
        exps = [self._invalid_exp("dim_p"), self._invalid_exp("dim_q")]
        violations = enforce_explanation_policy(exps, strict_policy)
        dim_ids = {v.dimension_id for v in violations}
        assert "dim_p" in dim_ids
        assert "dim_q" in dim_ids

    def test_empty_explanations_list(self, strict_policy):
        violations = enforce_explanation_policy([], strict_policy)
        assert violations == []

    def test_mixed_valid_and_invalid(self, strict_policy):
        exps = [self._valid_exp("dim_p"), self._invalid_exp("dim_q")]
        violations = enforce_explanation_policy(exps, strict_policy)
        dim_ids = {v.dimension_id for v in violations}
        assert "dim_p" not in dim_ids
        assert "dim_q" in dim_ids


# ── Tests: feedback.run() ──────────────────────────────────────────────────────


class TestFeedbackRun:
    def _make_inputs(self):
        decisions = [
            _decision("dim_p", 3, descriptor_refs=["phi desc 3"], evidence_ids=["span-1"]),
            _decision("dim_q", 4, descriptor_refs=["psi desc 4"], evidence_ids=["span-2"]),
        ]
        observations = [_observation("dim_p"), _observation("dim_q")]
        spans = [
            _span("dim_p", "span-1", "some phi text"),
            _span("dim_q", "span-2", "some psi text"),
        ]
        return decisions, observations, spans

    def test_output_has_dimensions_key(self, rubric, strict_policy):
        decisions, observations, spans = self._make_inputs()
        out = feedback.run(decisions, observations, spans, rubric, strict_policy)
        assert "dimensions" in out

    def test_output_has_violations_key(self, rubric, strict_policy):
        decisions, observations, spans = self._make_inputs()
        out = feedback.run(decisions, observations, spans, rubric, strict_policy)
        assert "violations" in out

    def test_output_has_generated_at(self, rubric, strict_policy):
        decisions, observations, spans = self._make_inputs()
        out = feedback.run(decisions, observations, spans, rubric, strict_policy)
        assert "generated_at" in out

    def test_dimensions_count_matches_decisions(self, rubric, strict_policy):
        decisions, observations, spans = self._make_inputs()
        out = feedback.run(decisions, observations, spans, rubric, strict_policy)
        assert len(out["dimensions"]) == 2

    def test_each_dimension_has_canonical_score(self, rubric, strict_policy):
        decisions, observations, spans = self._make_inputs()
        out = feedback.run(decisions, observations, spans, rubric, strict_policy)
        for dim_id, entry in out["dimensions"].items():
            assert "canonical_score" in entry

    def test_each_dimension_has_descriptor_refs(self, rubric, strict_policy):
        decisions, observations, spans = self._make_inputs()
        out = feedback.run(decisions, observations, spans, rubric, strict_policy)
        for dim_id, entry in out["dimensions"].items():
            assert "descriptor_refs" in entry

    def test_each_dimension_has_evidence_span_ids(self, rubric, strict_policy):
        decisions, observations, spans = self._make_inputs()
        out = feedback.run(decisions, observations, spans, rubric, strict_policy)
        for dim_id, entry in out["dimensions"].items():
            assert "evidence_span_ids" in entry

    def test_dimension_name_from_config(self, rubric, strict_policy):
        decisions, observations, spans = self._make_inputs()
        out = feedback.run(decisions, observations, spans, rubric, strict_policy)
        assert out["dimensions"]["dim_p"]["dimension_name"] == "Phi Dimension"
        assert out["dimensions"]["dim_q"]["dimension_name"] == "Psi Dimension"

    def test_no_violations_for_valid_decisions(self, rubric, strict_policy):
        decisions, observations, spans = self._make_inputs()
        out = feedback.run(decisions, observations, spans, rubric, strict_policy)
        assert out["violations"] == []

    def test_violations_reported_for_missing_refs(self, rubric, strict_policy):
        # Decision with empty descriptor_refs and evidence_ids
        decisions = [_decision("dim_p", 3, descriptor_refs=[], evidence_ids=[])]
        observations = [_observation("dim_p")]
        spans = []
        out = feedback.run(decisions, observations, spans, rubric, strict_policy)
        assert len(out["violations"]) > 0

    def test_permissive_policy_no_violations(self, rubric, permissive_policy):
        decisions = [_decision("dim_p", 3, descriptor_refs=[], evidence_ids=[])]
        observations = [_observation("dim_p")]
        spans = []
        out = feedback.run(decisions, observations, spans, rubric, permissive_policy)
        assert out["violations"] == []
