"""
Tests for request normalization and text segmentation contracts (Phase 2, Task 1).

Covers:
- EvaluationRequest: schema validation, required fields, optional fields
- NormalizedRequest: creation and serialization
- NormalizedDocument: creation with TextUnit list
- TextUnit: offset tracking, roundtrip serialization
- CoveragePlan: config-driven (no hardcoded dimension IDs, facets, or counts)
- Roundtrip to_dict() / from_dict() for all models
- Strict deserialization: reject extra/undeclared fields
- Zero-hardcoding: dimension IDs, facets, evidence counts all come from outside

No business facts (trait names, scale ranges, dimension IDs like 'I'/'O'/'V')
are hardcoded in these tests. All such values are treated as opaque strings
supplied by the caller.
"""

import pytest
from datetime import datetime, timezone

from src.contracts.request_models import (
    EvaluationRequest,
    NormalizedRequest,
    NormalizedDocument,
    TextUnit,
    CoveragePlan,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_text():
    return "The quick brown fox jumps over the lazy dog. It was a bright day."


@pytest.fixture
def text_unit(sample_text):
    return TextUnit(
        unit_id="unit-001",
        document_id="doc-001",
        text=sample_text[:44],
        start_offset=0,
        end_offset=44,
        unit_type="sentence",
        sequence_index=0,
    )


@pytest.fixture
def normalized_doc(sample_text, text_unit):
    return NormalizedDocument(
        document_id="doc-001",
        request_id="req-001",
        normalized_text=sample_text,
        text_units=[text_unit],
        char_count=len(sample_text),
        word_count=14,
        document_metadata={},
    )


@pytest.fixture
def coverage_plan():
    # dimension_id is an opaque string from rubric config — NOT hardcoded here
    return CoveragePlan(
        plan_id="plan-001",
        document_id="doc-001",
        dimension_id="dim_alpha",
        target_unit_ids=["unit-001", "unit-002"],
        required_facets=["facet_x", "facet_y"],
        minimum_evidence_units=2,
        allowed_evidence_scopes=["span", "global"],
        coverage_strategy="full_scan",
    )


# ── EvaluationRequest ──────────────────────────────────────────────────────────


class TestEvaluationRequest:
    def test_minimal_creation(self, sample_text):
        req = EvaluationRequest(
            raw_text=sample_text,
            bundle_ref="bundle://test_bundle/v1",
        )
        assert req.raw_text == sample_text
        assert req.bundle_ref == "bundle://test_bundle/v1"
        assert req.request_id is None
        assert req.metadata == {}

    def test_full_creation(self, sample_text):
        req = EvaluationRequest(
            raw_text=sample_text,
            bundle_ref="bundle://test_bundle/v1",
            request_id="req-42",
            metadata={"source": "test", "essay_id": "20716"},
        )
        assert req.request_id == "req-42"
        assert req.metadata["essay_id"] == "20716"

    def test_immutable(self, sample_text):
        req = EvaluationRequest(
            raw_text=sample_text,
            bundle_ref="bundle://test_bundle/v1",
        )
        with pytest.raises((AttributeError, TypeError)):
            req.raw_text = "mutated"  # type: ignore[misc]

    def test_roundtrip_serialization(self, sample_text):
        req = EvaluationRequest(
            raw_text=sample_text,
            bundle_ref="bundle://test_bundle/v1",
            request_id="req-001",
            metadata={"k": "v"},
        )
        d = req.to_dict()
        restored = EvaluationRequest.from_dict(d)
        assert restored == req

    def test_from_dict_rejects_extra_fields(self, sample_text):
        d = {
            "raw_text": sample_text,
            "bundle_ref": "bundle://test_bundle/v1",
            "unknown_field": "should_fail",
        }
        with pytest.raises((TypeError, ValueError)):
            EvaluationRequest.from_dict(d)


# ── NormalizedRequest ──────────────────────────────────────────────────────────


class TestNormalizedRequest:
    def test_creation(self, sample_text):
        ts = datetime(2026, 3, 12, 10, 0, 0, tzinfo=timezone.utc)
        nr = NormalizedRequest(
            request_id="req-001",
            raw_text=sample_text,
            bundle_ref="bundle://test_bundle/v1",
            normalized_at=ts,
            normalization_notes=["stripped_trailing_whitespace"],
            metadata={},
        )
        assert nr.request_id == "req-001"
        assert len(nr.normalization_notes) == 1

    def test_immutable(self, sample_text):
        ts = datetime(2026, 3, 12, 10, 0, 0, tzinfo=timezone.utc)
        nr = NormalizedRequest(
            request_id="req-001",
            raw_text=sample_text,
            bundle_ref="bundle://test_bundle/v1",
            normalized_at=ts,
            normalization_notes=[],
            metadata={},
        )
        with pytest.raises((AttributeError, TypeError)):
            nr.request_id = "mutated"  # type: ignore[misc]

    def test_roundtrip_serialization(self, sample_text):
        ts = datetime(2026, 3, 12, 10, 0, 0, tzinfo=timezone.utc)
        nr = NormalizedRequest(
            request_id="req-001",
            raw_text=sample_text,
            bundle_ref="bundle://test_bundle/v1",
            normalized_at=ts,
            normalization_notes=["norm_a", "norm_b"],
            metadata={"key": "val"},
        )
        restored = NormalizedRequest.from_dict(nr.to_dict())
        assert restored == nr

    def test_from_dict_rejects_extra_fields(self, sample_text):
        ts = datetime(2026, 3, 12, 10, 0, 0, tzinfo=timezone.utc)
        nr = NormalizedRequest(
            request_id="req-001",
            raw_text=sample_text,
            bundle_ref="bundle://test_bundle/v1",
            normalized_at=ts,
            normalization_notes=[],
            metadata={},
        )
        d = nr.to_dict()
        d["extra_key"] = "bad"
        with pytest.raises((TypeError, ValueError)):
            NormalizedRequest.from_dict(d)


# ── TextUnit ───────────────────────────────────────────────────────────────────


class TestTextUnit:
    def test_creation(self, text_unit):
        assert text_unit.unit_id == "unit-001"
        assert text_unit.start_offset == 0
        assert text_unit.end_offset == 44
        assert text_unit.sequence_index == 0

    def test_offset_consistency(self, text_unit):
        # end_offset must be > start_offset
        assert text_unit.end_offset > text_unit.start_offset

    def test_invalid_offsets_rejected(self):
        with pytest.raises((ValueError, TypeError)):
            TextUnit(
                unit_id="bad",
                document_id="doc-001",
                text="hello",
                start_offset=10,
                end_offset=5,  # end < start — invalid
                unit_type="sentence",
                sequence_index=0,
            )

    def test_immutable(self, text_unit):
        with pytest.raises((AttributeError, TypeError)):
            text_unit.text = "mutated"  # type: ignore[misc]

    def test_roundtrip_serialization(self, text_unit):
        restored = TextUnit.from_dict(text_unit.to_dict())
        assert restored == text_unit

    def test_from_dict_rejects_extra_fields(self, text_unit):
        d = text_unit.to_dict()
        d["surprise"] = "should_fail"
        with pytest.raises((TypeError, ValueError)):
            TextUnit.from_dict(d)

    def test_span_length(self, text_unit):
        assert text_unit.span_length() == text_unit.end_offset - text_unit.start_offset


# ── NormalizedDocument ─────────────────────────────────────────────────────────


class TestNormalizedDocument:
    def test_creation(self, normalized_doc, sample_text):
        assert normalized_doc.document_id == "doc-001"
        assert normalized_doc.char_count == len(sample_text)
        assert len(normalized_doc.text_units) == 1

    def test_immutable(self, normalized_doc):
        with pytest.raises((AttributeError, TypeError)):
            normalized_doc.document_id = "mutated"  # type: ignore[misc]

    def test_roundtrip_serialization(self, normalized_doc):
        restored = NormalizedDocument.from_dict(normalized_doc.to_dict())
        assert restored == normalized_doc

    def test_from_dict_rejects_extra_fields(self, normalized_doc):
        d = normalized_doc.to_dict()
        d["injected"] = "bad"
        with pytest.raises((TypeError, ValueError)):
            NormalizedDocument.from_dict(d)

    def test_get_unit_by_id(self, normalized_doc, text_unit):
        found = normalized_doc.get_unit("unit-001")
        assert found == text_unit

    def test_get_unit_missing_returns_none(self, normalized_doc):
        assert normalized_doc.get_unit("nonexistent") is None


# ── CoveragePlan ───────────────────────────────────────────────────────────────


class TestCoveragePlan:
    def test_creation(self, coverage_plan):
        assert coverage_plan.plan_id == "plan-001"
        assert coverage_plan.minimum_evidence_units == 2
        # dimension_id is opaque — we never assert it equals a known trait name
        assert isinstance(coverage_plan.dimension_id, str)
        assert len(coverage_plan.required_facets) == 2

    def test_dimension_id_is_opaque_string(self, coverage_plan):
        """Verify dimension_id is just a string — no hardcoded trait knowledge here."""
        assert isinstance(coverage_plan.dimension_id, str)
        assert len(coverage_plan.dimension_id) > 0

    def test_immutable(self, coverage_plan):
        with pytest.raises((AttributeError, TypeError)):
            coverage_plan.dimension_id = "mutated"  # type: ignore[misc]

    def test_roundtrip_serialization(self, coverage_plan):
        restored = CoveragePlan.from_dict(coverage_plan.to_dict())
        assert restored == coverage_plan

    def test_from_dict_rejects_extra_fields(self, coverage_plan):
        d = coverage_plan.to_dict()
        d["hidden_field"] = "forbidden"
        with pytest.raises((TypeError, ValueError)):
            CoveragePlan.from_dict(d)

    def test_different_dimension_ids_are_independent(self):
        """Two plans with different dimension IDs must be independent objects."""
        plan_a = CoveragePlan(
            plan_id="plan-a",
            document_id="doc-001",
            dimension_id="dim_alpha",
            target_unit_ids=[],
            required_facets=["f1"],
            minimum_evidence_units=1,
            allowed_evidence_scopes=["span"],
            coverage_strategy="full_scan",
        )
        plan_b = CoveragePlan(
            plan_id="plan-b",
            document_id="doc-001",
            dimension_id="dim_beta",
            target_unit_ids=[],
            required_facets=["f2"],
            minimum_evidence_units=1,
            allowed_evidence_scopes=["span"],
            coverage_strategy="full_scan",
        )
        assert plan_a.dimension_id != plan_b.dimension_id
        assert plan_a != plan_b
