"""
Policy-aware pipeline integration tests (Phase 4, Task 5).

Verifies the full policy chain working together:
  rubric_core → adjudication → aggregation → explanation → export → feedback

Key invariants tested:
- canonical_score and display_score are always separate fields.
- Display annotation (e.g., "-") is cosmetic: canonical_score is unchanged.
- CompositeDecision uses canonical_score, not display_score.
- All policy modules compose without internal data coupling.
- ExplanationViolation is surfaced when policy chain is broken.

Zero-hardcoding: synthetic dimension IDs, rater IDs, scale IDs.
"""

import pytest

from src.contracts.artifact_bundle import PolicySnapshot, RubricSnapshot
from src.contracts.evidence import (
    DimensionObservation,
    EvidenceScope,
    EvidenceSpan,
    ObservationConfidence,
)
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import (
    AdjudicationRecord,
    FinalDimensionDecision,
    ResolutionPath,
    ScoreHypothesis,
)
from src.policies.rubric_core import (
    build_dimension_traversal,
    get_scale_range,
    get_scale_ref,
    list_dimension_ids,
    validate_score_in_range,
)
from src.policies.adjudication import evaluate_all_triggers
from src.policies.aggregation import compute_composite
from src.policies.explanation import (
    enforce_explanation_policy,
    render_dimension_explanation,
)
from src.pipeline.export import build_pipeline_output
from src.agents import feedback
from src.providers.base import (
    BaseProvider,
    LLMRequest,
    LLMResponse,
    ProviderCapability,
    TokenUsage,
)
from src.providers.prompt_loader import PromptTemplate


# ── Synthetic fixtures ─────────────────────────────────────────────────────────

SCALE_S = {"scale_id": "scale_s", "min": 1, "max": 4}

DIM_M = {
    "dimension_id": "dim_m",
    "code": "M",
    "name": "Mu Dimension",
    "scale_ref": "scale_s",
    "observation_schema": {"required_facets": ["facet_m1"]},
    "evidence_requirements": {"minimum_evidence_units": 1},
    "levels": [
        {"rank": 1, "summary": "Low mu", "descriptors": ["mu desc 1"]},
        {"rank": 3, "summary": "Mid mu", "descriptors": ["mu desc 3"]},
        {"rank": 4, "summary": "High mu", "descriptors": ["mu desc 4"]},
    ],
}

DIM_N = {
    "dimension_id": "dim_n",
    "code": "N",
    "name": "Nu Dimension",
    "scale_ref": "scale_s",
    "observation_schema": {"required_facets": ["facet_n1"]},
    "evidence_requirements": {"minimum_evidence_units": 1},
    "levels": [
        {"rank": 2, "summary": "Low nu", "descriptors": ["nu desc 2"]},
        {"rank": 4, "summary": "High nu", "descriptors": ["nu desc 4"]},
    ],
}


@pytest.fixture
def rubric():
    dims = [DIM_M, DIM_N]
    return RubricSnapshot(
        rubric_id="integration_rubric",
        rubric_version="v1",
        rubric_name="Integration Test Rubric",
        dimensions=dims,
        scales=[SCALE_S],
        dimension_by_id={d["dimension_id"]: d for d in dims},
        dimension_by_code={d["code"]: d for d in dims},
        scale_by_id={"scale_s": SCALE_S},
    )


@pytest.fixture
def policy():
    return PolicySnapshot(
        adjudication_policy={
            "triggers": [
                {
                    "trigger_id": "trig_distance",
                    "type": "score_distance",
                    "priority": 1,
                    "applies_to_dimensions": ["*"],
                    "exclusions": [],
                    "threshold": {"operator": ">", "value": 1},
                    "action": "invoke_resolution",
                }
            ]
        },
        aggregation_policy={
            "policy_id": "integration_agg",
            "composite_formula": [
                {
                    "variant_id": "no_res",
                    "applies_when": "resolution_not_used",
                    "source_raters": ["rater_a", "rater_b"],
                    "aggregation_method": "average_per_trait_then_weighted_sum",
                    "weights": {"dim_m": 2, "dim_n": 1},
                },
                {
                    "variant_id": "with_res",
                    "applies_when": "resolution_used",
                    "source_raters": ["rater_c"],
                    "aggregation_method": "direct_weighted_sum",
                    "weights": {"dim_m": 2, "dim_n": 1},
                },
            ],
        },
        explanation_policy={
            "policy_id": "strict_exp",
            "requirements": {
                "require_descriptor_alignment": True,
                "require_evidence_links": True,
            },
            "citation_rules": {"min_citations_per_dimension": 1},
            "output_constraints": {
                "max_commentary_length_per_dimension": 200,
                "require_evidence_score_chain": True,
            },
            "render_sections": [],
        },
        policy_version="integration-v1",
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _hyp(dim_id: str, rater_id: str, score_val: int) -> ScoreHypothesis:
    return ScoreHypothesis(
        hypothesis_id=f"hyp-{dim_id}-{rater_id}",
        observation_id=f"obs-{dim_id}",
        dimension_id=dim_id,
        rater_id=rater_id,
        score=create_score_representation(score_val, "scale_s"),
        descriptor_refs=[f"desc-{dim_id}-{score_val}"],
        evidence_span_ids=[f"span-{dim_id}"],
        rationale="sample_rationale",
        confidence=0.85,
    )


def _decision(
    dim_id: str, score_val: int, annotation: str = None, adj_id: str = None
) -> FinalDimensionDecision:
    score = create_score_representation(score_val, "scale_s", annotation)
    return FinalDimensionDecision(
        decision_id=f"dec-{dim_id}",
        dimension_id=dim_id,
        final_score=score,
        primary_hypothesis_id=f"hyp-{dim_id}-rater_a",
        adjudication_id=adj_id,
        evidence_span_ids=[f"span-{dim_id}"],
        descriptor_refs=[f"mu desc {score_val}" if dim_id == "dim_m" else f"nu desc {score_val}"],
        decision_confidence=0.9,
        decision_note=None,
    )


def _span(dim_id: str) -> EvidenceSpan:
    return EvidenceSpan(
        span_id=f"span-{dim_id}",
        document_id="doc-int",
        unit_id="u1",
        text_quote="sample text for integration",
        start_offset=0,
        end_offset=30,
        scope=EvidenceScope.SPAN,
        dimension_id=dim_id,
        facet_ids=["facet_m1"],
        extraction_note=None,
    )


def _observation(dim_id: str) -> DimensionObservation:
    return DimensionObservation(
        observation_id=f"obs-{dim_id}",
        document_id="doc-int",
        dimension_id=dim_id,
        supporting_span_ids=[f"span-{dim_id}"],
        counter_span_ids=[],
        facet_findings=[],
        observation_confidence=ObservationConfidence.HIGH,
        uncertainty_notes=[],
    )


class _FeedbackProvider(BaseProvider):
    @property
    def name(self) -> str:
        return "integration_feedback_provider"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.TEXT_COMPLETION})

    def complete(self, request: LLMRequest) -> LLMResponse:
        _ = request
        return LLMResponse(
            content="",
            structured_data=None,
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            provider_name=self.name,
            model_id="integration-feedback-v1",
        )


def _feedback_template() -> PromptTemplate:
    return PromptTemplate(
        template_text=(
            "DIM={{ dimension_name }}\n"
            "CONF={{ observation_confidence }}\n"
            "RAT={{ scorer_rationale }}\n"
            "NOTE={{ decision_note }}\n"
        ),
        metadata={"template_version": "v1", "compatible_dimensions": ["*"]},
        source_path="inline-feedback-template",
    )


# ── Tests: rubric_core layer ──────────────────────────────────────────────────


class TestRubricCoreInPipeline:
    def test_traversal_produces_one_entry_per_dimension(self, rubric):
        traversals = build_dimension_traversal(rubric)
        assert len(traversals) == len(rubric.dimensions)

    def test_traversal_scale_from_config(self, rubric):
        traversals = build_dimension_traversal(rubric)
        for t in traversals:
            scale_min, scale_max = get_scale_range(rubric, t.dimension_id)
            assert t.scale_min == scale_min
            assert t.scale_max == scale_max

    def test_score_validated_against_scale(self, rubric):
        assert validate_score_in_range(rubric, "dim_m", 1)
        assert validate_score_in_range(rubric, "dim_m", 4)
        assert not validate_score_in_range(rubric, "dim_m", 5)

    def test_dimension_ids_match_config_order(self, rubric):
        ids = list_dimension_ids(rubric)
        assert ids == ["dim_m", "dim_n"]


# ── Tests: adjudication trigger evaluation ────────────────────────────────────


class TestAdjudicationInPipeline:
    def test_no_conflict_when_adjacent_scores(self, policy):
        # Scores differ by 1 — threshold is >1, no conflict expected
        hyps = [
            _hyp("dim_m", "rater_a", 3),
            _hyp("dim_m", "rater_b", 4),
        ]
        conflicts = evaluate_all_triggers(hyps, policy)
        assert conflicts == []

    def test_conflict_when_non_adjacent_scores(self, policy):
        # Scores differ by 2 — threshold is >1, conflict expected
        hyps = [
            _hyp("dim_m", "rater_a", 2),
            _hyp("dim_m", "rater_b", 4),
        ]
        conflicts = evaluate_all_triggers(hyps, policy)
        assert len(conflicts) == 1
        assert conflicts[0].dimension_id == "dim_m"

    def test_conflict_record_has_hypothesis_ids(self, policy):
        hyps = [
            _hyp("dim_m", "rater_a", 1),
            _hyp("dim_m", "rater_b", 4),
        ]
        conflicts = evaluate_all_triggers(hyps, policy)
        assert len(conflicts[0].hypothesis_ids) == 2

    def test_multiple_dimensions_checked(self, policy):
        hyps = [
            _hyp("dim_m", "rater_a", 1),
            _hyp("dim_m", "rater_b", 4),
            _hyp("dim_n", "rater_a", 1),
            _hyp("dim_n", "rater_b", 4),
        ]
        conflicts = evaluate_all_triggers(hyps, policy)
        conflict_dims = {c.dimension_id for c in conflicts}
        assert "dim_m" in conflict_dims
        assert "dim_n" in conflict_dims


# ── Tests: canonical vs display score separation ──────────────────────────────


class TestCanonicalDisplaySeparation:
    def test_canonical_score_is_int_unaffected_by_annotation(self):
        score_plain = create_score_representation(4, "scale_s")
        score_annotated = create_score_representation(4, "scale_s", "-")
        # Both have canonical_score = 4
        assert score_plain.canonical_score == score_annotated.canonical_score == 4

    def test_display_score_differs_when_annotated(self):
        score_plain = create_score_representation(4, "scale_s")
        score_annotated = create_score_representation(4, "scale_s", "-")
        assert score_plain.display_score == "4"
        assert score_annotated.display_score == "4-"

    def test_composite_uses_canonical_not_display(self, policy):
        # Two decisions with same canonical_score but different display_score
        decisions_plain = [
            _decision("dim_m", 4, annotation=None),
            _decision("dim_n", 2, annotation=None),
        ]
        decisions_annotated = [
            _decision("dim_m", 4, annotation="-"),
            _decision("dim_n", 2, annotation=None),
        ]
        hyps = [
            _hyp("dim_m", "rater_a", 4), _hyp("dim_m", "rater_b", 4),
            _hyp("dim_n", "rater_a", 2), _hyp("dim_n", "rater_b", 2),
        ]
        comp_plain = compute_composite(decisions_plain, hyps, [], policy)
        comp_annotated = compute_composite(decisions_annotated, hyps, [], policy)
        # Composite score should be same regardless of annotation
        assert comp_plain.composite_score.canonical_score == comp_annotated.composite_score.canonical_score

    def test_export_includes_both_canonical_and_display(self, policy):
        decisions = [_decision("dim_m", 4, annotation="-")]
        output = build_pipeline_output(decisions, None)
        trait = next(t for t in output["trait_scores"] if t["dimension_id"] == "dim_m")
        assert trait["canonical_score"] == 4
        assert trait["display_score"] == "4-"
        assert trait["canonical_score"] != trait["display_score"]

    def test_explanation_carries_both_score_fields(self, rubric, policy):
        decision = _decision("dim_m", 4, annotation="-")
        spans = [_span("dim_m")]
        exp = render_dimension_explanation(decision, spans, rubric, policy)
        assert exp.canonical_score == 4
        assert exp.display_score == "4-"


# ── Tests: aggregation in pipeline ────────────────────────────────────────────


class TestAggregationInPipeline:
    def test_composite_separate_from_trait_scores(self, policy):
        decisions = [_decision("dim_m", 3), _decision("dim_n", 2)]
        hyps = [
            _hyp("dim_m", "rater_a", 3), _hyp("dim_m", "rater_b", 3),
            _hyp("dim_n", "rater_a", 2), _hyp("dim_n", "rater_b", 2),
        ]
        composite = compute_composite(decisions, hyps, [], policy)
        output = build_pipeline_output(decisions, composite)
        # Trait scores and composite are in separate keys
        assert len(output["trait_scores"]) == 2
        assert output["composite"] is not None
        assert output["composite"]["composite_score"]["canonical_score"] > 0

    def test_no_composite_when_not_configured(self):
        no_agg_policy = PolicySnapshot(
            adjudication_policy={"triggers": []},
            aggregation_policy={},
            explanation_policy={},
            policy_version="no-agg",
        )
        decisions = [_decision("dim_m", 3)]
        result = compute_composite(decisions, [], [], no_agg_policy)
        assert result is None

    def test_export_composite_none_produces_null(self, policy):
        decisions = [_decision("dim_m", 3)]
        output = build_pipeline_output(decisions, None)
        assert output["composite"] is None


# ── Tests: explanation + feedback chain ──────────────────────────────────────


class TestExplanationFeedbackChain:
    def test_feedback_output_has_all_required_keys(self, rubric, policy):
        decisions = [_decision("dim_m", 3), _decision("dim_n", 4)]
        observations = [_observation("dim_m"), _observation("dim_n")]
        spans = [_span("dim_m"), _span("dim_n")]
        out = feedback.run(
            decisions=decisions,
            observations=observations,
            spans=spans,
            rubric=rubric,
            policy=policy,
            provider=_FeedbackProvider(),
            template=_feedback_template(),
        )
        assert "dimensions" in out
        assert "violations" in out
        assert "generated_at" in out

    def test_feedback_dimension_names_from_rubric(self, rubric, policy):
        decisions = [_decision("dim_m", 3)]
        out = feedback.run(
            decisions=decisions,
            observations=[],
            spans=[_span("dim_m")],
            rubric=rubric,
            policy=policy,
            provider=_FeedbackProvider(),
            template=_feedback_template(),
        )
        assert out["dimensions"]["dim_m"]["dimension_name"] == "Mu Dimension"

    def test_feedback_no_violations_for_valid_decisions(self, rubric, policy):
        decisions = [_decision("dim_m", 3), _decision("dim_n", 4)]
        spans = [_span("dim_m"), _span("dim_n")]
        out = feedback.run(
            decisions=decisions,
            observations=[],
            spans=spans,
            rubric=rubric,
            policy=policy,
            provider=_FeedbackProvider(),
            template=_feedback_template(),
        )
        assert out["violations"] == []

    def test_feedback_reports_violation_for_missing_evidence(self, rubric, policy):
        # Decision with no evidence_span_ids
        dec = FinalDimensionDecision(
            decision_id="dec-dim_m",
            dimension_id="dim_m",
            final_score=create_score_representation(3, "scale_s"),
            primary_hypothesis_id="hyp-dim_m",
            adjudication_id=None,
            evidence_span_ids=[],        # empty — violation expected
            descriptor_refs=["mu desc 3"],
            decision_confidence=0.9,
            decision_note=None,
        )
        out = feedback.run(
            decisions=[dec],
            observations=[],
            spans=[],
            rubric=rubric,
            policy=policy,
            provider=_FeedbackProvider(),
            template=_feedback_template(),
        )
        assert len(out["violations"]) > 0

    def test_explanation_chain_closed_when_valid(self, rubric, policy):
        decision = _decision("dim_m", 3)
        spans = [_span("dim_m")]
        exp = render_dimension_explanation(decision, spans, rubric, policy)
        violations = enforce_explanation_policy([exp], policy)
        assert violations == []

    def test_commentary_respects_max_length(self, rubric, policy):
        decision = _decision("dim_m", 3)
        spans = [_span("dim_m")]
        exp = render_dimension_explanation(decision, spans, rubric, policy)
        max_len = policy.explanation_policy["output_constraints"][
            "max_commentary_length_per_dimension"
        ]
        assert len(exp.commentary) <= max_len
