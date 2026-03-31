"""
分数表示契约，明确区分 canonical score 与展示层分数字段。

Score Representation Contract

Defines the explicit separation of canonical (integer) score from display form.

Key principle: the canonical_score is the authoritative integer used for ALL
computation — QWK, aggregation formulas, adjudication trigger evaluation.
The display_annotation is a purely cosmetic modifier that NEVER affects
computation. Rules for when and how annotations are applied live in
configs/ display overlay config, NOT in this contract.

Invariant:  display_score == str(canonical_score) + (display_annotation or "")

No score scale ranges, trait names, annotation values, or formula details
are hardcoded here. All such data flows through rubric config artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class ScoreRepresentation:
    """Canonical integer score with optional display annotation.

    Attributes:
        canonical_score: Integer score used for all computation.
                         Valid range is defined by scale_ref config, not this class.
        display_annotation: Optional cosmetic modifier (e.g., "-").
                            Allowed values come from display overlay config.
                            None means no annotation; display is the plain integer.
        display_score: Rendered display string.
                       Invariant: display_score == str(canonical_score) + (display_annotation or "")
        scale_ref: Reference to the scale definition in the rubric config artifact.
                   Must match a scale_id in the loaded RubricSnapshot.
    """

    canonical_score: int
    display_annotation: Optional[str]
    display_score: str
    scale_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_score, int):
            raise TypeError(
                f"canonical_score must be int, got {type(self.canonical_score).__name__}"
            )
        expected_display = str(self.canonical_score) + (self.display_annotation or "")
        if self.display_score != expected_display:
            raise ValueError(
                f"display_score '{self.display_score}' is inconsistent with "
                f"canonical_score={self.canonical_score} and "
                f"display_annotation={self.display_annotation!r}. "
                f"Expected '{expected_display}'."
            )

    def is_annotated(self) -> bool:
        """Return True if this score carries a display annotation."""
        return self.display_annotation is not None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict (JSON-safe)."""
        return {
            "canonical_score": self.canonical_score,
            "display_annotation": self.display_annotation,
            "display_score": self.display_score,
            "scale_ref": self.scale_ref,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ScoreRepresentation:
        """Deserialize from a plain dict produced by to_dict()."""
        return cls(
            canonical_score=data["canonical_score"],
            display_annotation=data.get("display_annotation"),
            display_score=data["display_score"],
            scale_ref=data["scale_ref"],
        )


def create_score_representation(
    canonical_score: int,
    scale_ref: str,
    display_annotation: Optional[str] = None,
) -> ScoreRepresentation:
    """Factory: create a ScoreRepresentation with auto-computed display_score.

    Computes display_score = str(canonical_score) + (display_annotation or ""),
    ensuring the invariant is always satisfied at creation time.

    Args:
        canonical_score: Integer score for computation.
        scale_ref: Reference to the rubric scale (e.g., "ordinal_4", "ordinal_10").
        display_annotation: Optional display modifier (e.g., "-").
                            None produces a plain integer display.

    Returns:
        A new, frozen ScoreRepresentation.
    """
    display_score = str(canonical_score) + (display_annotation or "")
    return ScoreRepresentation(
        canonical_score=canonical_score,
        display_annotation=display_annotation,
        display_score=display_score,
        scale_ref=scale_ref,
    )
