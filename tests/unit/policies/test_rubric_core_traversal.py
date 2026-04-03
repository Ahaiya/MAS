"""
Tests for Rubric Core Traversal Policy (Phase 4, Task 1).

Verifies that:
1. rubric_core.py provides config-driven dimension traversal utilities.
2. All dimension iteration, scale range lookup, facet extraction, and
   descriptor lookup flows through the policy layer.
3. With different rubric configs (different dimension counts, different
   scales), the system adapts without code changes.

Zero-hardcoding: no trait names, dimension codes, score values, or
scale ranges are hardcoded in these tests. All fixtures use synthetic
identifiers to ensure tests are config-agnostic.
"""

import pytest

from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.evidence import (
    DimensionObservation,
    ObservationConfidence,
)
from src.contracts.request_models import (
    CoveragePlan,
    NormalizedDocument,
    TextUnit,
)
from src.policies.rubric_core import (
    DimensionTraversal,
    build_dimension_traversal,
    get_descriptor_refs_for_score,
    get_evidence_requirements,
    get_required_facets,
    get_scale_range,
    get_scale_ref,
    list_dimension_ids,
    validate_score_in_range,
)


# ── Fixtures: 2-dimension rubric with scale 1-4 ──────────────────────────────

SCALE_A = {"scale_id": "scale_a", "min": 1, "max": 4}

DIM_ALPHA = {
    "dimension_id": "dim_alpha",
    "code": "A",
    "name": "Alpha Dimension",
    "scale_ref": "scale_a",
    "observation_schema": {"required_facets": ["facet_a1", "facet_a2"]},
    "evidence_requirements": {
        "minimum_evidence_units": 2,
        "allowed_evidence_scope": ["span", "global"],
        "require_textual_grounding": True,
    },
    "levels": [
        {"rank": 1, "summary": "Low alpha",
         "descriptors": ["alpha descriptor 1a", "alpha descriptor 1b"]},
        {"rank": 2, "summary": "Mid alpha",
         "descriptors": ["alpha descriptor 2a"]},
        {"rank": 3, "summary": "High alpha",
         "descriptors": ["alpha descriptor 3a", "alpha descriptor 3b"]},
        {"rank": 4, "summary": "Top alpha",
         "descriptors": ["alpha descriptor 4a"]},
    ],
}

DIM_BETA = {
    "dimension_id": "dim_beta",
    "code": "B",
    "name": "Beta Dimension",
    "scale_ref": "scale_a",
    "observation_schema": {"required_facets": ["facet_b1"]},
    "evidence_requirements": {
        "minimum_evidence_units": 1,
        "allowed_evidence_scope": ["span"],
        "require_textual_grounding": True,
    },
    "levels": [
        {"rank": 1, "summary": "Low beta",
         "descriptors": ["beta descriptor 1a"]},
        {"rank": 2, "summary": "Mid beta",
         "descriptors": ["beta descriptor 2a"]},
        {"rank": 3, "summary": "High beta",
         "descriptors": ["beta descriptor 3a"]},
        {"rank": 4, "summary": "Top beta",
         "descriptors": ["beta descriptor 4a"]},
    ],
}


@pytest.fixture
def rubric_2dim():
    dims = [DIM_ALPHA, DIM_BETA]
    return RubricSnapshot(
        rubric_id="test_rubric_2dim",
        rubric_version="v1",
        rubric_name="Two-Dimension Test Rubric",
        dimensions=dims,
        scales=[SCALE_A],
        dimension_by_id={d["dimension_id"]: d for d in dims},
        dimension_by_code={d["code"]: d for d in dims},
        scale_by_id={"scale_a": SCALE_A},
    )


# ── Fixtures: 3-dimension rubric with scale 1-10 ─────────────────────────────

SCALE_B = {"scale_id": "scale_b", "min": 1, "max": 10}

DIM_X = {
    "dimension_id": "dim_x",
    "code": "X",
    "name": "X Dimension",
    "scale_ref": "scale_b",
    "observation_schema": {"required_facets": ["facet_x1", "facet_x2", "facet_x3"]},
    "evidence_requirements": {"minimum_evidence_units": 3},
}

DIM_Y = {
    "dimension_id": "dim_y",
    "code": "Y",
    "name": "Y Dimension",
    "scale_ref": "scale_b",
    "observation_schema": {"required_facets": ["facet_y1"]},
    "evidence_requirements": {"minimum_evidence_units": 1},
}

DIM_Z = {
    "dimension_id": "dim_z",
    "code": "Z",
    "name": "Z Dimension",
    "scale_ref": "scale_b",
    "observation_schema": {"required_facets": ["facet_z1", "facet_z2"]},
    "evidence_requirements": {"minimum_evidence_units": 2},
}


@pytest.fixture
def rubric_3dim():
    dims = [DIM_X, DIM_Y, DIM_Z]
    return RubricSnapshot(
        rubric_id="test_rubric_3dim",
        rubric_version="v2",
        rubric_name="Three-Dimension Test Rubric",
        dimensions=dims,
        scales=[SCALE_B],
        dimension_by_id={d["dimension_id"]: d for d in dims},
        dimension_by_code={d["code"]: d for d in dims},
        scale_by_id={"scale_b": SCALE_B},
    )


# ── Helper ────────────────────────────────────────────────────────────────────


def _make_document():
    text = "Hello world. This is a test."
    return NormalizedDocument(
        document_id="doc-test",
        request_id="req-test",
        normalized_text=text,
        text_units=[
            TextUnit(unit_id="u1", document_id="doc-test",
                     text="Hello world.",
                     start_offset=0, end_offset=12,
                     unit_type="sentence", sequence_index=0),
            TextUnit(unit_id="u2", document_id="doc-test",
                     text=" This is a test.",
                     start_offset=12, end_offset=28,
                     unit_type="sentence", sequence_index=1),
        ],
        char_count=len(text),
        word_count=6,
        document_metadata={},
    )


def _make_observation(dim_id):
    return DimensionObservation(
        observation_id=f"obs-{dim_id}",
        document_id="doc-test",
        dimension_id=dim_id,
        supporting_span_ids=["span-1"],
        counter_span_ids=[],
        facet_findings=[],
        observation_confidence=ObservationConfidence.HIGH,
        uncertainty_notes=[],
    )


# ── Unit tests for rubric_core policy functions ──────────────────────────────


class TestListDimensionIds:
    def test_returns_ordered_ids(self, rubric_2dim):
        ids = list_dimension_ids(rubric_2dim)
        assert ids == ["dim_alpha", "dim_beta"]

    def test_three_dimension_rubric(self, rubric_3dim):
        ids = list_dimension_ids(rubric_3dim)
        assert ids == ["dim_x", "dim_y", "dim_z"]

    def test_count_matches_rubric(self, rubric_2dim):
        ids = list_dimension_ids(rubric_2dim)
        assert len(ids) == len(rubric_2dim.dimensions)


class TestGetScaleRange:
    def test_returns_correct_range(self, rubric_2dim):
        assert get_scale_range(rubric_2dim, "dim_alpha") == (1, 4)

    def test_different_scale(self, rubric_3dim):
        assert get_scale_range(rubric_3dim, "dim_x") == (1, 10)

    def test_unknown_dimension_raises(self, rubric_2dim):
        with pytest.raises(ValueError, match="not found"):
            get_scale_range(rubric_2dim, "nonexistent")


class TestGetScaleRef:
    def test_returns_scale_ref(self, rubric_2dim):
        assert get_scale_ref(rubric_2dim, "dim_alpha") == "scale_a"

    def test_different_scale(self, rubric_3dim):
        assert get_scale_ref(rubric_3dim, "dim_x") == "scale_b"

    def test_unknown_dimension_raises(self, rubric_2dim):
        with pytest.raises(ValueError, match="not found"):
            get_scale_ref(rubric_2dim, "nonexistent")


class TestGetRequiredFacets:
    def test_returns_facets(self, rubric_2dim):
        facets = get_required_facets(rubric_2dim, "dim_alpha")
        assert facets == ["facet_a1", "facet_a2"]

    def test_single_facet_dimension(self, rubric_2dim):
        facets = get_required_facets(rubric_2dim, "dim_beta")
        assert facets == ["facet_b1"]

    def test_three_facet_dimension(self, rubric_3dim):
        facets = get_required_facets(rubric_3dim, "dim_x")
        assert facets == ["facet_x1", "facet_x2", "facet_x3"]

    def test_unknown_dimension_raises(self, rubric_2dim):
        with pytest.raises(ValueError):
            get_required_facets(rubric_2dim, "nonexistent")


class TestGetEvidenceRequirements:
    def test_returns_dict(self, rubric_2dim):
        reqs = get_evidence_requirements(rubric_2dim, "dim_alpha")
        assert isinstance(reqs, dict)
        assert reqs["minimum_evidence_units"] == 2

    def test_unknown_dimension_raises(self, rubric_2dim):
        with pytest.raises(ValueError):
            get_evidence_requirements(rubric_2dim, "nonexistent")


class TestGetDescriptorRefsForScore:
    def test_returns_descriptors_for_valid_score(self, rubric_2dim):
        descs = get_descriptor_refs_for_score(rubric_2dim, "dim_alpha", 1)
        assert descs == ["alpha descriptor 1a", "alpha descriptor 1b"]

    def test_returns_descriptors_for_max_score(self, rubric_2dim):
        descs = get_descriptor_refs_for_score(rubric_2dim, "dim_alpha", 4)
        assert descs == ["alpha descriptor 4a"]

    def test_returns_empty_for_unlisted_score(self, rubric_2dim):
        descs = get_descriptor_refs_for_score(rubric_2dim, "dim_alpha", 99)
        assert descs == []

    def test_returns_empty_when_no_levels(self, rubric_3dim):
        descs = get_descriptor_refs_for_score(rubric_3dim, "dim_x", 5)
        assert descs == []

    def test_unknown_dimension_raises(self, rubric_2dim):
        with pytest.raises(ValueError):
            get_descriptor_refs_for_score(rubric_2dim, "nonexistent", 1)


class TestValidateScoreInRange:
    def test_valid_min_score(self, rubric_2dim):
        assert validate_score_in_range(rubric_2dim, "dim_alpha", 1)

    def test_valid_max_score(self, rubric_2dim):
        assert validate_score_in_range(rubric_2dim, "dim_alpha", 4)

    def test_valid_mid_score(self, rubric_2dim):
        assert validate_score_in_range(rubric_2dim, "dim_alpha", 3)

    def test_invalid_score_below(self, rubric_2dim):
        assert not validate_score_in_range(rubric_2dim, "dim_alpha", 0)

    def test_invalid_score_above(self, rubric_2dim):
        assert not validate_score_in_range(rubric_2dim, "dim_alpha", 5)

    def test_different_scale_boundary(self, rubric_3dim):
        assert validate_score_in_range(rubric_3dim, "dim_x", 10)
        assert not validate_score_in_range(rubric_3dim, "dim_x", 11)

    def test_unknown_dimension_returns_false(self, rubric_2dim):
        assert not validate_score_in_range(rubric_2dim, "nonexistent", 1)


class TestBuildDimensionTraversal:
    def test_returns_list_of_traversals(self, rubric_2dim):
        traversals = build_dimension_traversal(rubric_2dim)
        assert isinstance(traversals, list)
        assert all(isinstance(t, DimensionTraversal) for t in traversals)

    def test_count_matches_dimensions(self, rubric_2dim):
        traversals = build_dimension_traversal(rubric_2dim)
        assert len(traversals) == 2

    def test_traversal_ids_match_config(self, rubric_2dim):
        traversals = build_dimension_traversal(rubric_2dim)
        ids = [t.dimension_id for t in traversals]
        assert ids == ["dim_alpha", "dim_beta"]

    def test_traversal_scale_range_correct(self, rubric_2dim):
        traversals = build_dimension_traversal(rubric_2dim)
        for t in traversals:
            assert t.scale_min == 1
            assert t.scale_max == 4

    def test_traversal_required_facets(self, rubric_2dim):
        traversals = build_dimension_traversal(rubric_2dim)
        alpha = next(t for t in traversals if t.dimension_id == "dim_alpha")
        assert alpha.required_facets == ["facet_a1", "facet_a2"]

    def test_traversal_code_and_name(self, rubric_2dim):
        traversals = build_dimension_traversal(rubric_2dim)
        alpha = next(t for t in traversals if t.dimension_id == "dim_alpha")
        assert alpha.code == "A"
        assert alpha.name == "Alpha Dimension"

    def test_three_dimension_traversal(self, rubric_3dim):
        traversals = build_dimension_traversal(rubric_3dim)
        assert len(traversals) == 3
        for t in traversals:
            assert t.scale_min == 1
            assert t.scale_max == 10

    def test_traversal_is_frozen(self, rubric_2dim):
        traversals = build_dimension_traversal(rubric_2dim)
        with pytest.raises(AttributeError):
            traversals[0].dimension_id = "modified"  # type: ignore[misc]

