"""外环 QWK 指标模块，负责计算维度级评分与总分对齐度。"""

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
    """Compute Quadratic Weighted Kappa between two integer score lists."""
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
        for score in scores:
            if not (min_score <= score <= max_score):
                raise ValueError(
                    f"{label} contains out-of-range score {score} "
                    f"(expected [{min_score}, {max_score}])"
                )

    n_classes = max_score - min_score + 1
    n = len(y_true)

    conf = [[0.0] * n_classes for _ in range(n_classes)]
    for true_score, pred_score in zip(y_true, y_pred):
        conf[true_score - min_score][pred_score - min_score] += 1.0

    denom_w = (n_classes - 1) ** 2
    weights = [
        [(i - j) ** 2 / denom_w for j in range(n_classes)]
        for i in range(n_classes)
    ]

    hist_true = [sum(conf[i][j] for j in range(n_classes)) for i in range(n_classes)]
    hist_pred = [sum(conf[i][j] for i in range(n_classes)) for j in range(n_classes)]

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


__all__ = ["QWKResult", "qwk", "qwk_for_dimension"]

