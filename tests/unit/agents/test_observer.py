"""Unit tests for observer support split and coverage diagnostics (Stage J)."""

from __future__ import annotations

from src.agents import observer
from src.contracts.evidence import EvidenceScope, EvidenceSpan, ObservationConfidence
from src.contracts.request_models import CoveragePlan


def _plan(*, facets: list[str], targets: list[str]) -> CoveragePlan:
    return CoveragePlan(
        plan_id="plan-observer-1",
        document_id="doc-1",
        dimension_id="dim-1",
        target_unit_ids=targets,
        required_facets=facets,
        minimum_evidence_units=1,
        allowed_evidence_scopes=["span", "global"],
        coverage_strategy="targeted",
    )


def _span(
    span_id: str,
    *,
    facet_ids: list[str],
    support_type: str = "supporting",
    unit_id: str | None = "u1",
) -> EvidenceSpan:
    return EvidenceSpan(
        span_id=span_id,
        document_id="doc-1",
        unit_id=unit_id,
        text_quote="sample quote",
        start_offset=0,
        end_offset=12,
        scope=EvidenceScope.SPAN if unit_id is not None else EvidenceScope.GLOBAL,
        dimension_id="dim-1",
        facet_ids=facet_ids,
        extraction_note="test",
        support_type=support_type,
    )


def test_supporting_counter_split():
    plan = _plan(facets=["f1"], targets=["u1"])
    spans = [
        _span("s1", facet_ids=["f1"], support_type="supporting"),
        _span("s2", facet_ids=["f1"], support_type="neutral"),
        _span("s3", facet_ids=["f1"], support_type="counter"),
    ]

    obs = observer.run(spans, plan)
    ff = obs.get_facet_finding("f1")
    assert ff is not None
    assert ff.supporting_span_ids == ["s1", "s2"]
    assert ff.counter_span_ids == ["s3"]
    assert obs.supporting_span_ids == ["s1", "s2"]
    assert obs.counter_span_ids == ["s3"]


def test_neutral_treated_as_supporting():
    plan = _plan(facets=["f1"], targets=["u1"])
    spans = [_span("s-neutral", facet_ids=["f1"], support_type="neutral")]

    obs = observer.run(spans, plan)
    ff = obs.get_facet_finding("f1")
    assert ff is not None
    assert ff.supporting_span_ids == ["s-neutral"]
    assert ff.counter_span_ids == []


def test_coverage_miss_computed():
    plan = _plan(facets=["f1"], targets=["u1"])
    spans = [
        _span("s-in", facet_ids=["f1"], support_type="supporting", unit_id="u1"),
        _span("s-out", facet_ids=["f1"], support_type="counter", unit_id="u2"),
    ]

    obs = observer.run(spans, plan)
    assert obs.coverage_miss_span_ids == ["s-out"]


def test_uncertainty_notes_for_missing_facet():
    plan = _plan(facets=["f1", "f2"], targets=["u1"])
    spans = [_span("s1", facet_ids=["f1"], support_type="supporting")]

    obs = observer.run(spans, plan)
    assert any("facet 'f2' has no evidence" in note for note in obs.uncertainty_notes)
    assert obs.observation_confidence == ObservationConfidence.MEDIUM


def test_low_confidence_appends_summary_note():
    plan = _plan(facets=["f1"], targets=["u1"])
    obs = observer.run([], plan)

    assert obs.observation_confidence == ObservationConfidence.LOW
    assert "observation has significant coverage gaps" in obs.uncertainty_notes


def test_backward_compat_no_support_type():
    legacy = EvidenceSpan.from_dict(
        {
            "span_id": "legacy-1",
            "document_id": "doc-1",
            "unit_id": "u1",
            "text_quote": "legacy quote",
            "start_offset": 0,
            "end_offset": 11,
            "scope": "span",
            "dimension_id": "dim-1",
            "facet_ids": ["f1"],
            "extraction_note": "legacy",
        }
    )
    assert legacy.support_type == "supporting"
    obs = observer.run([legacy], _plan(facets=["f1"], targets=["u1"]))
    assert obs.supporting_span_ids == ["legacy-1"]
    assert obs.counter_span_ids == []

