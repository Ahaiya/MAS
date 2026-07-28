"""
聚合策略：一级指标分 = 各观测点 final_score 按量规 `weight` 的加权平均。

单一路径，纯计算无 LLM。`FinalDecision.final_score` 无论 source 是 consensus 还是
adjudicated，都已经是该观测点唯一的权威值——不需要再区分"是否发生了裁决"去选
公式变体，"""

from __future__ import annotations

from typing import List, Mapping

from src.contracts.scoring import FinalDecision


def aggregate_final_decisions(
    decisions: List[FinalDecision], weights: Mapping[str, float]
) -> float:
    """纯函数：一级指标分 = 各观测点 final_score 按 `weight` 的加权平均。

        权重来自量规（`dimensions[].weight`），由 `rubric_validation` 保证必填且
        全量和为 1.0。但这里仍按**实际参与聚合的观测点**的权重和归一化：某个观测点
        评价失败时不会有 FinalDecision（见 engine 的 failed_dims），剩余权重和不
        再是 1.0，不归一化就会让 dim 分数凭空变低。

        Args:
            decisions: 一个一级指标下各观测点的 FinalDecision（每个观测点一条）。
            weights:   {dimension_id: weight}，须覆盖 decisions 里的全部观测点。

        Returns:
            各 final_score.canonical_score 的加权平均值。

        Raises:
            ValueError: decisions 为空。
            KeyError:   某个 dimension_id 不在 weights 中（量规与决策对不上）。"""
    if not decisions:
        raise ValueError("aggregate_final_decisions: decisions 不能为空")
    total_weight = sum(weights[d.dimension_id] for d in decisions)
    weighted = sum(weights[d.dimension_id] * d.final_score.canonical_score for d in decisions)
    return weighted / total_weight
