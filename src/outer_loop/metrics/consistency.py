"""外环一致性指标模块，负责计算多评分者之间的分歧与一致性统计。

这部分逻辑不参与内环主流水线执行，只消费运行产物或评分假设，
用于外环分析“评分者之间是否稳定”“冲突是否系统化”等问题。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DimensionConsistency:
    """Consistency metrics for one scoring dimension across multiple hypotheses."""

    dimension_id: str
    scores: tuple[int, ...]
    mean: float
    variance: float
    spread: int
    conflict_count: int
    agreement_ratio: float

    def to_dict(self) -> dict:
        return {
            "dimension_id": self.dimension_id,
            "scores": list(self.scores),
            "mean": self.mean,
            "variance": self.variance,
            "spread": self.spread,
            "conflict_count": self.conflict_count,
            "agreement_ratio": self.agreement_ratio,
        }

    @property
    def has_conflict(self) -> bool:
        return self.conflict_count > 0


@dataclass(frozen=True)
class ConsistencyReport:
    """Full consistency report across all dimensions in a run."""

    run_id: str
    dimensions: tuple[DimensionConsistency, ...]
    total_conflict_count: int
    dimensions_with_conflict: int
    overall_agreement_ratio: float

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "total_conflict_count": self.total_conflict_count,
            "dimensions_with_conflict": self.dimensions_with_conflict,
            "overall_agreement_ratio": self.overall_agreement_ratio,
            "dimensions": [d.to_dict() for d in self.dimensions],
        }


def _compute_dimension_consistency(
    dimension_id: str, scores: list[int]
) -> DimensionConsistency:
    """Compute consistency metrics for a single dimension's score list."""
    if not scores:
        return DimensionConsistency(
            dimension_id=dimension_id,
            scores=(),
            mean=0.0,
            variance=0.0,
            spread=0,
            conflict_count=0,
            agreement_ratio=1.0,
        )

    n = len(scores)
    mean = sum(scores) / n
    variance = sum((s - mean) ** 2 for s in scores) / n
    spread = max(scores) - min(scores)

    conflict_count = 0
    total_pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            total_pairs += 1
            if abs(scores[i] - scores[j]) > 1:
                conflict_count += 1

    agreement_ratio = (
        (total_pairs - conflict_count) / total_pairs if total_pairs > 0 else 1.0
    )

    return DimensionConsistency(
        dimension_id=dimension_id,
        scores=tuple(scores),
        mean=mean,
        variance=variance,
        spread=spread,
        conflict_count=conflict_count,
        agreement_ratio=agreement_ratio,
    )


def compute_consistency(
    run_id: str,
    hypotheses_by_dimension: dict[str, list[int]],
) -> ConsistencyReport:
    """Compute inter-agent consistency metrics from score hypotheses."""
    dim_results = [
        _compute_dimension_consistency(dim_id, scores)
        for dim_id, scores in hypotheses_by_dimension.items()
    ]

    total_conflicts = sum(d.conflict_count for d in dim_results)
    dims_with_conflict = sum(1 for d in dim_results if d.has_conflict)

    all_ratios = [d.agreement_ratio for d in dim_results]
    overall_ratio = sum(all_ratios) / len(all_ratios) if all_ratios else 1.0

    return ConsistencyReport(
        run_id=run_id,
        dimensions=tuple(dim_results),
        total_conflict_count=total_conflicts,
        dimensions_with_conflict=dims_with_conflict,
        overall_agreement_ratio=overall_ratio,
    )


def extract_hypotheses_from_trace(trace: dict) -> dict[str, list[int]]:
    """Extract score hypotheses grouped by dimension from a run_trace dict."""
    result: dict[str, list[int]] = {}
    scorer_node = next(
        (n for n in trace.get("node_traces", []) if n["node_id"] == "node_scorer"),
        None,
    )
    if scorer_node is None:
        return result
    return result


__all__ = [
    "ConsistencyReport",
    "DimensionConsistency",
    "compute_consistency",
    "extract_hypotheses_from_trace",
]

