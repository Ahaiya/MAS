"""
双链比较：一致直接决策（source=consensus），分歧触发 Rater3 仲裁（source=adjudicated）。

"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from src.agents import adjudicator
from src.contracts.artifact_bundle import PolicySnapshot, RubricSnapshot
from src.contracts.package import DataPackage
from src.contracts.scoring import FinalDecision, RaterChainResult, ScoreSource
from src.policies.adjudication import needs_adjudication
from src.providers.base import BaseProvider
from src.providers.prompt_loader import PromptTemplate


def _by_dimension(chains: Sequence[RaterChainResult]) -> Dict[str, RaterChainResult]:
    return {c.dimension_id: c for c in chains}


def reconcile(
    package: DataPackage,
    chains_a: Sequence[RaterChainResult],
    chains_b: Sequence[RaterChainResult],
    rubric: RubricSnapshot,
    policy: PolicySnapshot,
    rater_3_provider: Optional[BaseProvider] = None,
    adjudication_template: Optional[PromptTemplate] = None,
) -> List[FinalDecision]:
    """比较两条 Rater 链，逐二级指标产出唯一 FinalDecision。

    未触发仲裁规则的维度 → source=consensus，取 chains_a 的分数为一致值。
    触发的维度 → source=adjudicated，调用 Rater3；缺 provider/模板直接报错。

    Args:
        package: 两条链共同引用的 DataPackage（Rater3 仲裁时需要看完整原文）。
        chains_a: 一条 Rater 链在各二级指标上的 RaterChainResult。
        chains_b: 另一条 Rater 链在各二级指标上的 RaterChainResult。
        rubric  : 用于 dimension/scale 查找的 RubricSnapshot。
        policy  : 带两个仲裁触发阈值的 PolicySnapshot。
        rater_3_provider     : Rater3 用的 provider；有分歧时才需要。
        adjudication_template: 已加载的 adjudication PromptTemplate；有分歧时才需要。

    Returns:
        按 dimension_id 排序的 FinalDecision 列表，每个二级指标恰好一条。"""
    by_a = _by_dimension(chains_a)
    by_b = _by_dimension(chains_b)
    dims_a, dims_b = set(by_a), set(by_b)
    if dims_a != dims_b:
        raise ValueError(
            "两条 Rater 链覆盖的二级指标不一致："
            f"only_in_a={sorted(dims_a - dims_b)}, only_in_b={sorted(dims_b - dims_a)}"
        )

    triggered = needs_adjudication(list(chains_a), list(chains_b), policy)

    decisions: List[FinalDecision] = []
    for dim_id in sorted(dims_a):
        chain_a = by_a[dim_id]
        chain_b = by_b[dim_id]

        if dim_id not in triggered:
            # "一致" = 未触发仲裁规则，不要求两分完全相等（分差<=1 且非同向漂移组
            # 也算一致）。不相等时确定性地取 chains_a 的值——不平均、不取高分，
            # 那两条路径已随 v1 的 average/highest 兜底一起删除。
            decisions.append(
                FinalDecision(
                    dimension_id=dim_id,
                    final_score=chain_a.score.score,
                    source=ScoreSource.CONSENSUS,
                    unit_ids=list(chain_a.score.supporting_unit_ids),
                )
            )
            continue

        if rater_3_provider is None or adjudication_template is None:
            raise ValueError(
                f"dimension '{dim_id}' 触发仲裁但缺少 rater_3 provider/adjudication 模板"
                "——分歧一律走 Rater3，不静默降级。"
            )

        dimension = rubric.get_dimension(dim_id)
        if dimension is None:
            raise ValueError(f"Dimension '{dim_id}' not found in rubric")

        adjudicated_score = adjudicator.adjudicate(
            package,
            dimension,
            rubric,
            chain_a,
            chain_b,
            rater_3_provider,
            adjudication_template,
        )
        decisions.append(
            FinalDecision(
                dimension_id=dim_id,
                final_score=adjudicated_score.score,
                source=ScoreSource.ADJUDICATED,
                unit_ids=list(adjudicated_score.supporting_unit_ids),
            )
        )

    return decisions
