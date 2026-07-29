"""
裁决触发判断：双链分数比较，纯计算无 LLM。

两条规则，阈值全部来自 configs/adjudication.yaml：

  1. 分差过大  —— 任意观测点上两位评委分差 > score_gap_threshold；
  2. 同向漂移  —— 分差恰为 1 且方向一致的观测点数 ≥ drift_min_dimensions。

"相邻"（差 1）与"同向"都是规则定义的一部分，写死在这里而不是配置里：差 ≥2 早被
规则 1 单独触发，把"相邻"做成旋钮转到 2 以上永远无效；而"方向一致"正是"系统性
漂移"区别于"零散分歧"的地方，改成 false 它就退化成规则 1 的重复。"""

from __future__ import annotations

from typing import Dict, List

from src.contracts.artifact_bundle import PolicySnapshot
from src.contracts.scoring import RaterChainResult

# 规则 2 只看"相邻"分歧：分差恰为此值的观测点才计入同向漂移的统计。
_ADJACENT_GAP = 1


def _drift_triggered_dims(
    scores_a: Dict[str, int], scores_b: Dict[str, int], min_dimensions: int
) -> set[str]:
    """分差恰为 1 且同向的观测点数达标时，返回这些观测点；否则空集。

    两个方向分别统计——一边偏高 2 个、另一边偏低 2 个，不构成"整体错位"。"""
    if min_dimensions <= 0:
        return set()

    by_direction: Dict[int, set[str]] = {-1: set(), 1: set()}
    for dim_id in sorted(set(scores_a) & set(scores_b)):
        signed_diff = scores_b[dim_id] - scores_a[dim_id]
        if abs(signed_diff) == _ADJACENT_GAP:
            by_direction[1 if signed_diff > 0 else -1].add(dim_id)

    return {
        dim_id
        for dims in by_direction.values()
        if len(dims) >= min_dimensions
        for dim_id in dims
    }


def needs_adjudication(
    chains_a: List[RaterChainResult],
    chains_b: List[RaterChainResult],
    policy: PolicySnapshot,
) -> set[str]:
    """纯函数：比较两条 Rater 链的分数，返回需要 Rater3 仲裁的观测点集合。

        Args:
            chains_a: rater_1（或任一方）在各观测点上的 RaterChainResult。
            chains_b: rater_2（另一方）在各观测点上的 RaterChainResult。
            policy  : 带两个触发阈值的 PolicySnapshot。

        Returns:
            触发仲裁的观测点标识符集合；未触发的视为一致（consensus）。"""
    scores_a = {c.dimension_id: c.score.score for c in chains_a}
    scores_b = {c.dimension_id: c.score.score for c in chains_b}

    gap_triggered = {
        dim_id
        for dim_id in set(scores_a) & set(scores_b)
        if abs(scores_a[dim_id] - scores_b[dim_id]) > policy.score_gap_threshold
    }
    return gap_triggered | _drift_triggered_dims(scores_a, scores_b, policy.drift_min_dimensions)
