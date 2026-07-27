"""
聚合策略模块，负责按配置计算 composite 总分。

聚合策略 — 基于配置驱动的 composite 分数计算。

从 PolicySnapshot 配置评估聚合公式。
支持的聚合方法：

- average_per_trait_then_weighted_sum: 对于每个维度，对配置的 source_raters 的评分求平均，然后乘以配置的权重。在未发生第三评分者裁决时使用。
- average_per_trait_then_weighted_average: 同上，但除以总参与权重，使最终分数保持在原始量表上。

- direct_weighted_sum: 对每个维度直接使用 FinalDimensionDecision.final_score，乘以配置的权重。在应用了第三评分者裁决时使用。
- direct_weighted_average: 同上，但通过总参与权重进行归一化。

变体选择：
  - 如果任何 AdjudicationRecord 的 is_resolved=True → "resolution_used" 变体。
  - 否则 → "resolution_not_used" 变体。

所有维度权重、评分者 ID 和变体条件均从 PolicySnapshot.aggregation_policy 读取 — 这里没有任何硬编码。"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from src.contracts.artifact_bundle import PolicySnapshot
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import (
    AdjudicationRecord,
    CompositeDecision,
    FinalDecision,
    FinalDimensionDecision,
    ScoreHypothesis,
)


def _hid(seed: str, length: int = 12) -> str:
    return hashlib.md5(seed.encode()).hexdigest()[:length]


def _round_half_up(value: float) -> int:
    return int(value + 0.5)


def _is_resolution_used(adjudications: List[AdjudicationRecord]) -> bool:
    """如果任何裁决记录被成功解决，则返回 True。"""
    return any(a.is_resolved for a in adjudications)


def _select_variant(
    variants: List[Dict[str, Any]], resolution_used: bool
) -> Optional[Dict[str, Any]]:
    """根据裁决状态选择适当的公式变体。"""
    applies_when = "resolution_used" if resolution_used else "resolution_not_used"
    for v in variants:
        if v.get("applies_when") == applies_when:
            return v
    return None


def _compute_average_then_weighted(
    hypotheses: List[ScoreHypothesis],
    source_raters: List[str],
    weights: Dict[str, int],
) -> tuple[float, List[str], float]:
    """使用每个维度中各评分者的平均值计算加权和。
    
        返回 (weighted_total, contributing_dim_ids, contributing_weight_total)。"""
    by_dim: Dict[str, Dict[str, int]] = {}
    for h in hypotheses:
        if h.rater_id in source_raters:
            by_dim.setdefault(h.dimension_id, {})[h.rater_id] = h.score.canonical_score

    total = 0.0
    contributing: List[str] = []
    contributing_weight_total = 0.0

    for dim_id, rater_scores in sorted(by_dim.items()):
        w = weights.get(dim_id, 0)
        if w == 0:
            continue
        scores = [rater_scores[r] for r in source_raters if r in rater_scores]
        if not scores:
            continue
        avg = sum(scores) / len(scores)
        total += avg * w
        contributing.append(dim_id)
        contributing_weight_total += w

    return total, contributing, contributing_weight_total


def _compute_direct_weighted(
    decisions: List[FinalDimensionDecision],
    weights: Dict[str, int],
) -> tuple[float, List[str], float]:
    """直接使用 final_score.canonical_score 计算加权和。
    
        返回 (weighted_total, contributing_dim_ids, contributing_weight_total)。"""
    total = 0.0
    contributing: List[str] = []
    contributing_weight_total = 0.0

    for decision in sorted(decisions, key=lambda d: d.dimension_id):
        w = weights.get(decision.dimension_id, 0)
        if w == 0:
            continue
        total += decision.final_score.canonical_score * w
        contributing.append(decision.dimension_id)
        contributing_weight_total += w

    return total, contributing, contributing_weight_total


def compute_composite(
    decisions: List[FinalDimensionDecision],
    hypotheses: List[ScoreHypothesis],
    adjudications: List[AdjudicationRecord],
    policy: PolicySnapshot,
    policy_ref: str = "",
) -> Optional[CompositeDecision]:
    """从配置驱动的公式计算可选的 CompositeDecision。
    
        从 policy.aggregation_policy 读取 composite_formula，根据是否使用了裁决选择适当的变体，计算加权汇总分数，并返回 CompositeDecision。
    
        如果策略中未定义 composite_formula，则返回 None。
    
        Args:
            decisions: FinalDimensionDecision 列表（每个维度一个）。
            hypotheses: ScoreHypothesis 列表（所有评分者，所有维度）。
            adjudications: AdjudicationRecord 列表，用于检查裁决状态。
            policy: 包含聚合策略配置的 PolicySnapshot。
            policy_ref: 可选的 URI 引用，指向聚合策略产物。
    
        Returns:
            CompositeDecision，如果未配置聚合则为 None。"""
    agg = policy.aggregation_policy
    variants = agg.get("composite_formula", [])
    if not variants:
        return None

    resolution_used = _is_resolution_used(adjudications)
    variant = _select_variant(variants, resolution_used)
    if variant is None:
        return None

    method = variant.get("aggregation_method", "")
    source_raters: List[str] = variant.get("source_raters", [])

    raw_weights = variant.get("weights", {})
    if raw_weights == "auto_equal":
        # 在运行时根据实际存在的维度推导出相等的权重
        # 数据中，因此策略文件不需要枚举维度代码。
        if "direct" in method:
            auto_dim_ids: List[str] = [d.dimension_id for d in decisions]
        else:
            auto_dim_ids = list({
                h.dimension_id for h in hypotheses if h.rater_id in source_raters
            })
        weights: Dict[str, int] = {dim_id: 1 for dim_id in auto_dim_ids}
    else:
        weights = {k: int(v) for k, v in raw_weights.items()}

    if method == "average_per_trait_then_weighted_sum":
        total, contributing_dims, weight_total = _compute_average_then_weighted(
            hypotheses, source_raters, weights
        )
        composite_score_val = _round_half_up(total)
    elif method == "average_per_trait_then_weighted_average":
        total, contributing_dims, weight_total = _compute_average_then_weighted(
            hypotheses, source_raters, weights
        )
        if weight_total <= 0:
            return None
        composite_score_val = _round_half_up(total / weight_total)
    elif method == "direct_weighted_sum":
        total, contributing_dims, weight_total = _compute_direct_weighted(decisions, weights)
        composite_score_val = _round_half_up(total)
    elif method == "direct_weighted_average":
        total, contributing_dims, weight_total = _compute_direct_weighted(decisions, weights)
        if weight_total <= 0:
            return None
        composite_score_val = _round_half_up(total / weight_total)
    else:
        return None

    # 根据参与计算的维度确定贡献的决策 ID
    decision_by_dim = {d.dimension_id: d for d in decisions}
    contributing_decision_ids = [
        decision_by_dim[dim_id].decision_id
        for dim_id in contributing_dims
        if dim_id in decision_by_dim
    ]

    # 从策略构建 scale_ref；回退至 policy_id
    policy_id = agg.get("policy_id", "aggregation")
    composite_id = f"composite-{_hid(policy_id + str(composite_score_val))}"

    # 使用合成的 composite 量表引用（不是 rubric 量表 — composite 有其自身的范围）
    composite_score = create_score_representation(
        canonical_score=composite_score_val,
        scale_ref=f"composite:{policy_id}",
    )

    return CompositeDecision(
        composite_id=composite_id,
        aggregation_policy_ref=policy_ref,
        contributing_decision_ids=contributing_decision_ids,
        composite_score=composite_score,
        aggregation_detail={
            "variant_id": variant.get("variant_id", ""),
            "aggregation_method": method,
            "source_raters": source_raters,
            "weights": weights,
            "weight_total": weight_total,
            "resolution_used": resolution_used,
        },
        composite_note=None,
    )


# ═══════════════════════════════════════════════════════════════════════════
# v2 —— 单一聚合路径，与上方 v1 with/without-resolution 变体机制并存。
#
# FinalDecision.final_score 无论 source 是 consensus 还是 adjudicated，都已经
# 是该二级指标唯一的权威值——不再需要区分"是否发生了裁决"去选公式变体。
# 聚合因此只有一条路径：auto_equal 等权平均，不可配置、不读 policy。
# ═══════════════════════════════════════════════════════════════════════════


def aggregate_final_decisions(decisions: List[FinalDecision]) -> float:
    """纯函数：一级指标分 = 各二级指标 final_score 的等权平均（auto_equal）。

        Args:
            decisions: 一个一级指标下各二级指标的 FinalDecision（每个二级指标一条）。

        Returns:
            各 final_score.canonical_score 的算术平均值。"""
    if not decisions:
        raise ValueError("aggregate_final_decisions: decisions 不能为空")
    return sum(d.final_score.canonical_score for d in decisions) / len(decisions)
