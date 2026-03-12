"""
Evidence and Observation Contracts

Defines the intermediate data shapes for the evidence extraction and
observation building stages of the evaluation pipeline:

  TextUnit[] + CoveragePlan[] -> EvidenceSpan[]
  EvidenceSpan[]              -> DimensionObservation[]

Design invariants:
- All models are frozen (immutable) dataclasses.
- dimension_id and facet_ids are opaque strings from rubric config artifacts.
  No trait names, codes, or scale values are hardcoded here.
- EvidenceScope distinguishes char-level span evidence from document-global evidence.
- from_dict() rejects unknown keys (strict schema enforcement).
- SPAN-scope spans must carry valid character offsets; GLOBAL-scope spans need not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ── Strict deserialization helper ──────────────────────────────────────────────


def _check_no_extra(data: Dict[str, Any], allowed: frozenset, cls_name: str) -> None:
    extra = set(data) - allowed
    if extra:
        raise TypeError(
            f"{cls_name}.from_dict() received unexpected fields: {sorted(extra)}"
        )


# ── EvidenceScope ──────────────────────────────────────────────────────────────


class EvidenceScope(str, Enum):
    """Whether an evidence span is anchored to a character range or document-global."""

    SPAN = "span"
    GLOBAL = "global"


# ── ObservationConfidence ──────────────────────────────────────────────────────


class ObservationConfidence(str, Enum):
    """Confidence level of a DimensionObservation.

    HIGH   – all required facets covered with strong evidence.
    MEDIUM – most facets covered; some gaps or weak evidence.
    LOW    – significant coverage gaps; adjudication or re-extract recommended.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ── EvidenceSpan ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvidenceSpan:
    """A single piece of textual evidence extracted from a document.

    Extraction subagents produce EvidenceSpan objects; they are the atomic
    evidence units referenced by DimensionObservation, ScoreHypothesis,
    AdjudicationRecord, and the Feedback assembler.

    Attributes:
        span_id: Unique identifier for this evidence span within a run.
        document_id: Parent NormalizedDocument reference.
        unit_id: Parent TextUnit reference. None for GLOBAL-scope spans.
        text_quote: Verbatim text excerpt. None for GLOBAL-scope spans.
        start_offset: Inclusive char offset in normalized_text (SPAN only).
        end_offset: Exclusive char offset in normalized_text (SPAN only).
        scope: SPAN for char-anchored evidence; GLOBAL for document-level.
        dimension_id: Opaque dimension identifier from rubric config.
        facet_ids: Rubric facets this span is relevant to (opaque IDs from config).
        extraction_note: Optional extraction-time comment (e.g., uncertainty flag).
    """

    span_id: str
    document_id: str
    unit_id: Optional[str]
    text_quote: Optional[str]
    start_offset: Optional[int]
    end_offset: Optional[int]
    scope: EvidenceScope
    dimension_id: str
    facet_ids: List[str]
    extraction_note: Optional[str]

    def __post_init__(self) -> None:
        if self.scope == EvidenceScope.SPAN:
            if self.start_offset is None or self.end_offset is None:
                raise ValueError(
                    f"EvidenceSpan '{self.span_id}': SPAN-scope spans must have "
                    f"start_offset and end_offset set."
                )
            if self.end_offset <= self.start_offset:
                raise ValueError(
                    f"EvidenceSpan '{self.span_id}': end_offset ({self.end_offset}) "
                    f"must be > start_offset ({self.start_offset})."
                )

    def span_length(self) -> Optional[int]:
        """Character length of this span, or None for GLOBAL-scope spans."""
        if self.start_offset is not None and self.end_offset is not None:
            return self.end_offset - self.start_offset
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "span_id": self.span_id,
            "document_id": self.document_id,
            "unit_id": self.unit_id,
            "text_quote": self.text_quote,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "scope": self.scope.value,
            "dimension_id": self.dimension_id,
            "facet_ids": list(self.facet_ids),
            "extraction_note": self.extraction_note,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EvidenceSpan:
        _check_no_extra(
            data,
            frozenset({
                "span_id", "document_id", "unit_id", "text_quote",
                "start_offset", "end_offset", "scope", "dimension_id",
                "facet_ids", "extraction_note",
            }),
            "EvidenceSpan",
        )
        return cls(
            span_id=data["span_id"],
            document_id=data["document_id"],
            unit_id=data.get("unit_id"),
            text_quote=data.get("text_quote"),
            start_offset=data.get("start_offset"),
            end_offset=data.get("end_offset"),
            scope=EvidenceScope(data["scope"]),
            dimension_id=data["dimension_id"],
            facet_ids=list(data.get("facet_ids") or []),
            extraction_note=data.get("extraction_note"),
        )


# ── FacetFinding ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FacetFinding:
    """Evidence reference summary for a single rubric facet within an observation.

    The Observation Builder groups EvidenceSpan IDs by the facet they address,
    producing one FacetFinding per required facet in the dimension's
    observation_schema.required_facets list.

    Attributes:
        facet_id: Opaque facet identifier from rubric config (e.g., from
                  dimension.observation_schema.required_facets).
        supporting_span_ids: Span IDs that positively support this facet.
        counter_span_ids: Span IDs that contradict or weaken this facet.
        finding_note: Optional human/agent annotation about this facet's status.
    """

    facet_id: str
    supporting_span_ids: List[str]
    counter_span_ids: List[str]
    finding_note: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "facet_id": self.facet_id,
            "supporting_span_ids": list(self.supporting_span_ids),
            "counter_span_ids": list(self.counter_span_ids),
            "finding_note": self.finding_note,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FacetFinding:
        _check_no_extra(
            data,
            frozenset({"facet_id", "supporting_span_ids", "counter_span_ids",
                       "finding_note"}),
            "FacetFinding",
        )
        return cls(
            facet_id=data["facet_id"],
            supporting_span_ids=list(data.get("supporting_span_ids") or []),
            counter_span_ids=list(data.get("counter_span_ids") or []),
            finding_note=data.get("finding_note"),
        )


# ── DimensionObservation ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class DimensionObservation:
    """Structured observation for a single rubric dimension.

    The Observation Builder produces one DimensionObservation per dimension
    per evaluation run. It aggregates EvidenceSpans into supporting/counter
    lists, organizes them by facet, and records overall observation confidence.

    Scoring subagents receive DimensionObservation (not raw EvidenceSpans) to
    ensure score rationale is anchored to organized evidence.

    Attributes:
        observation_id: Unique ID for this observation.
        document_id: Parent NormalizedDocument reference.
        dimension_id: Opaque dimension identifier from rubric config.
        supporting_span_ids: Span IDs that support a positive assessment.
        counter_span_ids: Span IDs that represent weaknesses or contradictions.
        facet_findings: Per-facet evidence breakdowns (one per required facet).
        observation_confidence: Overall confidence in this observation's completeness.
        uncertainty_notes: Free-text notes about coverage gaps or ambiguities.
    """

    observation_id: str
    document_id: str
    dimension_id: str
    supporting_span_ids: List[str]
    counter_span_ids: List[str]
    facet_findings: List[FacetFinding]
    observation_confidence: ObservationConfidence
    uncertainty_notes: List[str]

    def get_facet_finding(self, facet_id: str) -> Optional[FacetFinding]:
        """Return the FacetFinding for the given facet_id, or None."""
        for ff in self.facet_findings:
            if ff.facet_id == facet_id:
                return ff
        return None

    def all_span_ids(self) -> List[str]:
        """Return the union of supporting and counter span IDs (deduplicated)."""
        seen = set()
        result = []
        for sid in list(self.supporting_span_ids) + list(self.counter_span_ids):
            if sid not in seen:
                seen.add(sid)
                result.append(sid)
        return result

    def to_dict(self) -> Dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "document_id": self.document_id,
            "dimension_id": self.dimension_id,
            "supporting_span_ids": list(self.supporting_span_ids),
            "counter_span_ids": list(self.counter_span_ids),
            "facet_findings": [ff.to_dict() for ff in self.facet_findings],
            "observation_confidence": self.observation_confidence.value,
            "uncertainty_notes": list(self.uncertainty_notes),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DimensionObservation:
        _check_no_extra(
            data,
            frozenset({
                "observation_id", "document_id", "dimension_id",
                "supporting_span_ids", "counter_span_ids", "facet_findings",
                "observation_confidence", "uncertainty_notes",
            }),
            "DimensionObservation",
        )
        return cls(
            observation_id=data["observation_id"],
            document_id=data["document_id"],
            dimension_id=data["dimension_id"],
            supporting_span_ids=list(data.get("supporting_span_ids") or []),
            counter_span_ids=list(data.get("counter_span_ids") or []),
            facet_findings=[
                FacetFinding.from_dict(ff) for ff in (data.get("facet_findings") or [])
            ],
            observation_confidence=ObservationConfidence(data["observation_confidence"]),
            uncertainty_notes=list(data.get("uncertainty_notes") or []),
        )
