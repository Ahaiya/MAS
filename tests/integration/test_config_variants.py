"""
Config variant integration tests (Phase 4, Task 5).

Verifies that the system adapts to different configuration variants without
code changes. Tests three axes of variation:

1. Rubric variants — different dimension counts and scale ranges
2. Adjudication variants — different thresholds trigger different conflicts
3. Aggregation variants — different weights produce different composite scores
4. Display annotation isolation — annotation never contaminates canonical score

Design principle: change the config dict, not the code, to prove zero-hardcoding.
All test fixtures use synthetic identifiers only.
"""

import pytest

from src.contracts.artifact_bundle import PolicySnapshot, RubricSnapshot
from src.contracts.evidence import EvidenceScope, EvidenceSpan
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import FinalDimensionDecision, ScoreHypothesis
from src.policies.rubric_core import (
    build_dimension_traversal,
    get_scale_range,
    list_dimension_ids,
    validate_score_in_range,
)
from src.policies.adjudication import evaluate_all_triggers
from src.policies.aggregation import compute_composite
from src.policies.explanation import render_dimension_explanation
from src.pipeline.export import build_pipeline_output
from src.agents import coverage, deterministic_scorer


# ── Rubric builder helper ──────────────────────────────────────────────────────


def _make_rubric(dims: list, scales: list) -> RubricSnapshot:
    return RubricSnapshot(
        rubric_id="variant_rubric",
        rubric_version="v1",
        rubric_name="Variant Test Rubric",
        dimensions=dims,
        scales=scales,
        dimension_by_id={d["dimension_id"]: d for d in dims},
        dimension_by_code={d["code"]: d for d in dims},
        scale_by_id={s["scale_id"]: s for s in scales},
    )


def _make_policy(triggers=None, weights=None, source_raters=None) -> PolicySnapshot:
    triggers = triggers or []
    weights = weights or {}
    source_raters = source_raters or ["rater_a", "rater_b"]
    return PolicySnapshot(
        adjudication_policy={"triggers": triggers},
        aggregation_policy={
            "policy_id": "variant_agg",
            "composite_formula": [
                {
                    "variant_id": "no_res",
                    "applies_when": "resolution_not_used",
                    "source_raters": source_raters,
                    "aggregation_method": "average_per_trait_then_weighted_sum",
                    "weights": weights,
                }
            ],
        },
        explanation_policy={
            "requirements": {
                "require_descriptor_alignment": False,
                "require_evidence_links": False,
            },
            "citation_rules": {"min_citations_per_dimension": 0},
            "output_constraints": {
                "max_commentary_length_per_dimension": 200,
                "require_evidence_score_chain": False,
            },
            "render_sections": [],
        },
        policy_version="variant-v1",
    )


def _hyp(dim_id: str, rater_id: str, score_val: int, scale: str = "s1") -> ScoreHypothesis:
    return ScoreHypothesis(
        hypothesis_id=f"hyp-{dim_id}-{rater_id}",
        observation_id=f"obs-{dim_id}",
        dimension_id=dim_id,
        rater_id=rater_id,
        score=create_score_representation(score_val, scale),
        descriptor_refs=[f"desc-{score_val}"],
        evidence_span_ids=[f"span-{dim_id}"],
        rationale="mock",
        confidence=0.9,
    )


def _decision(dim_id: str, score_val: int, scale: str = "s1", annotation=None) -> FinalDimensionDecision:
    return FinalDimensionDecision(
        decision_id=f"dec-{dim_id}",
        dimension_id=dim_id,
        final_score=create_score_representation(score_val, scale, annotation),
        primary_hypothesis_id=f"hyp-{dim_id}",
        adjudication_id=None,
        evidence_span_ids=[f"span-{dim_id}"],
        descriptor_refs=[f"desc-{score_val}"],
        decision_confidence=0.9,
        decision_note=None,
    )


def _make_doc():
    from src.contracts.request_models import NormalizedDocument, TextUnit
    text = "Integration variant test document."
    return NormalizedDocument(
        document_id="doc-var",
        request_id="req-var",
        normalized_text=text,
        text_units=[
            TextUnit(
                unit_id="u1",
                document_id="doc-var",
                text=text,
                start_offset=0,
                end_offset=len(text),
                unit_type="sentence",
                sequence_index=0,
            )
        ],
        char_count=len(text),
        word_count=len(text.split()),
        document_metadata={},
    )


# ── Tests: rubric variants ─────────────────────────────────────────────────────


class TestRubricVariants:
    """Changing the rubric config changes dimension count and scale, not code."""

    def _rubric_2dim_scale4(self):
        dims = [
            {
                "dimension_id": "dim_r1", "code": "R1", "name": "R1 Dim",
                "scale_ref": "s4",
                "observation_schema": {"required_facets": ["f1"]},
                "evidence_requirements": {"minimum_evidence_units": 1},
                "levels": [],
            },
            {
                "dimension_id": "dim_r2", "code": "R2", "name": "R2 Dim",
                "scale_ref": "s4",
                "observation_schema": {"required_facets": ["f2"]},
                "evidence_requirements": {"minimum_evidence_units": 1},
                "levels": [],
            },
        ]
        scales = [{"scale_id": "s4", "min": 1, "max": 4}]
        return _make_rubric(dims, scales)

    def _rubric_3dim_scale10(self):
        dims = [
            {
                "dimension_id": "dim_t1", "code": "T1", "name": "T1 Dim",
                "scale_ref": "s10",
                "observation_schema": {"required_facets": ["f1"]},
                "evidence_requirements": {"minimum_evidence_units": 1},
                "levels": [],
            },
            {
                "dimension_id": "dim_t2", "code": "T2", "name": "T2 Dim",
                "scale_ref": "s10",
                "observation_schema": {"required_facets": ["f2"]},
                "evidence_requirements": {"minimum_evidence_units": 1},
                "levels": [],
            },
            {
                "dimension_id": "dim_t3", "code": "T3", "name": "T3 Dim",
                "scale_ref": "s10",
                "observation_schema": {"required_facets": ["f3"]},
                "evidence_requirements": {"minimum_evidence_units": 1},
                "levels": [],
            },
        ]
        scales = [{"scale_id": "s10", "min": 1, "max": 10}]
        return _make_rubric(dims, scales)

    def test_traversal_count_adapts_to_dim_count(self):
        t2 = build_dimension_traversal(self._rubric_2dim_scale4())
        t3 = build_dimension_traversal(self._rubric_3dim_scale10())
        assert len(t2) == 2
        assert len(t3) == 3

    def test_scale_range_adapts_to_config(self):
        rubric4 = self._rubric_2dim_scale4()
        rubric10 = self._rubric_3dim_scale10()
        assert get_scale_range(rubric4, "dim_r1") == (1, 4)
        assert get_scale_range(rubric10, "dim_t1") == (1, 10)

    def test_score_5_invalid_for_scale_4(self):
        rubric = self._rubric_2dim_scale4()
        assert not validate_score_in_range(rubric, "dim_r1", 5)

    def test_score_5_valid_for_scale_10(self):
        rubric = self._rubric_3dim_scale10()
        assert validate_score_in_range(rubric, "dim_t1", 5)

    def test_coverage_adapts_to_rubric(self):
        doc = _make_doc()
        rubric2 = self._rubric_2dim_scale4()
        rubric3 = self._rubric_3dim_scale10()
        plans2 = coverage.run(doc, rubric2)
        plans3 = coverage.run(doc, rubric3)
        assert len(plans2) == 2
        assert len(plans3) == 3

    def test_deterministic_scorer_respects_scale_range(self):
        from src.contracts.evidence import DimensionObservation, ObservationConfidence
        rubric4 = self._rubric_2dim_scale4()
        obs = DimensionObservation(
            observation_id="obs-dim_r1",
            document_id="doc-var",
            dimension_id="dim_r1",
            supporting_span_ids=["s1"],
            counter_span_ids=[],
            facet_findings=[],
            observation_confidence=ObservationConfidence.HIGH,
            uncertainty_notes=[],
        )
        hyp = deterministic_scorer.run(obs, rubric4, "rater_x")
        assert 1 <= hyp.score.canonical_score <= 4

    def test_dimension_ids_from_config_not_hardcoded(self):
        rubric = self._rubric_2dim_scale4()
        ids = list_dimension_ids(rubric)
        assert "dim_r1" in ids
        assert "dim_r2" in ids


# ── Tests: adjudication threshold variants ─────────────────────────────────────


class TestAdjudicationVariants:
    """Changing trigger threshold changes which conflicts are detected."""

    def _trigger(self, threshold_value: int) -> dict:
        return {
            "trigger_id": "t1",
            "type": "score_distance",
            "priority": 1,
            "applies_to_dimensions": ["*"],
            "exclusions": [],
            "threshold": {"operator": ">", "value": threshold_value},
            "action": "invoke_resolution",
        }

    def test_tight_threshold_catches_small_diff(self):
        # threshold >0: diff of 1 triggers conflict
        policy = _make_policy(triggers=[self._trigger(0)])
        hyps = [
            _hyp("dim_r1", "rater_a", 3, "s4"),
            _hyp("dim_r1", "rater_b", 4, "s4"),
        ]
        conflicts = evaluate_all_triggers(hyps, policy)
        assert len(conflicts) == 1

    def test_loose_threshold_does_not_trigger_for_same_diff(self):
        # threshold >2: diff of 1 does NOT trigger conflict
        policy = _make_policy(triggers=[self._trigger(2)])
        hyps = [
            _hyp("dim_r1", "rater_a", 3, "s4"),
            _hyp("dim_r1", "rater_b", 4, "s4"),
        ]
        conflicts = evaluate_all_triggers(hyps, policy)
        assert conflicts == []

    def test_no_triggers_means_no_conflicts(self):
        policy = _make_policy(triggers=[])
        hyps = [
            _hyp("dim_r1", "rater_a", 1, "s4"),
            _hyp("dim_r1", "rater_b", 4, "s4"),
        ]
        conflicts = evaluate_all_triggers(hyps, policy)
        assert conflicts == []

    def test_threshold_boundary_is_strict(self):
        # threshold >1: diff of exactly 1 should NOT trigger (> not >=)
        policy = _make_policy(triggers=[self._trigger(1)])
        hyps = [
            _hyp("dim_r1", "rater_a", 3, "s4"),
            _hyp("dim_r1", "rater_b", 4, "s4"),
        ]
        conflicts = evaluate_all_triggers(hyps, policy)
        assert conflicts == []

    def test_multiple_dimensions_with_different_diffs(self):
        policy = _make_policy(triggers=[self._trigger(1)])
        hyps = [
            # dim_r1: diff=3, triggers conflict
            _hyp("dim_r1", "rater_a", 1, "s4"),
            _hyp("dim_r1", "rater_b", 4, "s4"),
            # dim_r2: diff=1, no conflict (threshold >1)
            _hyp("dim_r2", "rater_a", 3, "s4"),
            _hyp("dim_r2", "rater_b", 4, "s4"),
        ]
        conflicts = evaluate_all_triggers(hyps, policy)
        conflict_dims = {c.dimension_id for c in conflicts}
        assert "dim_r1" in conflict_dims
        assert "dim_r2" not in conflict_dims


# ── Tests: aggregation weight variants ────────────────────────────────────────


class TestAggregationVariants:
    """Changing aggregation weights produces different composite scores."""

    def test_equal_weights_vs_skewed_weights(self):
        # dim_a=3, dim_b=3
        # Equal weights {1,1}: 3*1 + 3*1 = 6
        # Skewed {2,1}: 3*2 + 3*1 = 9
        hyps_a = [
            _hyp("dim_a", "rater_a", 3, "sa"),
            _hyp("dim_a", "rater_b", 3, "sa"),
            _hyp("dim_b", "rater_a", 3, "sa"),
            _hyp("dim_b", "rater_b", 3, "sa"),
        ]
        decisions = [_decision("dim_a", 3, "sa"), _decision("dim_b", 3, "sa")]

        policy_equal = _make_policy(weights={"dim_a": 1, "dim_b": 1})
        policy_skewed = _make_policy(weights={"dim_a": 2, "dim_b": 1})

        comp_equal = compute_composite(decisions, hyps_a, [], policy_equal)
        comp_skewed = compute_composite(decisions, hyps_a, [], policy_skewed)

        assert comp_equal.composite_score.canonical_score == 6
        assert comp_skewed.composite_score.canonical_score == 9

    def test_zero_weight_dimension_excluded(self):
        # dim_b has weight 0: should not contribute to composite
        hyps = [
            _hyp("dim_a", "rater_a", 3, "sa"),
            _hyp("dim_a", "rater_b", 3, "sa"),
            _hyp("dim_b", "rater_a", 99, "sa"),  # high score but excluded
            _hyp("dim_b", "rater_b", 99, "sa"),
        ]
        decisions = [_decision("dim_a", 3, "sa"), _decision("dim_b", 99, "sa")]
        policy = _make_policy(weights={"dim_a": 2, "dim_b": 0})
        comp = compute_composite(decisions, hyps, [], policy)
        # Only dim_a contributes: avg(3,3) * 2 = 6
        assert comp.composite_score.canonical_score == 6

    def test_weight_flip_changes_composite(self):
        # dim_a=4, dim_b=2
        # weights {2,1}: 4*2 + 2*1 = 10
        # weights {1,2}: 4*1 + 2*2 = 8
        hyps = [
            _hyp("dim_a", "rater_a", 4, "sa"),
            _hyp("dim_a", "rater_b", 4, "sa"),
            _hyp("dim_b", "rater_a", 2, "sa"),
            _hyp("dim_b", "rater_b", 2, "sa"),
        ]
        decisions = [_decision("dim_a", 4, "sa"), _decision("dim_b", 2, "sa")]

        policy_ab = _make_policy(weights={"dim_a": 2, "dim_b": 1})
        policy_ba = _make_policy(weights={"dim_a": 1, "dim_b": 2})

        comp_ab = compute_composite(decisions, hyps, [], policy_ab)
        comp_ba = compute_composite(decisions, hyps, [], policy_ba)

        assert comp_ab.composite_score.canonical_score == 10
        assert comp_ba.composite_score.canonical_score == 8


# ── Tests: display annotation isolation ──────────────────────────────────────


class TestDisplayAnnotationIsolation:
    """Display annotations are purely cosmetic and never affect computation."""

    def test_annotation_changes_display_not_canonical(self):
        plain = create_score_representation(3, "s4")
        annotated = create_score_representation(3, "s4", "-")
        assert plain.canonical_score == annotated.canonical_score == 3
        assert plain.display_score == "3"
        assert annotated.display_score == "3-"

    def test_different_annotations_same_canonical(self):
        dash = create_score_representation(4, "s4", "-")
        plus = create_score_representation(4, "s4", "+")
        assert dash.canonical_score == plus.canonical_score == 4

    def test_export_shows_both_fields_when_annotated(self):
        dec = _decision("dim_a", 3, annotation="-")
        out = build_pipeline_output([dec], None)
        trait = out["trait_scores"][0]
        assert trait["canonical_score"] == 3
        assert trait["display_score"] == "3-"

    def test_composite_uses_canonical_despite_annotation(self):
        # Both decisions annotated with "-" but canonical_score is 3
        hyps = [
            _hyp("dim_a", "rater_a", 3, "sa"),
            _hyp("dim_a", "rater_b", 3, "sa"),
        ]
        decisions_annotated = [_decision("dim_a", 3, "sa", annotation="-")]
        decisions_plain = [_decision("dim_a", 3, "sa")]
        policy = _make_policy(weights={"dim_a": 2})

        comp_ann = compute_composite(decisions_annotated, hyps, [], policy)
        comp_plain = compute_composite(decisions_plain, hyps, [], policy)

        # Both should produce same composite (annotation is irrelevant)
        assert comp_ann.composite_score.canonical_score == comp_plain.composite_score.canonical_score

    def test_explanation_preserves_display_score(self):
        rubric_dims = [
            {
                "dimension_id": "dim_a", "code": "A", "name": "A Dim",
                "scale_ref": "sa",
                "observation_schema": {"required_facets": ["f1"]},
                "evidence_requirements": {"minimum_evidence_units": 1},
                "levels": [{"rank": 3, "summary": "Mid A", "descriptors": ["a desc 3"]}],
            }
        ]
        rubric = _make_rubric(rubric_dims, [{"scale_id": "sa", "min": 1, "max": 4}])
        policy = _make_policy()
        decision = _decision("dim_a", 3, "sa", annotation="-")
        spans = [EvidenceSpan(
            span_id="span-dim_a", document_id="d1", unit_id="u1",
            text_quote="test quote", start_offset=0, end_offset=10,
            scope=EvidenceScope.SPAN, dimension_id="dim_a",
            facet_ids=["f1"], extraction_note=None,
        )]
        exp = render_dimension_explanation(decision, spans, rubric, policy)
        # canonical_score is the integer, display_score preserves the annotation
        assert exp.canonical_score == 3
        assert exp.display_score == "3-"
