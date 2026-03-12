"""
Tests for deterministic mock workers (Phase 3, Task 3).

Covers all 9 mock workers:
- mock_config_resolver: wraps real ConfigCompiler, returns ResolvedArtifactBundle
- mock_preprocess: EvaluationRequest -> (NormalizedRequest, NormalizedDocument)
- mock_coverage: NormalizedDocument + RubricSnapshot -> List[CoveragePlan]
- mock_extractor: CoveragePlan + NormalizedDocument -> List[EvidenceSpan]
- mock_observer: List[EvidenceSpan] + CoveragePlan -> DimensionObservation
- mock_scorer: DimensionObservation + RubricSnapshot + rater_id -> ScoreHypothesis
- mock_consistency_checker: List[ScoreHypothesis] + PolicySnapshot -> List[ConflictRecord]
- mock_adjudicator: (conflicts, hypotheses, policy) -> (AdjudicationRecords, FinalDimensionDecisions)
- mock_feedback: (decisions, observations, rubric) -> Dict[str, Any]

Zero-hardcoding: no trait names, dimension codes, or score values are hardcoded here.
All config-driven values flow from the minimal RubricSnapshot / PolicySnapshot fixtures.
"""

import pytest
from typing import List

from src.contracts.artifact_bundle import RubricSnapshot, PolicySnapshot, ResolvedArtifactBundle
from src.contracts.request_models import (
    EvaluationRequest, NormalizedRequest, NormalizedDocument, TextUnit, CoveragePlan,
)
from src.contracts.evidence import (
    EvidenceSpan, EvidenceScope, DimensionObservation, FacetFinding, ObservationConfidence,
)
from src.contracts.scoring import (
    ScoreHypothesis, ConflictRecord, AdjudicationRecord, FinalDimensionDecision,
    ConflictType, ResolutionPath,
)
from src.contracts.score_representation import create_score_representation

from src.agents import (
    mock_config_resolver,
    mock_preprocess,
    mock_coverage,
    mock_extractor,
    mock_observer,
    mock_scorer,
    mock_consistency_checker,
    mock_adjudicator,
    mock_feedback,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

SCALE_ID = "test_scale"
SCALE_DEF = {"scale_id": SCALE_ID, "min": 1, "max": 4}

DIM1 = {
    "dimension_id": "dim_001",
    "code": "X",
    "name": "Test Dimension One",
    "scale_ref": SCALE_ID,
    "observation_schema": {"required_facets": ["facet_x1", "facet_x2"]},
}
DIM2 = {
    "dimension_id": "dim_002",
    "code": "Y",
    "name": "Test Dimension Two",
    "scale_ref": SCALE_ID,
    "observation_schema": {"required_facets": ["facet_y1"]},
}


@pytest.fixture
def rubric():
    dims = [DIM1, DIM2]
    return RubricSnapshot(
        rubric_id="test_rubric",
        rubric_version="v1",
        rubric_name="Test Rubric",
        dimensions=dims,
        scales=[SCALE_DEF],
        dimension_by_id={d["dimension_id"]: d for d in dims},
        dimension_by_code={d["code"]: d for d in dims},
        scale_by_id={SCALE_ID: SCALE_DEF},
    )


@pytest.fixture
def policy():
    adj = {
        "policy_id": "test_adjudication",
        "raters": {
            "required_independent_scores": 2,
            "rater_labels": ["rater_1", "rater_2"],
            "resolution_rater_label": "rater_3",
        },
        "triggers": [
            {
                "trigger_id": "non_adjacent",
                "type": "score_distance",
                "threshold": {"operator": ">", "value": 1},
                "action": "invoke_resolution",
            }
        ],
        "resolution_strategy": {"default": "use_rater_3_as_authoritative"},
    }
    return PolicySnapshot(
        adjudication_policy=adj,
        aggregation_policy={},
        explanation_policy={},
        policy_version="v1",
    )


@pytest.fixture
def eval_request():
    return EvaluationRequest(
        raw_text="The cat sat on the mat. It was a sunny day. The end.",
        bundle_ref="test_bundle@v1",
        request_id="req-001",
    )


@pytest.fixture
def document(eval_request, rubric):
    _, doc = mock_preprocess.run(eval_request)
    return doc


@pytest.fixture
def coverage_plans(document, rubric):
    return mock_coverage.run(document, rubric)


@pytest.fixture
def plan_dim1(coverage_plans):
    return next(p for p in coverage_plans if p.dimension_id == "dim_001")


@pytest.fixture
def spans_dim1(plan_dim1, document):
    return mock_extractor.run(plan_dim1, document)


@pytest.fixture
def observation_dim1(spans_dim1, plan_dim1):
    return mock_observer.run(spans_dim1, plan_dim1)


def _make_hypothesis(dim_id: str, obs_id: str, rater_id: str, score_val: int) -> ScoreHypothesis:
    score = create_score_representation(score_val, SCALE_ID)
    return ScoreHypothesis(
        hypothesis_id=f"hyp-{dim_id}-{rater_id}",
        observation_id=obs_id,
        dimension_id=dim_id,
        rater_id=rater_id,
        score=score,
        descriptor_refs=[f"level_{score_val}"],
        evidence_span_ids=[],
        rationale="fixture hypothesis",
        confidence=0.8,
    )


# ── TestMockConfigResolver ────────────────────────────────────────────────────


class TestMockConfigResolver:
    def test_returns_resolved_artifact_bundle(self):
        bundle_path = "configs/bundles/asap_set8_baseline.bundle.yaml"
        result = mock_config_resolver.run(bundle_path)
        assert isinstance(result, ResolvedArtifactBundle)

    def test_bundle_has_rubric_snapshot(self):
        bundle_path = "configs/bundles/asap_set8_baseline.bundle.yaml"
        result = mock_config_resolver.run(bundle_path)
        assert isinstance(result.rubric_snapshot, RubricSnapshot)
        assert len(result.rubric_snapshot.dimensions) > 0

    def test_bundle_has_policy_snapshot(self):
        bundle_path = "configs/bundles/asap_set8_baseline.bundle.yaml"
        result = mock_config_resolver.run(bundle_path)
        assert isinstance(result.policy_snapshot, PolicySnapshot)


# ── TestMockPreprocess ────────────────────────────────────────────────────────


class TestMockPreprocess:
    def test_returns_tuple_of_two_contracts(self, eval_request):
        result = mock_preprocess.run(eval_request)
        assert isinstance(result, tuple)
        assert len(result) == 2
        norm_req, doc = result
        assert isinstance(norm_req, NormalizedRequest)
        assert isinstance(doc, NormalizedDocument)

    def test_normalized_request_preserves_request_id(self, eval_request):
        norm_req, _ = mock_preprocess.run(eval_request)
        assert norm_req.request_id == eval_request.request_id

    def test_normalized_request_preserves_bundle_ref(self, eval_request):
        norm_req, _ = mock_preprocess.run(eval_request)
        assert norm_req.bundle_ref == eval_request.bundle_ref

    def test_document_has_non_empty_text_units(self, eval_request):
        _, doc = mock_preprocess.run(eval_request)
        assert len(doc.text_units) > 0

    def test_text_units_are_textunit_instances(self, eval_request):
        _, doc = mock_preprocess.run(eval_request)
        for unit in doc.text_units:
            assert isinstance(unit, TextUnit)

    def test_text_unit_offsets_are_valid(self, eval_request):
        _, doc = mock_preprocess.run(eval_request)
        for unit in doc.text_units:
            assert unit.end_offset > unit.start_offset
            assert unit.start_offset >= 0
            assert unit.end_offset <= len(doc.normalized_text)

    def test_text_units_text_matches_document_slice(self, eval_request):
        _, doc = mock_preprocess.run(eval_request)
        for unit in doc.text_units:
            expected = doc.normalized_text[unit.start_offset:unit.end_offset]
            assert unit.text == expected

    def test_document_char_count_matches_text(self, eval_request):
        _, doc = mock_preprocess.run(eval_request)
        assert doc.char_count == len(doc.normalized_text)

    def test_deterministic_same_input(self, eval_request):
        r1, d1 = mock_preprocess.run(eval_request)
        r2, d2 = mock_preprocess.run(eval_request)
        assert r1.request_id == r2.request_id
        assert d1.document_id == d2.document_id
        assert len(d1.text_units) == len(d2.text_units)

    def test_different_text_produces_different_document_id(self):
        req_a = EvaluationRequest(raw_text="Alpha text here.", bundle_ref="b@v1")
        req_b = EvaluationRequest(raw_text="Beta text here.", bundle_ref="b@v1")
        _, doc_a = mock_preprocess.run(req_a)
        _, doc_b = mock_preprocess.run(req_b)
        assert doc_a.document_id != doc_b.document_id

    def test_auto_generates_request_id_when_missing(self):
        req = EvaluationRequest(raw_text="Some text.", bundle_ref="b@v1")
        norm_req, _ = mock_preprocess.run(req)
        assert norm_req.request_id is not None
        assert len(norm_req.request_id) > 0


# ── TestMockCoverage ──────────────────────────────────────────────────────────


class TestMockCoverage:
    def test_returns_list_of_coverage_plans(self, document, rubric):
        plans = mock_coverage.run(document, rubric)
        assert isinstance(plans, list)
        assert all(isinstance(p, CoveragePlan) for p in plans)

    def test_one_plan_per_dimension(self, document, rubric):
        plans = mock_coverage.run(document, rubric)
        assert len(plans) == len(rubric.dimensions)

    def test_plan_dimension_ids_match_rubric(self, document, rubric):
        plans = mock_coverage.run(document, rubric)
        plan_dim_ids = {p.dimension_id for p in plans}
        rubric_dim_ids = {d["dimension_id"] for d in rubric.dimensions}
        assert plan_dim_ids == rubric_dim_ids

    def test_plan_required_facets_from_config(self, document, rubric):
        plans = mock_coverage.run(document, rubric)
        for plan in plans:
            dim = rubric.dimension_by_id[plan.dimension_id]
            expected_facets = dim["observation_schema"]["required_facets"]
            assert plan.required_facets == expected_facets

    def test_plan_target_unit_ids_from_document(self, document, rubric):
        plans = mock_coverage.run(document, rubric)
        doc_unit_ids = {u.unit_id for u in document.text_units}
        for plan in plans:
            assert set(plan.target_unit_ids).issubset(doc_unit_ids)

    def test_deterministic(self, document, rubric):
        plans1 = mock_coverage.run(document, rubric)
        plans2 = mock_coverage.run(document, rubric)
        ids1 = sorted(p.plan_id for p in plans1)
        ids2 = sorted(p.plan_id for p in plans2)
        assert ids1 == ids2


# ── TestMockExtractor ─────────────────────────────────────────────────────────


class TestMockExtractor:
    def test_returns_list_of_evidence_spans(self, plan_dim1, document):
        spans = mock_extractor.run(plan_dim1, document)
        assert isinstance(spans, list)
        assert all(isinstance(s, EvidenceSpan) for s in spans)

    def test_spans_non_empty(self, plan_dim1, document):
        spans = mock_extractor.run(plan_dim1, document)
        assert len(spans) > 0

    def test_spans_have_correct_dimension_id(self, plan_dim1, document):
        spans = mock_extractor.run(plan_dim1, document)
        for span in spans:
            assert span.dimension_id == plan_dim1.dimension_id

    def test_spans_reference_plan_facets(self, plan_dim1, document):
        spans = mock_extractor.run(plan_dim1, document)
        all_facet_ids = {fid for s in spans for fid in s.facet_ids}
        for facet_id in plan_dim1.required_facets:
            assert facet_id in all_facet_ids

    def test_spans_have_correct_document_id(self, plan_dim1, document):
        spans = mock_extractor.run(plan_dim1, document)
        for span in spans:
            assert span.document_id == document.document_id

    def test_deterministic(self, plan_dim1, document):
        spans1 = mock_extractor.run(plan_dim1, document)
        spans2 = mock_extractor.run(plan_dim1, document)
        assert [s.span_id for s in spans1] == [s.span_id for s in spans2]

    def test_different_plans_produce_different_spans(self, coverage_plans, document):
        plan_a, plan_b = coverage_plans[0], coverage_plans[1]
        spans_a = mock_extractor.run(plan_a, document)
        spans_b = mock_extractor.run(plan_b, document)
        ids_a = {s.span_id for s in spans_a}
        ids_b = {s.span_id for s in spans_b}
        assert ids_a.isdisjoint(ids_b)


# ── TestMockObserver ──────────────────────────────────────────────────────────


class TestMockObserver:
    def test_returns_dimension_observation(self, spans_dim1, plan_dim1):
        obs = mock_observer.run(spans_dim1, plan_dim1)
        assert isinstance(obs, DimensionObservation)

    def test_observation_dimension_matches_plan(self, spans_dim1, plan_dim1):
        obs = mock_observer.run(spans_dim1, plan_dim1)
        assert obs.dimension_id == plan_dim1.dimension_id

    def test_observation_document_matches_plan(self, spans_dim1, plan_dim1):
        obs = mock_observer.run(spans_dim1, plan_dim1)
        assert obs.document_id == plan_dim1.document_id

    def test_facet_findings_cover_all_required_facets(self, spans_dim1, plan_dim1):
        obs = mock_observer.run(spans_dim1, plan_dim1)
        finding_facet_ids = {ff.facet_id for ff in obs.facet_findings}
        for facet_id in plan_dim1.required_facets:
            assert facet_id in finding_facet_ids

    def test_facet_findings_are_facetfinding_instances(self, spans_dim1, plan_dim1):
        obs = mock_observer.run(spans_dim1, plan_dim1)
        for ff in obs.facet_findings:
            assert isinstance(ff, FacetFinding)

    def test_supporting_spans_reference_input_spans(self, spans_dim1, plan_dim1):
        obs = mock_observer.run(spans_dim1, plan_dim1)
        input_span_ids = {s.span_id for s in spans_dim1}
        for sid in obs.supporting_span_ids:
            assert sid in input_span_ids

    def test_full_coverage_produces_high_confidence(self, spans_dim1, plan_dim1):
        obs = mock_observer.run(spans_dim1, plan_dim1)
        assert obs.observation_confidence == ObservationConfidence.HIGH

    def test_empty_spans_produces_low_confidence(self, plan_dim1):
        obs = mock_observer.run([], plan_dim1)
        assert obs.observation_confidence == ObservationConfidence.LOW

    def test_deterministic(self, spans_dim1, plan_dim1):
        obs1 = mock_observer.run(spans_dim1, plan_dim1)
        obs2 = mock_observer.run(spans_dim1, plan_dim1)
        assert obs1.observation_id == obs2.observation_id
        assert obs1 == obs2


# ── TestMockScorer ────────────────────────────────────────────────────────────


class TestMockScorer:
    def test_returns_score_hypothesis(self, observation_dim1, rubric):
        hyp = mock_scorer.run(observation_dim1, rubric, "rater_1")
        assert isinstance(hyp, ScoreHypothesis)

    def test_score_within_scale_range(self, observation_dim1, rubric):
        hyp = mock_scorer.run(observation_dim1, rubric, "rater_1")
        scale = rubric.scale_by_id[SCALE_ID]
        assert scale["min"] <= hyp.score.canonical_score <= scale["max"]

    def test_score_uses_scale_ref_from_rubric(self, observation_dim1, rubric):
        hyp = mock_scorer.run(observation_dim1, rubric, "rater_1")
        dim = rubric.dimension_by_id[observation_dim1.dimension_id]
        assert hyp.score.scale_ref == dim["scale_ref"]

    def test_hypothesis_dimension_id_matches_observation(self, observation_dim1, rubric):
        hyp = mock_scorer.run(observation_dim1, rubric, "rater_1")
        assert hyp.dimension_id == observation_dim1.dimension_id

    def test_hypothesis_rater_id_preserved(self, observation_dim1, rubric):
        hyp = mock_scorer.run(observation_dim1, rubric, "rater_1")
        assert hyp.rater_id == "rater_1"

    def test_confidence_in_valid_range(self, observation_dim1, rubric):
        hyp = mock_scorer.run(observation_dim1, rubric, "rater_1")
        assert 0.0 <= hyp.confidence <= 1.0

    def test_different_rater_ids_produce_different_hypothesis_ids(self, observation_dim1, rubric):
        hyp1 = mock_scorer.run(observation_dim1, rubric, "rater_1")
        hyp2 = mock_scorer.run(observation_dim1, rubric, "rater_2")
        assert hyp1.hypothesis_id != hyp2.hypothesis_id

    def test_deterministic(self, observation_dim1, rubric):
        hyp1 = mock_scorer.run(observation_dim1, rubric, "rater_1")
        hyp2 = mock_scorer.run(observation_dim1, rubric, "rater_1")
        assert hyp1 == hyp2


# ── TestMockConsistencyChecker ────────────────────────────────────────────────


class TestMockConsistencyChecker:
    def test_returns_list(self, policy):
        result = mock_consistency_checker.run([], policy)
        assert isinstance(result, list)

    def test_no_conflicts_when_scores_agree(self, policy):
        # Both raters give score 2 for dim_001 (diff = 0 <= 1)
        hyps = [
            _make_hypothesis("dim_001", "obs-1", "rater_1", 2),
            _make_hypothesis("dim_001", "obs-1", "rater_2", 2),
        ]
        conflicts = mock_consistency_checker.run(hyps, policy)
        assert conflicts == []

    def test_no_conflicts_when_adjacent(self, policy):
        # Diff = 1, threshold is > 1, so no conflict
        hyps = [
            _make_hypothesis("dim_001", "obs-1", "rater_1", 2),
            _make_hypothesis("dim_001", "obs-1", "rater_2", 3),
        ]
        conflicts = mock_consistency_checker.run(hyps, policy)
        assert conflicts == []

    def test_conflict_when_non_adjacent(self, policy):
        # Diff = 3 > 1 → conflict
        hyps = [
            _make_hypothesis("dim_001", "obs-1", "rater_1", 1),
            _make_hypothesis("dim_001", "obs-1", "rater_2", 4),
        ]
        conflicts = mock_consistency_checker.run(hyps, policy)
        assert len(conflicts) == 1

    def test_conflict_record_type(self, policy):
        hyps = [
            _make_hypothesis("dim_001", "obs-1", "rater_1", 1),
            _make_hypothesis("dim_001", "obs-1", "rater_2", 4),
        ]
        conflicts = mock_consistency_checker.run(hyps, policy)
        assert isinstance(conflicts[0], ConflictRecord)

    def test_conflict_record_has_correct_dimension(self, policy):
        hyps = [
            _make_hypothesis("dim_001", "obs-1", "rater_1", 1),
            _make_hypothesis("dim_001", "obs-1", "rater_2", 4),
        ]
        conflicts = mock_consistency_checker.run(hyps, policy)
        assert conflicts[0].dimension_id == "dim_001"

    def test_conflict_record_references_both_hypotheses(self, policy):
        h1 = _make_hypothesis("dim_001", "obs-1", "rater_1", 1)
        h2 = _make_hypothesis("dim_001", "obs-1", "rater_2", 4)
        conflicts = mock_consistency_checker.run([h1, h2], policy)
        assert h1.hypothesis_id in conflicts[0].hypothesis_ids
        assert h2.hypothesis_id in conflicts[0].hypothesis_ids

    def test_independent_per_dimension(self, policy):
        # dim_001 conflicts, dim_002 does not
        hyps = [
            _make_hypothesis("dim_001", "obs-1", "rater_1", 1),
            _make_hypothesis("dim_001", "obs-1", "rater_2", 4),
            _make_hypothesis("dim_002", "obs-2", "rater_1", 2),
            _make_hypothesis("dim_002", "obs-2", "rater_2", 2),
        ]
        conflicts = mock_consistency_checker.run(hyps, policy)
        assert len(conflicts) == 1
        assert conflicts[0].dimension_id == "dim_001"

    def test_deterministic(self, policy):
        hyps = [
            _make_hypothesis("dim_001", "obs-1", "rater_1", 1),
            _make_hypothesis("dim_001", "obs-1", "rater_2", 4),
        ]
        c1 = mock_consistency_checker.run(hyps, policy)
        c2 = mock_consistency_checker.run(hyps, policy)
        assert c1[0].conflict_id == c2[0].conflict_id


# ── TestMockAdjudicator ───────────────────────────────────────────────────────


class TestMockAdjudicator:
    def _make_conflict(self, h1: ScoreHypothesis, h2: ScoreHypothesis) -> ConflictRecord:
        return ConflictRecord(
            conflict_id=f"conflict-{h1.hypothesis_id}-{h2.hypothesis_id}",
            dimension_id=h1.dimension_id,
            hypothesis_ids=[h1.hypothesis_id, h2.hypothesis_id],
            conflict_type=ConflictType.NON_ADJACENT,
            trigger_rule_id="non_adjacent",
            conflict_detail="mock conflict",
            recommended_path=ResolutionPath.THIRD_RATER,
        )

    def test_returns_tuple_of_two_lists(self, policy):
        result = mock_adjudicator.run([], [], policy)
        assert isinstance(result, tuple)
        assert len(result) == 2
        adj_records, decisions = result
        assert isinstance(adj_records, list)
        assert isinstance(decisions, list)

    def test_no_conflict_produces_decision_for_each_dimension(self, policy):
        hyps = [
            _make_hypothesis("dim_001", "obs-1", "rater_1", 3),
            _make_hypothesis("dim_001", "obs-1", "rater_2", 3),
            _make_hypothesis("dim_002", "obs-2", "rater_1", 2),
            _make_hypothesis("dim_002", "obs-2", "rater_2", 2),
        ]
        _, decisions = mock_adjudicator.run([], hyps, policy)
        dim_ids = {d.dimension_id for d in decisions}
        assert "dim_001" in dim_ids
        assert "dim_002" in dim_ids

    def test_adjudication_record_for_each_conflict(self, policy):
        h1 = _make_hypothesis("dim_001", "obs-1", "rater_1", 1)
        h2 = _make_hypothesis("dim_001", "obs-1", "rater_2", 4)
        conflict = self._make_conflict(h1, h2)
        adj_records, _ = mock_adjudicator.run([conflict], [h1, h2], policy)
        assert len(adj_records) == 1
        assert isinstance(adj_records[0], AdjudicationRecord)

    def test_adjudication_record_references_conflict(self, policy):
        h1 = _make_hypothesis("dim_001", "obs-1", "rater_1", 1)
        h2 = _make_hypothesis("dim_001", "obs-1", "rater_2", 4)
        conflict = self._make_conflict(h1, h2)
        adj_records, _ = mock_adjudicator.run([conflict], [h1, h2], policy)
        assert adj_records[0].conflict_id == conflict.conflict_id

    def test_resolved_conflict_produces_final_decision(self, policy):
        h1 = _make_hypothesis("dim_001", "obs-1", "rater_1", 1)
        h2 = _make_hypothesis("dim_001", "obs-1", "rater_2", 4)
        conflict = self._make_conflict(h1, h2)
        adj_records, decisions = mock_adjudicator.run([conflict], [h1, h2], policy)
        assert adj_records[0].is_resolved is True
        dim_ids = {d.dimension_id for d in decisions}
        assert "dim_001" in dim_ids

    def test_final_decision_is_correct_type(self, policy):
        h1 = _make_hypothesis("dim_001", "obs-1", "rater_1", 1)
        h2 = _make_hypothesis("dim_001", "obs-1", "rater_2", 4)
        conflict = self._make_conflict(h1, h2)
        _, decisions = mock_adjudicator.run([conflict], [h1, h2], policy)
        for d in decisions:
            assert isinstance(d, FinalDimensionDecision)

    def test_deterministic(self, policy):
        h1 = _make_hypothesis("dim_001", "obs-1", "rater_1", 1)
        h2 = _make_hypothesis("dim_001", "obs-1", "rater_2", 4)
        conflict = self._make_conflict(h1, h2)
        r1, d1 = mock_adjudicator.run([conflict], [h1, h2], policy)
        r2, d2 = mock_adjudicator.run([conflict], [h1, h2], policy)
        assert r1[0].adjudication_id == r2[0].adjudication_id
        assert d1[0].decision_id == d2[0].decision_id


# ── TestMockFeedback ──────────────────────────────────────────────────────────


class TestMockFeedback:
    def _make_decision(self, dim_id: str, score_val: int) -> FinalDimensionDecision:
        score = create_score_representation(score_val, SCALE_ID)
        return FinalDimensionDecision(
            decision_id=f"dec-{dim_id}",
            dimension_id=dim_id,
            final_score=score,
            primary_hypothesis_id=f"hyp-{dim_id}-rater_1",
            adjudication_id=None,
            evidence_span_ids=[],
            descriptor_refs=[f"level_{score_val}"],
            decision_confidence=0.85,
            decision_note=None,
        )

    def _make_observation(self, dim_id: str) -> DimensionObservation:
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

    def test_returns_dict(self, rubric):
        decisions = [self._make_decision("dim_001", 3)]
        observations = [self._make_observation("dim_001")]
        result = mock_feedback.run(decisions, observations, rubric)
        assert isinstance(result, dict)

    def test_has_dimensions_key(self, rubric):
        decisions = [self._make_decision("dim_001", 3)]
        observations = [self._make_observation("dim_001")]
        result = mock_feedback.run(decisions, observations, rubric)
        assert "dimensions" in result

    def test_dimensions_keyed_by_dimension_id(self, rubric):
        decisions = [
            self._make_decision("dim_001", 3),
            self._make_decision("dim_002", 2),
        ]
        observations = [
            self._make_observation("dim_001"),
            self._make_observation("dim_002"),
        ]
        result = mock_feedback.run(decisions, observations, rubric)
        assert "dim_001" in result["dimensions"]
        assert "dim_002" in result["dimensions"]

    def test_dimension_entry_has_score(self, rubric):
        decisions = [self._make_decision("dim_001", 3)]
        observations = [self._make_observation("dim_001")]
        result = mock_feedback.run(decisions, observations, rubric)
        entry = result["dimensions"]["dim_001"]
        assert "final_score" in entry
        assert entry["final_score"] == 3

    def test_dimension_entry_has_display_score(self, rubric):
        decisions = [self._make_decision("dim_001", 3)]
        observations = [self._make_observation("dim_001")]
        result = mock_feedback.run(decisions, observations, rubric)
        entry = result["dimensions"]["dim_001"]
        assert "display_score" in entry
        assert entry["display_score"] == "3"

    def test_dimension_name_from_rubric_config(self, rubric):
        decisions = [self._make_decision("dim_001", 3)]
        observations = [self._make_observation("dim_001")]
        result = mock_feedback.run(decisions, observations, rubric)
        entry = result["dimensions"]["dim_001"]
        assert entry["dimension_name"] == DIM1["name"]

    def test_deterministic(self, rubric):
        decisions = [self._make_decision("dim_001", 3)]
        observations = [self._make_observation("dim_001")]
        r1 = mock_feedback.run(decisions, observations, rubric)
        r2 = mock_feedback.run(decisions, observations, rubric)
        assert r1["dimensions"]["dim_001"]["final_score"] == r2["dimensions"]["dim_001"]["final_score"]
