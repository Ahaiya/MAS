"""
流水线校验器，负责在关键阶段前后检查 contract 不变量。

流水线校验器 —— 阶段前/阶段后的不变量检查。

每个校验器都是一个纯函数，当预期的不变量被违反时，会抛出 ValueError。所有检查都是 contract 驱动的（没有硬编码的 trait 名称、dimension 代码或分数阈值）。

校验器由 PipelineRunner 在阶段之间调用，以便尽早捕获缺失的输出，避免它们静默破坏下游阶段。"""

from __future__ import annotations

from typing import List

from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.evidence import DimensionObservation
from src.contracts.request_models import CoveragePlan
from src.contracts.scoring import FinalDimensionDecision, ScoreHypothesis


def validate_observations(
    observations: List[DimensionObservation],
    plans: List[CoveragePlan],
) -> None:
    """验证每个 CoveragePlan 都有对应的 DimensionObservation。
    
        Raises:
            ValueError: 如果任何计划内的 dimension 缺少 observation。"""
    obs_dims = {o.dimension_id for o in observations}
    plan_dims = {p.dimension_id for p in plans}
    missing = plan_dims - obs_dims
    if missing:
        raise ValueError(
            f"Missing observations for dimensions: {sorted(missing)}"
        )


def validate_hypotheses(
    hypotheses: List[ScoreHypothesis],
    plans: List[CoveragePlan],
    rater_ids: List[str],
) -> None:
    """验证每个 (dimension_id, rater_id) 组合都有一个 ScoreHypothesis。
    
        Raises:
            ValueError: 如果缺少任何必需的 (dimension, rater) 对。"""
    plan_dims = {p.dimension_id for p in plans}
    scored_pairs = {(h.dimension_id, h.rater_id) for h in hypotheses}
    missing = [
        (dim_id, rater_id)
        for dim_id in sorted(plan_dims)
        for rater_id in rater_ids
        if (dim_id, rater_id) not in scored_pairs
    ]
    if missing:
        raise ValueError(
            f"Missing hypotheses for (dimension_id, rater_id) pairs: {missing}"
        )


def validate_final_decisions(
    decisions: List[FinalDimensionDecision],
    plans: List[CoveragePlan],
) -> None:
    """验证每个计划内的 dimension 都有一个 FinalDimensionDecision。
    
        Raises:
            ValueError: 如果任何计划内的 dimension 缺少最终决策。"""
    dec_dims = {d.dimension_id for d in decisions}
    plan_dims = {p.dimension_id for p in plans}
    missing = plan_dims - dec_dims
    if missing:
        raise ValueError(
            f"Missing final decisions for dimensions: {sorted(missing)}"
        )


def terminal_validation(
    decisions: List[FinalDimensionDecision],
    plans: List[CoveragePlan],
    rubric: RubricSnapshot,
) -> bool:
    """检查所有最终决策是否存在，并且分数在有效的量表范围内。
    
        量表范围从 RubricSnapshot 读取（配置驱动，零硬编码）。
    
        Returns:
            如果所有 dimension 都有有效的范围内分数，则返回 True；否则返回 False。"""
    plan_dims = {p.dimension_id for p in plans}
    dec_by_dim = {d.dimension_id: d for d in decisions}

    for dim_id in plan_dims:
        if dim_id not in dec_by_dim:
            return False
        decision = dec_by_dim[dim_id]
        if not rubric.validate_score(dim_id, decision.final_score.canonical_score):
            return False

    return True
