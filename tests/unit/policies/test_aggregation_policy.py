"""
Tests for Aggregation Policy (Phase 4, Task 3).

Verifies that:
1. aggregation.py computes composite scores from config-driven formulas.
2. Two variants are supported: average_per_trait_then_weighted_sum (no resolution)
   and direct_weighted_sum (with resolution).
3. Weighted-average variants are also supported for indicator-level aggregation.
4. Dimensions with weight=0 are excluded from the composite.
5. CompositeDecision is produced with correct fields; all formula values come
   from config — no hardcoded trait names, weights, or rater IDs.
6. export.py distinguishes trait-level output from composite output.

Zero-hardcoding: all dimension IDs, rater IDs, weights, and scores use
synthetic identifiers to ensure tests are config-agnostic.
"""

import pytest

from src.contracts.artifact_bundle import PolicySnapshot
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import (
    AdjudicationRecord,
    CompositeDecision,
    FinalDimensionDecision,
    ResolutionPath,
    ScoreHypothesis,
)
from src.policies.aggregation import compute_composite
from src.pipeline.export import build_pipeline_output


# ── Synthetic aggregation policy fixture ─────────────────────────────────────
#
# 3 dimensions: dim_a (weight 2), dim_b (weight 1), dim_c (weight 0 = excluded)
# Two variants: no-resolution (average rater_x and rater_y) and
#               with-resolution (direct rater_z scores)

_AGG_POLICY = {
    "policy_id": "test_agg",
    "policy_version": "v1",
    "composite_formula": [
        {
            "variant_id": "no_res",
            "applies_when": "resolution_not_used",
            "source_raters": ["rater_x", "rater_y"],
            "aggregation_method": "average_per_trait_then_weighted_sum",
            "weights": {
                "dim_a": 2,
                "dim_b": 1,
                "dim_c": 0,
            },
        },
        {
            "variant_id": "with_res",
            "applies_when": "resolution_used",
            "source_raters": ["rater_z"],
            "aggregation_method": "direct_weighted_sum",
            "weights": {
                "dim_a": 2,
                "dim_b": 1,
                "dim_c": 0,
            },
        },
    ],
}

_AGG_POLICY_AVERAGE = {
    "policy_id": "test_agg_avg",
    "policy_version": "v1",
    "composite_formula": [
        {
            "variant_id": "no_res_avg",
            "applies_when": "resolution_not_used",
            "source_raters": ["rater_x", "rater_y"],
            "aggregation_method": "average_per_trait_then_weighted_average",
            "weights": {
                "dim_a": 2,
                "dim_b": 1,
                "dim_c": 0,
            },
        },
        {
            "variant_id": "with_res_avg",
            "applies_when": "resolution_used",
            "source_raters": ["rater_z"],
            "aggregation_method": "direct_weighted_average",
            "weights": {
                "dim_a": 2,
                "dim_b": 1,
                "dim_c": 0,
            },
        },
    ],
}


@pytest.fixture
def policy():
    return PolicySnapshot(
        adjudication_policy={"triggers": []},
        aggregation_policy=_AGG_POLICY,
        explanation_policy={},
        policy_version="test-v1",
    )


@pytest.fixture
def policy_no_formula():
    """Policy with no composite_formula defined."""
    return PolicySnapshot(
        adjudication_policy={"triggers": []},
        aggregation_policy={"policy_id": "empty", "policy_version": "v0"},
        explanation_policy={},
        policy_version="test-v0",
    )


@pytest.fixture
def policy_average():
    return PolicySnapshot(
        adjudication_policy={"triggers": []},
        aggregation_policy=_AGG_POLICY_AVERAGE,
        explanation_policy={},
        policy_version="test-v1",
    )


# ── Score / hypothesis / decision helpers ─────────────────────────────────────


def _score(val: int, scale: str = "scale_test") -> "ScoreRepresentation":
    return create_score_representation(val, scale)


def _hyp(dim_id: str, rater_id: str, score_val: int, hid: str = None) -> ScoreHypothesis:
    hid = hid or f"hyp-{dim_id}-{rater_id}"
    return ScoreHypothesis(
        hypothesis_id=hid,
        observation_id=f"obs-{dim_id}",
        dimension_id=dim_id,
        rater_id=rater_id,
        score=_score(score_val),
        descriptor_refs=[],
        evidence_span_ids=[],
        rationale="sample_rationale",
        confidence=0.9,
    )


def _decision(dim_id: str, score_val: int, adj_id: str = None) -> FinalDimensionDecision:
    return FinalDimensionDecision(
        decision_id=f"dec-{dim_id}",
        dimension_id=dim_id,
        final_score=_score(score_val),
        primary_hypothesis_id=f"hyp-{dim_id}",
        adjudication_id=adj_id,
        evidence_span_ids=[],
        descriptor_refs=[],
        decision_confidence=0.9,
        decision_note=None,
    )


def _adjudication(dim_id: str, is_resolved: bool = True) -> AdjudicationRecord:
    return AdjudicationRecord(
        adjudication_id=f"adj-{dim_id}",
        conflict_id=f"con-{dim_id}",
        dimension_id=dim_id,
        resolution_path=ResolutionPath.THIRD_RATER,
        policy_rule_id="rule-1",
        resolved_score=_score(3) if is_resolved else None,
        resolver_hypothesis_id=f"hyp-{dim_id}-rater_z" if is_resolved else None,
        resolution_note=None,
        is_resolved=is_resolved,
    )


# ── Tests: no-resolution variant ──────────────────────────────────────────────


class TestCompositeNoResolution:
    """Variant: average_per_trait_then_weighted_sum, no adjudication used."""

    def _make_hypotheses(self):
        # rater_x: dim_a=4, dim_b=2, dim_c=3
        # rater_y: dim_a=2, dim_b=4, dim_c=1
        return [
            _hyp("dim_a", "rater_x", 4),
            _hyp("dim_b", "rater_x", 2),
            _hyp("dim_c", "rater_x", 3),
            _hyp("dim_a", "rater_y", 2),
            _hyp("dim_b", "rater_y", 4),
            _hyp("dim_c", "rater_y", 1),
        ]

    def test_returns_composite_decision(self, policy):
        hyps = self._make_hypotheses()
        decisions = [_decision("dim_a", 3), _decision("dim_b", 3), _decision("dim_c", 2)]
        result = compute_composite(decisions, hyps, [], policy, policy_ref="test://agg")
        assert isinstance(result, CompositeDecision)

    def test_composite_id_is_set(self, policy):
        hyps = self._make_hypotheses()
        decisions = [_decision("dim_a", 3), _decision("dim_b", 3), _decision("dim_c", 2)]
        result = compute_composite(decisions, hyps, [], policy)
        assert result.composite_id != ""

    def test_score_formula_no_resolution(self, policy):
        # dim_a avg = (4+2)/2 = 3, weight 2 → 6
        # dim_b avg = (2+4)/2 = 3, weight 1 → 3
        # dim_c weight = 0 → excluded
        # total = 9
        hyps = self._make_hypotheses()
        decisions = [_decision("dim_a", 3), _decision("dim_b", 3), _decision("dim_c", 2)]
        result = compute_composite(decisions, hyps, [], policy)
        assert result.composite_score.canonical_score == 9

    def test_weight_zero_dimension_excluded(self, policy):
        # Even if dim_c has a high score, weight=0 means it's not counted
        hyps = [
            _hyp("dim_a", "rater_x", 1),
            _hyp("dim_b", "rater_x", 1),
            _hyp("dim_c", "rater_x", 99),  # high score but weight=0
            _hyp("dim_a", "rater_y", 1),
            _hyp("dim_b", "rater_y", 1),
            _hyp("dim_c", "rater_y", 99),
        ]
        decisions = [_decision("dim_a", 1), _decision("dim_b", 1), _decision("dim_c", 99)]
        result = compute_composite(decisions, hyps, [], policy)
        # dim_a avg=1, weight 2 → 2; dim_b avg=1, weight 1 → 1; total = 3
        assert result.composite_score.canonical_score == 3

    def test_contributing_decision_ids(self, policy):
        hyps = self._make_hypotheses()
        decisions = [_decision("dim_a", 3), _decision("dim_b", 3), _decision("dim_c", 2)]
        result = compute_composite(decisions, hyps, [], policy)
        # Only dimensions with non-zero weight should be contributing
        assert "dec-dim_a" in result.contributing_decision_ids
        assert "dec-dim_b" in result.contributing_decision_ids

    def test_policy_ref_stored(self, policy):
        hyps = self._make_hypotheses()
        decisions = [_decision("dim_a", 3), _decision("dim_b", 3), _decision("dim_c", 2)]
        result = compute_composite(decisions, hyps, [], policy, policy_ref="agg://test")
        assert result.aggregation_policy_ref == "agg://test"

    def test_aggregation_detail_contains_variant_id(self, policy):
        hyps = self._make_hypotheses()
        decisions = [_decision("dim_a", 3), _decision("dim_b", 3)]
        result = compute_composite(decisions, hyps, [], policy)
        assert "variant_id" in result.aggregation_detail

    def test_single_rater_hypothesis_still_averages(self, policy):
        # Only rater_x provided for dim_a → avg = score of rater_x only
        hyps = [
            _hyp("dim_a", "rater_x", 4),
            _hyp("dim_b", "rater_x", 2),
            _hyp("dim_b", "rater_y", 4),
        ]
        decisions = [_decision("dim_a", 4), _decision("dim_b", 3)]
        result = compute_composite(decisions, hyps, [], policy)
        # dim_a: only rater_x=4, avg=4, weight 2 → 8
        # dim_b: avg=(2+4)/2=3, weight 1 → 3
        # total = 11
        assert result.composite_score.canonical_score == 11


# ── Tests: with-resolution variant ────────────────────────────────────────────


class TestCompositeWithResolution:
    """Variant: direct_weighted_sum, adjudication was used."""

    def test_uses_decision_final_score_not_hypotheses(self, policy):
        # With resolution, decisions hold the authoritative score
        decisions = [_decision("dim_a", 4), _decision("dim_b", 2), _decision("dim_c", 1)]
        adjudications = [_adjudication("dim_a", is_resolved=True)]
        # dim_a: 4*2=8, dim_b: 2*1=2, total=10
        result = compute_composite(decisions, [], adjudications, policy)
        assert result.composite_score.canonical_score == 10

    def test_resolution_variant_selected_when_adjudication_exists(self, policy):
        decisions = [_decision("dim_a", 3), _decision("dim_b", 3)]
        adjudications = [_adjudication("dim_a", is_resolved=True)]
        result = compute_composite(decisions, [], adjudications, policy)
        assert result.aggregation_detail["variant_id"] == "with_res"

    def test_no_resolution_variant_selected_when_no_adjudication(self, policy):
        hyps = [
            _hyp("dim_a", "rater_x", 3),
            _hyp("dim_a", "rater_y", 3),
            _hyp("dim_b", "rater_x", 2),
            _hyp("dim_b", "rater_y", 2),
        ]
        decisions = [_decision("dim_a", 3), _decision("dim_b", 2)]
        result = compute_composite(decisions, hyps, [], policy)
        assert result.aggregation_detail["variant_id"] == "no_res"

    def test_unresolved_adjudication_still_uses_no_resolution_variant(self, policy):
        # An adjudication record that is NOT resolved should not trigger with_res variant
        hyps = [
            _hyp("dim_a", "rater_x", 3),
            _hyp("dim_a", "rater_y", 3),
            _hyp("dim_b", "rater_x", 2),
            _hyp("dim_b", "rater_y", 2),
        ]
        decisions = [_decision("dim_a", 3), _decision("dim_b", 2)]
        adjudications = [_adjudication("dim_a", is_resolved=False)]
        result = compute_composite(decisions, hyps, adjudications, policy)
        assert result.aggregation_detail["variant_id"] == "no_res"

    def test_weight_zero_excluded_in_resolution_variant(self, policy):
        decisions = [_decision("dim_a", 3), _decision("dim_b", 2), _decision("dim_c", 99)]
        adjudications = [_adjudication("dim_a", is_resolved=True)]
        # dim_a: 3*2=6, dim_b: 2*1=2, dim_c: weight=0, total=8
        result = compute_composite(decisions, [], adjudications, policy)
        assert result.composite_score.canonical_score == 8


class TestCompositeWeightedAverage:
    """Variant: indicator-level weighted average keeps the original score scale."""

    def test_no_resolution_weighted_average(self, policy_average):
        hyps = [
            _hyp("dim_a", "rater_x", 4),
            _hyp("dim_a", "rater_y", 2),
            _hyp("dim_b", "rater_x", 2),
            _hyp("dim_b", "rater_y", 4),
            _hyp("dim_c", "rater_x", 5),
            _hyp("dim_c", "rater_y", 5),
        ]
        decisions = [_decision("dim_a", 3), _decision("dim_b", 3), _decision("dim_c", 5)]
        result = compute_composite(decisions, hyps, [], policy_average)
        assert result is not None
        assert result.composite_score.canonical_score == 3
        assert result.aggregation_detail["aggregation_method"] == (
            "average_per_trait_then_weighted_average"
        )
        assert result.aggregation_detail["weight_total"] == 3

    def test_resolution_weighted_average(self, policy_average):
        decisions = [_decision("dim_a", 4), _decision("dim_b", 2), _decision("dim_c", 5)]
        adjudications = [_adjudication("dim_a", is_resolved=True)]
        result = compute_composite(decisions, [], adjudications, policy_average)
        assert result is not None
        assert result.composite_score.canonical_score == 3
        assert result.aggregation_detail["aggregation_method"] == "direct_weighted_average"
        assert result.aggregation_detail["weight_total"] == 3


# ── Tests: edge cases ─────────────────────────────────────────────────────────


class TestCompositeEdgeCases:
    def test_returns_none_when_no_formula(self, policy_no_formula):
        decisions = [_decision("dim_a", 3)]
        result = compute_composite(decisions, [], [], policy_no_formula)
        assert result is None

    def test_empty_decisions_returns_none_or_zero(self, policy):
        # No decisions to aggregate
        result = compute_composite([], [], [], policy)
        # Either None or a 0-score composite — either is acceptable
        if result is not None:
            assert result.composite_score.canonical_score == 0

    def test_all_weight_zero_dimensions(self, policy):
        # Only dim_c which has weight 0 — no contributing dims
        decisions = [_decision("dim_c", 5)]
        hyps = [
            _hyp("dim_c", "rater_x", 5),
            _hyp("dim_c", "rater_y", 5),
        ]
        result = compute_composite(decisions, hyps, [], policy)
        # Either None or 0 score
        if result is not None:
            assert result.composite_score.canonical_score == 0

    def test_composite_decision_is_frozen(self, policy):
        hyps = [
            _hyp("dim_a", "rater_x", 3),
            _hyp("dim_a", "rater_y", 3),
        ]
        decisions = [_decision("dim_a", 3)]
        result = compute_composite(decisions, hyps, [], policy)
        assert result is not None
        with pytest.raises(AttributeError):
            result.composite_id = "modified"  # type: ignore[misc]


# ── Tests: export.py ──────────────────────────────────────────────────────────


class TestBuildPipelineOutput:
    def _make_decisions(self):
        return [
            _decision("dim_a", 3),
            _decision("dim_b", 2),
        ]

    def _make_composite(self):
        from src.contracts.score_representation import create_score_representation
        return CompositeDecision(
            composite_id="cmp-1",
            aggregation_policy_ref="agg://test",
            contributing_decision_ids=["dec-dim_a", "dec-dim_b"],
            composite_score=create_score_representation(8, "scale_test"),
            aggregation_detail={"variant_id": "no_res"},
            composite_note=None,
        )

    def test_output_has_trait_scores_key(self):
        out = build_pipeline_output(self._make_decisions(), None)
        assert "trait_scores" in out

    def test_output_has_composite_key(self):
        out = build_pipeline_output(self._make_decisions(), None)
        assert "composite" in out

    def test_output_has_indicator_score_key(self):
        out = build_pipeline_output(self._make_decisions(), None)
        assert "indicator_score" in out

    def test_trait_scores_count_matches_decisions(self):
        out = build_pipeline_output(self._make_decisions(), None)
        assert len(out["trait_scores"]) == 2

    def test_composite_none_when_not_provided(self):
        out = build_pipeline_output(self._make_decisions(), None)
        assert out["composite"] is None
        assert out["indicator_score"] is None

    def test_composite_populated_when_provided(self):
        composite = self._make_composite()
        out = build_pipeline_output(self._make_decisions(), composite)
        assert out["composite"] is not None
        assert out["indicator_score"] is not None

    def test_trait_scores_contain_dimension_id(self):
        out = build_pipeline_output(self._make_decisions(), None)
        dim_ids = {ts["dimension_id"] for ts in out["trait_scores"]}
        assert "dim_a" in dim_ids
        assert "dim_b" in dim_ids

    def test_trait_scores_contain_canonical_score(self):
        out = build_pipeline_output(self._make_decisions(), None)
        scores = {ts["dimension_id"]: ts["canonical_score"] for ts in out["trait_scores"]}
        assert scores["dim_a"] == 3
        assert scores["dim_b"] == 2

    def test_composite_score_in_output(self):
        composite = self._make_composite()
        out = build_pipeline_output(self._make_decisions(), composite)
        assert out["composite"]["composite_score"]["canonical_score"] == 8
        assert out["indicator_score"]["composite_score"]["canonical_score"] == 8

    def test_indicator_score_and_composite_share_payload(self):
        composite = self._make_composite()
        out = build_pipeline_output(self._make_decisions(), composite)
        assert out["indicator_score"] == out["composite"]

    def test_empty_decisions_produces_empty_trait_scores(self):
        out = build_pipeline_output([], None)
        assert out["trait_scores"] == []
