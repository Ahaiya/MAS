"""
Tests for ScoreRepresentation contract (P1T5)

Covers:
- ScoreRepresentation creation with and without display annotation
- Field consistency invariant: display_score == str(canonical_score) + annotation
- Immutability (frozen dataclass)
- is_annotated() method
- to_dict() / from_dict() round-trip serialization
- create_score_representation factory
- Zero-hardcoding: scale_ref, annotation values, and score ranges all come from outside

No business facts (trait names, fixed scale ranges, specific annotation strings)
are hardcoded in these tests. All assertions use contract structure only.
"""

import pytest

from src.contracts.score_representation import (
    ScoreRepresentation,
    create_score_representation,
)


# ── Construction Tests ────────────────────────────────────────────────────────


class TestScoreRepresentationCreation:
    def test_plain_score_no_annotation(self):
        sr = ScoreRepresentation(
            canonical_score=4,
            display_annotation=None,
            display_score="4",
            scale_ref="ordinal_6",
        )
        assert sr.canonical_score == 4
        assert sr.display_annotation is None
        assert sr.display_score == "4"
        assert sr.scale_ref == "ordinal_6"

    def test_score_with_minus_annotation(self):
        sr = ScoreRepresentation(
            canonical_score=4,
            display_annotation="-",
            display_score="4-",
            scale_ref="ordinal_6",
        )
        assert sr.canonical_score == 4
        assert sr.display_annotation == "-"
        assert sr.display_score == "4-"

    def test_score_with_custom_annotation(self):
        """Any annotation string is accepted; allowed values come from config."""
        sr = ScoreRepresentation(
            canonical_score=3,
            display_annotation="+",
            display_score="3+",
            scale_ref="custom_scale",
        )
        assert sr.display_score == "3+"

    def test_display_score_inconsistency_raises_value_error(self):
        """display_score must equal str(canonical_score) + annotation."""
        with pytest.raises(ValueError):
            ScoreRepresentation(
                canonical_score=4,
                display_annotation=None,
                display_score="5",  # wrong canonical
                scale_ref="ordinal_6",
            )

    def test_display_score_missing_annotation_raises(self):
        with pytest.raises(ValueError):
            ScoreRepresentation(
                canonical_score=4,
                display_annotation="-",
                display_score="4",  # annotation not reflected
                scale_ref="ordinal_6",
            )

    def test_display_score_extra_annotation_raises(self):
        with pytest.raises(ValueError):
            ScoreRepresentation(
                canonical_score=4,
                display_annotation=None,
                display_score="4-",  # annotation present but annotation is None
                scale_ref="ordinal_6",
            )

    def test_canonical_score_must_be_int(self):
        with pytest.raises((TypeError, ValueError)):
            ScoreRepresentation(
                canonical_score="4",  # type: ignore
                display_annotation=None,
                display_score="4",
                scale_ref="ordinal_6",
            )

    def test_scale_ref_required(self):
        with pytest.raises(TypeError):
            ScoreRepresentation(  # type: ignore
                canonical_score=4,
                display_annotation=None,
                display_score="4",
                # scale_ref missing
            )


# ── Method Tests ──────────────────────────────────────────────────────────────


class TestScoreRepresentationMethods:
    def test_is_annotated_true_when_annotation_set(self):
        sr = ScoreRepresentation(
            canonical_score=4,
            display_annotation="-",
            display_score="4-",
            scale_ref="ordinal_6",
        )
        assert sr.is_annotated() is True

    def test_is_annotated_false_when_no_annotation(self):
        sr = ScoreRepresentation(
            canonical_score=4,
            display_annotation=None,
            display_score="4",
            scale_ref="ordinal_6",
        )
        assert sr.is_annotated() is False

    def test_to_dict_contains_all_fields(self):
        sr = ScoreRepresentation(
            canonical_score=4,
            display_annotation="-",
            display_score="4-",
            scale_ref="ordinal_6",
        )
        d = sr.to_dict()
        assert d["canonical_score"] == 4
        assert d["display_annotation"] == "-"
        assert d["display_score"] == "4-"
        assert d["scale_ref"] == "ordinal_6"

    def test_to_dict_no_extra_keys(self):
        sr = ScoreRepresentation(
            canonical_score=4,
            display_annotation=None,
            display_score="4",
            scale_ref="ordinal_6",
        )
        d = sr.to_dict()
        assert set(d.keys()) == {"canonical_score", "display_annotation", "display_score", "scale_ref"}

    def test_to_dict_annotation_none_preserved(self):
        sr = ScoreRepresentation(
            canonical_score=5,
            display_annotation=None,
            display_score="5",
            scale_ref="ordinal_6",
        )
        d = sr.to_dict()
        assert "display_annotation" in d
        assert d["display_annotation"] is None

    def test_from_dict_roundtrip_with_annotation(self):
        original = ScoreRepresentation(
            canonical_score=3,
            display_annotation="-",
            display_score="3-",
            scale_ref="ordinal_6",
        )
        restored = ScoreRepresentation.from_dict(original.to_dict())
        assert restored == original

    def test_from_dict_roundtrip_without_annotation(self):
        original = ScoreRepresentation(
            canonical_score=5,
            display_annotation=None,
            display_score="5",
            scale_ref="ordinal_6",
        )
        restored = ScoreRepresentation.from_dict(original.to_dict())
        assert restored == original

    def test_from_dict_annotation_none(self):
        d = {
            "canonical_score": 5,
            "display_annotation": None,
            "display_score": "5",
            "scale_ref": "ordinal_6",
        }
        sr = ScoreRepresentation.from_dict(d)
        assert sr.canonical_score == 5
        assert sr.display_annotation is None


# ── Immutability Tests ────────────────────────────────────────────────────────


class TestScoreRepresentationImmutability:
    def test_frozen_canonical_score(self):
        sr = ScoreRepresentation(
            canonical_score=4,
            display_annotation=None,
            display_score="4",
            scale_ref="ordinal_6",
        )
        with pytest.raises(Exception):
            sr.canonical_score = 5  # type: ignore

    def test_frozen_display_score(self):
        sr = ScoreRepresentation(
            canonical_score=4,
            display_annotation=None,
            display_score="4",
            scale_ref="ordinal_6",
        )
        with pytest.raises(Exception):
            sr.display_score = "5"  # type: ignore

    def test_frozen_scale_ref(self):
        sr = ScoreRepresentation(
            canonical_score=4,
            display_annotation=None,
            display_score="4",
            scale_ref="ordinal_6",
        )
        with pytest.raises(Exception):
            sr.scale_ref = "other_scale"  # type: ignore


# ── Factory Tests ─────────────────────────────────────────────────────────────


class TestCreateScoreRepresentationFactory:
    def test_without_annotation_produces_plain_display(self):
        sr = create_score_representation(canonical_score=5, scale_ref="ordinal_6")
        assert sr.canonical_score == 5
        assert sr.display_annotation is None
        assert sr.display_score == "5"
        assert sr.scale_ref == "ordinal_6"

    def test_with_minus_annotation(self):
        sr = create_score_representation(
            canonical_score=4, scale_ref="ordinal_6", display_annotation="-"
        )
        assert sr.canonical_score == 4
        assert sr.display_annotation == "-"
        assert sr.display_score == "4-"

    def test_factory_produces_frozen_object(self):
        sr = create_score_representation(canonical_score=3, scale_ref="ordinal_6")
        with pytest.raises(Exception):
            sr.canonical_score = 4  # type: ignore

    def test_factory_returns_score_representation_instance(self):
        sr = create_score_representation(canonical_score=3, scale_ref="ordinal_6")
        assert isinstance(sr, ScoreRepresentation)

    def test_any_integer_score_accepted(self):
        """Score range is NOT hardcoded; it is defined by scale config."""
        for score in [1, 5, 10, 100]:
            sr = create_score_representation(canonical_score=score, scale_ref="custom_scale")
            assert sr.canonical_score == score
            assert sr.display_score == str(score)

    def test_custom_annotation_string(self):
        """Any annotation string accepted; allowed values from config, not hardcoded."""
        for annotation in ["-", "+", "~", "*", "low", "high"]:
            sr = create_score_representation(
                canonical_score=3, scale_ref="ordinal_6", display_annotation=annotation
            )
            assert sr.display_annotation == annotation
            assert sr.display_score == f"3{annotation}"


# ── Zero-Hardcoding Tests ─────────────────────────────────────────────────────


class TestScoreRepresentationNoHardcoding:
    """Verify the contract structure contains no hardcoded business facts."""

    def test_arbitrary_scale_ref_accepted(self):
        """scale_ref is just a string key; validation against actual scales is done at config layer."""
        sr = create_score_representation(
            canonical_score=3, scale_ref="any_future_scale_id_xyz"
        )
        assert sr.scale_ref == "any_future_scale_id_xyz"

    def test_non_standard_score_range_accepted(self):
        """Contract does NOT enforce scale min/max; that comes from rubric config."""
        sr = create_score_representation(canonical_score=7, scale_ref="ordinal_8")
        assert sr.canonical_score == 7

    def test_non_standard_annotation_accepted(self):
        """Contract does NOT restrict annotation to '-' only; allowed set from config."""
        sr = create_score_representation(
            canonical_score=4, scale_ref="ordinal_6", display_annotation="*"
        )
        assert sr.display_annotation == "*"
