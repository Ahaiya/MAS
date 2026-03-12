"""
Request Normalization and Text Segmentation Contracts

Defines the data shapes for the evaluation request pipeline:
  EvaluationRequest -> NormalizedRequest -> NormalizedDocument (with TextUnit[])
  NormalizedDocument + RubricSnapshot -> CoveragePlan[]

Design invariants:
- All models are frozen (immutable) dataclasses.
- No dimension names, trait codes, scale ranges, or facet names are hardcoded here.
  All such values are opaque strings that flow in from rubric config artifacts.
- from_dict() is strict: unknown keys raise TypeError to catch schema drift early.
- to_dict() produces plain JSON-safe dicts; datetime is serialized as ISO 8601.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ── Strict deserialization helper ──────────────────────────────────────────────

_SENTINEL = object()

_KNOWN_FIELDS: Dict[str, frozenset] = {}  # populated by each class


def _check_no_extra(data: Dict[str, Any], allowed: frozenset, cls_name: str) -> None:
    extra = set(data) - allowed
    if extra:
        raise TypeError(
            f"{cls_name}.from_dict() received unexpected fields: {sorted(extra)}"
        )


# ── TextUnit ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TextUnit:
    """A contiguous slice of text within a NormalizedDocument.

    Offsets are character-level, 0-indexed, half-open [start_offset, end_offset).

    Attributes:
        unit_id: Unique identifier within the document.
        document_id: Parent document reference.
        text: The actual text content of this unit.
        start_offset: Inclusive start character offset in the full document text.
        end_offset: Exclusive end character offset.
        unit_type: Semantic type — e.g., "sentence", "paragraph", "full_document".
        sequence_index: Zero-based position of this unit in document order.
    """

    unit_id: str
    document_id: str
    text: str
    start_offset: int
    end_offset: int
    unit_type: str
    sequence_index: int

    def __post_init__(self) -> None:
        if self.end_offset <= self.start_offset:
            raise ValueError(
                f"TextUnit '{self.unit_id}': end_offset ({self.end_offset}) "
                f"must be > start_offset ({self.start_offset})"
            )

    def span_length(self) -> int:
        """Number of characters covered by this unit."""
        return self.end_offset - self.start_offset

    def to_dict(self) -> Dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "document_id": self.document_id,
            "text": self.text,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "unit_type": self.unit_type,
            "sequence_index": self.sequence_index,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TextUnit:
        _check_no_extra(
            data,
            frozenset({"unit_id", "document_id", "text", "start_offset",
                       "end_offset", "unit_type", "sequence_index"}),
            "TextUnit",
        )
        return cls(
            unit_id=data["unit_id"],
            document_id=data["document_id"],
            text=data["text"],
            start_offset=data["start_offset"],
            end_offset=data["end_offset"],
            unit_type=data["unit_type"],
            sequence_index=data["sequence_index"],
        )


# ── EvaluationRequest ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvaluationRequest:
    """Raw inbound evaluation request — the system boundary entry point.

    Attributes:
        raw_text: The student essay or text to evaluate. Must be non-empty.
        bundle_ref: URI-style reference to the ArtifactBundle to use.
        request_id: Caller-supplied ID. None means the pipeline will generate one.
        metadata: Arbitrary caller metadata (e.g., essay_id, source, session_id).
    """

    raw_text: str
    bundle_ref: str
    request_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_text": self.raw_text,
            "bundle_ref": self.bundle_ref,
            "request_id": self.request_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EvaluationRequest:
        _check_no_extra(
            data,
            frozenset({"raw_text", "bundle_ref", "request_id", "metadata"}),
            "EvaluationRequest",
        )
        return cls(
            raw_text=data["raw_text"],
            bundle_ref=data["bundle_ref"],
            request_id=data.get("request_id"),
            metadata=data.get("metadata") or {},
        )


# ── NormalizedRequest ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NormalizedRequest:
    """An EvaluationRequest after input normalization.

    The preprocessing node converts EvaluationRequest -> NormalizedRequest,
    recording what transformations were applied so the run is auditable.

    Attributes:
        request_id: Authoritative ID (generated by pipeline if not in raw request).
        raw_text: Original text, preserved verbatim for audit trail.
        bundle_ref: Forwarded from EvaluationRequest.
        normalized_at: UTC timestamp of normalization.
        normalization_notes: List of applied normalization steps (e.g., "strip_bom").
        metadata: Forwarded caller metadata plus any pipeline-added fields.
    """

    request_id: str
    raw_text: str
    bundle_ref: str
    normalized_at: datetime
    normalization_notes: List[str]
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "raw_text": self.raw_text,
            "bundle_ref": self.bundle_ref,
            "normalized_at": self.normalized_at.isoformat(),
            "normalization_notes": list(self.normalization_notes),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> NormalizedRequest:
        _check_no_extra(
            data,
            frozenset({"request_id", "raw_text", "bundle_ref", "normalized_at",
                       "normalization_notes", "metadata"}),
            "NormalizedRequest",
        )
        ts_raw = data["normalized_at"]
        if isinstance(ts_raw, str):
            ts = datetime.fromisoformat(ts_raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts_raw
        return cls(
            request_id=data["request_id"],
            raw_text=data["raw_text"],
            bundle_ref=data["bundle_ref"],
            normalized_at=ts,
            normalization_notes=list(data.get("normalization_notes") or []),
            metadata=dict(data.get("metadata") or {}),
        )


# ── NormalizedDocument ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NormalizedDocument:
    """A normalized, segmented document ready for evidence extraction.

    Produced by the text preprocessing node from a NormalizedRequest.

    Attributes:
        document_id: Unique document identifier (may equal request_id).
        request_id: Links back to the originating NormalizedRequest.
        normalized_text: Full normalized text (canonical form used for all offsets).
        text_units: Ordered list of TextUnit slices (sentences, paragraphs, etc.).
        char_count: Total character count of normalized_text.
        word_count: Approximate word count (whitespace-tokenized).
        document_metadata: Pipeline-generated document metadata.
    """

    document_id: str
    request_id: str
    normalized_text: str
    text_units: List[TextUnit]
    char_count: int
    word_count: int
    document_metadata: Dict[str, Any]

    def get_unit(self, unit_id: str) -> Optional[TextUnit]:
        """Look up a TextUnit by its unit_id. Returns None if not found."""
        for u in self.text_units:
            if u.unit_id == unit_id:
                return u
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "request_id": self.request_id,
            "normalized_text": self.normalized_text,
            "text_units": [u.to_dict() for u in self.text_units],
            "char_count": self.char_count,
            "word_count": self.word_count,
            "document_metadata": dict(self.document_metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> NormalizedDocument:
        _check_no_extra(
            data,
            frozenset({"document_id", "request_id", "normalized_text", "text_units",
                       "char_count", "word_count", "document_metadata"}),
            "NormalizedDocument",
        )
        return cls(
            document_id=data["document_id"],
            request_id=data["request_id"],
            normalized_text=data["normalized_text"],
            text_units=[TextUnit.from_dict(u) for u in data.get("text_units", [])],
            char_count=data["char_count"],
            word_count=data["word_count"],
            document_metadata=dict(data.get("document_metadata") or {}),
        )


# ── CoveragePlan ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CoveragePlan:
    """A per-dimension extraction plan produced by the Coverage Planner.

    Specifies which text units to search, which facets to satisfy, and
    how many evidence units are required — all values sourced from the
    rubric config artifact, never hardcoded.

    Attributes:
        plan_id: Unique identifier for this plan.
        document_id: The NormalizedDocument this plan applies to.
        dimension_id: Opaque dimension identifier from rubric config.
                      Must NOT be assumed to equal any hardcoded trait name.
        target_unit_ids: TextUnit IDs that extraction workers should scan.
        required_facets: Facet IDs the observation must cover (from rubric config).
        minimum_evidence_units: Min evidence spans required (from rubric config).
        allowed_evidence_scopes: Valid scope types (e.g., ["span", "global"]).
        coverage_strategy: Extraction strategy hint (e.g., "full_scan", "targeted").
    """

    plan_id: str
    document_id: str
    dimension_id: str
    target_unit_ids: List[str]
    required_facets: List[str]
    minimum_evidence_units: int
    allowed_evidence_scopes: List[str]
    coverage_strategy: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "document_id": self.document_id,
            "dimension_id": self.dimension_id,
            "target_unit_ids": list(self.target_unit_ids),
            "required_facets": list(self.required_facets),
            "minimum_evidence_units": self.minimum_evidence_units,
            "allowed_evidence_scopes": list(self.allowed_evidence_scopes),
            "coverage_strategy": self.coverage_strategy,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CoveragePlan:
        _check_no_extra(
            data,
            frozenset({"plan_id", "document_id", "dimension_id", "target_unit_ids",
                       "required_facets", "minimum_evidence_units",
                       "allowed_evidence_scopes", "coverage_strategy"}),
            "CoveragePlan",
        )
        return cls(
            plan_id=data["plan_id"],
            document_id=data["document_id"],
            dimension_id=data["dimension_id"],
            target_unit_ids=list(data.get("target_unit_ids") or []),
            required_facets=list(data.get("required_facets") or []),
            minimum_evidence_units=data["minimum_evidence_units"],
            allowed_evidence_scopes=list(data.get("allowed_evidence_scopes") or []),
            coverage_strategy=data["coverage_strategy"],
        )
