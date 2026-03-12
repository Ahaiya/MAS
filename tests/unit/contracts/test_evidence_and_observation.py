"""
Tests for evidence and observation contracts (Phase 2, Task 2).

Covers:
- EvidenceSpan: creation, offset validation, source linkage, serialization
- FacetFinding: facet-level evidence reference (opaque facet IDs from config)
- DimensionObservation: aggregated evidence, supporting/counter evidence,
  facet findings, observation confidence
- Roundtrip to_dict() / from_dict() for all models
- Strict deserialization: reject extra/undeclared fields
- Zero-hardcoding: all dimension IDs, facet IDs, evidence scopes are opaque strings

No trait names, dimension codes, scale values, or facet names are hardcoded here.
"""

import pytest

from src.contracts.evidence import (
    EvidenceSpan,
    EvidenceScope,
    FacetFinding,
    DimensionObservation,
    ObservationConfidence,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def span_a():
    return EvidenceSpan(
        span_id="span-001",
        document_id="doc-001",
        unit_id="unit-001",
        text_quote="The quick brown fox jumps over the lazy dog.",
        start_offset=0,
        end_offset=44,
        scope=EvidenceScope.SPAN,
        dimension_id="dim_alpha",
        facet_ids=["facet_x", "facet_y"],
        extraction_note=None,
    )


@pytest.fixture
def span_b():
    return EvidenceSpan(
        span_id="span-002",
        document_id="doc-001",
        unit_id="unit-001",
        text_quote="It was a bright day.",
        start_offset=45,
        end_offset=65,
        scope=EvidenceScope.SPAN,
        dimension_id="dim_alpha",
        facet_ids=["facet_x"],
        extraction_note="counter-evidence candidate",
    )


@pytest.fixture
def facet_finding(span_a):
    return FacetFinding(
        facet_id="facet_x",
        supporting_span_ids=["span-001"],
        counter_span_ids=[],
        finding_note="facet_x is well-supported",
    )


@pytest.fixture
def observation(span_a, span_b, facet_finding):
    return DimensionObservation(
        observation_id="obs-001",
        document_id="doc-001",
        dimension_id="dim_alpha",
        supporting_span_ids=["span-001"],
        counter_span_ids=["span-002"],
        facet_findings=[facet_finding],
        observation_confidence=ObservationConfidence.HIGH,
        uncertainty_notes=[],
    )


# ── EvidenceScope enum ─────────────────────────────────────────────────────────


class TestEvidenceScope:
    def test_span_and_global_exist(self):
        assert EvidenceScope.SPAN is not None
        assert EvidenceScope.GLOBAL is not None

    def test_roundtrip_value(self):
        assert EvidenceScope("span") == EvidenceScope.SPAN
        assert EvidenceScope("global") == EvidenceScope.GLOBAL


# ── EvidenceSpan ───────────────────────────────────────────────────────────────


class TestEvidenceSpan:
    def test_creation(self, span_a):
        assert span_a.span_id == "span-001"
        assert span_a.start_offset == 0
        assert span_a.end_offset == 44
        assert span_a.scope == EvidenceScope.SPAN
        assert "facet_x" in span_a.facet_ids
        # dimension_id is opaque — no hardcoded trait name
        assert isinstance(span_a.dimension_id, str)

    def test_global_scope_no_offsets(self):
        """A global-scope span may omit character offsets (document-level)."""
        gs = EvidenceSpan(
            span_id="span-g",
            document_id="doc-001",
            unit_id=None,
            text_quote=None,
            start_offset=None,
            end_offset=None,
            scope=EvidenceScope.GLOBAL,
            dimension_id="dim_beta",
            facet_ids=["facet_z"],
            extraction_note="overall impression",
        )
        assert gs.scope == EvidenceScope.GLOBAL
        assert gs.start_offset is None
        assert gs.unit_id is None

    def test_span_scope_requires_offsets(self):
        """A span-scope evidence must have offsets."""
        with pytest.raises((ValueError, TypeError)):
            EvidenceSpan(
                span_id="bad",
                document_id="doc-001",
                unit_id="unit-001",
                text_quote="some text",
                start_offset=None,  # invalid for SPAN scope
                end_offset=10,
                scope=EvidenceScope.SPAN,
                dimension_id="dim_alpha",
                facet_ids=[],
                extraction_note=None,
            )

    def test_invalid_offsets_rejected(self):
        with pytest.raises((ValueError, TypeError)):
            EvidenceSpan(
                span_id="bad",
                document_id="doc-001",
                unit_id="unit-001",
                text_quote="some text",
                start_offset=20,
                end_offset=10,  # end < start
                scope=EvidenceScope.SPAN,
                dimension_id="dim_alpha",
                facet_ids=[],
                extraction_note=None,
            )

    def test_immutable(self, span_a):
        with pytest.raises((AttributeError, TypeError)):
            span_a.span_id = "mutated"  # type: ignore[misc]

    def test_roundtrip_serialization(self, span_a):
        restored = EvidenceSpan.from_dict(span_a.to_dict())
        assert restored == span_a

    def test_roundtrip_global_scope(self):
        gs = EvidenceSpan(
            span_id="span-g",
            document_id="doc-001",
            unit_id=None,
            text_quote=None,
            start_offset=None,
            end_offset=None,
            scope=EvidenceScope.GLOBAL,
            dimension_id="dim_beta",
            facet_ids=["facet_z"],
            extraction_note="global note",
        )
        restored = EvidenceSpan.from_dict(gs.to_dict())
        assert restored == gs

    def test_from_dict_rejects_extra_fields(self, span_a):
        d = span_a.to_dict()
        d["unknown"] = "bad"
        with pytest.raises((TypeError, ValueError)):
            EvidenceSpan.from_dict(d)

    def test_span_length(self, span_a):
        assert span_a.span_length() == 44


# ── FacetFinding ───────────────────────────────────────────────────────────────


class TestFacetFinding:
    def test_creation(self, facet_finding):
        assert facet_finding.facet_id == "facet_x"
        assert "span-001" in facet_finding.supporting_span_ids
        assert facet_finding.counter_span_ids == []

    def test_facet_id_is_opaque(self, facet_finding):
        """facet_id is an opaque string from rubric config — not a hardcoded name."""
        assert isinstance(facet_finding.facet_id, str)

    def test_immutable(self, facet_finding):
        with pytest.raises((AttributeError, TypeError)):
            facet_finding.facet_id = "mutated"  # type: ignore[misc]

    def test_roundtrip_serialization(self, facet_finding):
        restored = FacetFinding.from_dict(facet_finding.to_dict())
        assert restored == facet_finding

    def test_from_dict_rejects_extra_fields(self, facet_finding):
        d = facet_finding.to_dict()
        d["extra"] = "bad"
        with pytest.raises((TypeError, ValueError)):
            FacetFinding.from_dict(d)


# ── ObservationConfidence enum ─────────────────────────────────────────────────


class TestObservationConfidence:
    def test_levels_exist(self):
        assert ObservationConfidence.HIGH is not None
        assert ObservationConfidence.MEDIUM is not None
        assert ObservationConfidence.LOW is not None

    def test_roundtrip_value(self):
        assert ObservationConfidence("high") == ObservationConfidence.HIGH


# ── DimensionObservation ───────────────────────────────────────────────────────


class TestDimensionObservation:
    def test_creation(self, observation):
        assert observation.observation_id == "obs-001"
        assert observation.dimension_id == "dim_alpha"
        assert len(observation.supporting_span_ids) == 1
        assert len(observation.counter_span_ids) == 1
        assert len(observation.facet_findings) == 1
        assert observation.observation_confidence == ObservationConfidence.HIGH

    def test_dimension_id_is_opaque(self, observation):
        assert isinstance(observation.dimension_id, str)

    def test_immutable(self, observation):
        with pytest.raises((AttributeError, TypeError)):
            observation.observation_id = "mutated"  # type: ignore[misc]

    def test_roundtrip_serialization(self, observation):
        restored = DimensionObservation.from_dict(observation.to_dict())
        assert restored == observation

    def test_from_dict_rejects_extra_fields(self, observation):
        d = observation.to_dict()
        d["injected"] = "bad"
        with pytest.raises((TypeError, ValueError)):
            DimensionObservation.from_dict(d)

    def test_get_facet_finding(self, observation):
        found = observation.get_facet_finding("facet_x")
        assert found is not None
        assert found.facet_id == "facet_x"

    def test_get_facet_finding_missing(self, observation):
        assert observation.get_facet_finding("nonexistent") is None

    def test_all_span_ids(self, observation):
        """all_span_ids() should return union of supporting and counter."""
        all_ids = observation.all_span_ids()
        assert "span-001" in all_ids
        assert "span-002" in all_ids

    def test_no_uncertainty_notes_defaults_empty(self):
        obs = DimensionObservation(
            observation_id="obs-002",
            document_id="doc-001",
            dimension_id="dim_beta",
            supporting_span_ids=["span-001"],
            counter_span_ids=[],
            facet_findings=[],
            observation_confidence=ObservationConfidence.MEDIUM,
            uncertainty_notes=[],
        )
        assert obs.uncertainty_notes == []
