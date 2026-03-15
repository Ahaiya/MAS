"""Quadratic Weighted Kappa (QWK) computation for dimension-level scoring.

Provides:
  - qwk(y_true, y_pred, min_score, max_score) -> float
  - QWKResult dataclass for structured output

This module is purely computational — no I/O, no LLM calls, no config loading.
Inputs are lists of integer scores; min/max bounds come from the caller (config-driven).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QWKResult:
    """Result of a QWK computation for a single scoring dimension."""

    dimension_id: str
    qwk: float
    n_samples: int
    min_score: int
    max_score: int
    y_true: tuple[int, ...]
    y_pred: tuple[int, ...]

    def to_dict(self) -> dict:
        return {
            "dimension_id": self.dimension_id,
            "qwk": self.qwk,
            "n_samples": self.n_samples,
            "min_score": self.min_score,
            "max_score": self.max_score,
        }


def qwk(
    y_true: list[int],
    y_pred: list[int],
    min_score: int,
    max_score: int,
) -> float:
    """Compute Quadratic Weighted Kappa between two integer score lists.

    Args:
        y_true: Ground-truth scores (list of ints in [min_score, max_score]).
        y_pred: Predicted scores (list of ints in [min_score, max_score]).
        min_score: Minimum possible score (inclusive).
        max_score: Maximum possible score (inclusive).

    Returns:
        QWK coefficient in [-1.0, 1.0].  Returns 0.0 for trivial inputs.

    Raises:
        ValueError: If inputs have different lengths, are empty, or scores are
                    outside [min_score, max_score].
    """
    if len(y_true) != len(y_pred):
        raise ValueError(
            f"y_true and y_pred must have the same length, "
            f"got {len(y_true)} and {len(y_pred)}"
        )
    if len(y_true) == 0:
        raise ValueError("Score lists must not be empty")
    if min_score >= max_score:
        raise ValueError(f"min_score ({min_score}) must be < max_score ({max_score})")

    for label, scores in (("y_true", y_true), ("y_pred", y_pred)):
        for s in scores:
            if not (min_score <= s <= max_score):
                raise ValueError(
                    f"{label} contains out-of-range score {s} "
                    f"(expected [{min_score}, {max_score}])"
                )

    n_classes = max_score - min_score + 1
    n = len(y_true)

    # Build confusion matrix
    conf = [[0.0] * n_classes for _ in range(n_classes)]
    for t, p in zip(y_true, y_pred):
        conf[t - min_score][p - min_score] += 1.0

    # Build weight matrix (quadratic)
    denom_w = (n_classes - 1) ** 2
    weights = [
        [(i - j) ** 2 / denom_w for j in range(n_classes)]
        for i in range(n_classes)
    ]

    # Row and column marginals
    hist_true = [sum(conf[i][j] for j in range(n_classes)) for i in range(n_classes)]
    hist_pred = [sum(conf[i][j] for i in range(n_classes)) for j in range(n_classes)]

    # Expected matrix (outer product / n)
    expected = [
        [hist_true[i] * hist_pred[j] / n for j in range(n_classes)]
        for i in range(n_classes)
    ]

    numerator = sum(
        weights[i][j] * conf[i][j]
        for i in range(n_classes)
        for j in range(n_classes)
    )
    denominator = sum(
        weights[i][j] * expected[i][j]
        for i in range(n_classes)
        for j in range(n_classes)
    )

    if denominator == 0.0:
        return 0.0

    return float(1.0 - numerator / denominator)


def qwk_for_dimension(
    dimension_id: str,
    y_true: list[int],
    y_pred: list[int],
    min_score: int,
    max_score: int,
) -> QWKResult:
    """Compute QWK for a single scoring dimension and return a structured result."""
    score = qwk(y_true, y_pred, min_score, max_score)
    return QWKResult(
        dimension_id=dimension_id,
        qwk=score,
        n_samples=len(y_true),
        min_score=min_score,
        max_score=max_score,
        y_true=tuple(y_true),
        y_pred=tuple(y_pred),
    )
