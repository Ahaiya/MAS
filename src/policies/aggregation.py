"""
聚合策略：一级指标分 = 各二级指标 final_score 的等权平均。

单一路径，纯计算无 LLM。`FinalDecision.final_score` 无论 source 是 consensus 还是
adjudicated，都已经是该二级指标唯一的权威值——不需要再区分"是否发生了裁决"去选
公式变体，因此 v1 的 with/without-resolution 变体机制（compute_composite +
composite_formula 配置）整套删除，聚合不可配置、不读 policy。"""

from __future__ import annotations

from typing import List

from src.contracts.scoring import FinalDecision


def aggregate_final_decisions(decisions: List[FinalDecision]) -> float:
    """纯函数：一级指标分 = 各二级指标 final_score 的等权平均（auto_equal）。

        Args:
            decisions: 一个一级指标下各二级指标的 FinalDecision（每个二级指标一条）。

        Returns:
            各 final_score.canonical_score 的算术平均值。"""
    if not decisions:
        raise ValueError("aggregate_final_decisions: decisions 不能为空")
    return sum(d.final_score.canonical_score for d in decisions) / len(decisions)
