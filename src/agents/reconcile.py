"""
双链比较：一致直接决策（source=consensus），分歧触发 Rater3 仲裁（source=adjudicated）。

"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from src.agents import adjudicator
from src.contracts.configuration import PolicySnapshot, RubricSnapshot
from src.contracts.package import DataPackage
from src.contracts.scoring import FinalDecision, RaterChainResult, ScoreSource
from src.policies.adjudication import needs_adjudication
from src.providers.base import BaseProvider
from src.providers.prompt_loader import PromptTemplate


def _by_code(chains: Sequence[RaterChainResult]) -> Dict[str, RaterChainResult]:
    return {c.code: c for c in chains}


def reconcile(
    package: DataPackage,
    chains_a: Sequence[RaterChainResult],
    chains_b: Sequence[RaterChainResult],
    rubric: RubricSnapshot,
    policy: PolicySnapshot,
    rater_3_provider: Optional[BaseProvider] = None,
    adjudication_template: Optional[PromptTemplate] = None,
) -> List[FinalDecision]:
    """比较两条 Rater 链，逐观测点产出唯一 FinalDecision。

    未触发仲裁规则的维度 → source=consensus，取 chains_a 的分数为一致值。
    触发的维度 → source=adjudicated，调用 Rater3。

    Args:
        package: 两条链共同引用的 DataPackage（Rater3 仲裁时需要看完整原文）。
        chains_a: 一条 Rater 链在各观测点上的 RaterChainResult。
        chains_b: 另一条 Rater 链在各观测点上的 RaterChainResult。
        rubric  : 用于 dimension/scale 查找的 RubricSnapshot。
        policy  : 带两个仲裁触发阈值的 PolicySnapshot。
        rater_3_provider     : Rater3 用的 provider；有分歧时才需要。
        adjudication_template: 已加载的 adjudication PromptTemplate；有分歧时才需要。

    Returns:
        按观测点 code 排序的 FinalDecision 列表，每个观测点恰好一条。"""
    by_a = _by_code(chains_a)
    by_b = _by_code(chains_b)
    dims_a, dims_b = set(by_a), set(by_b)
    if dims_a != dims_b:
        raise ValueError(
            "两条 Rater 链覆盖的观测点不一致："
            f"only_in_a={sorted(dims_a - dims_b)}, only_in_b={sorted(dims_b - dims_a)}"
        )

    triggered = needs_adjudication(list(chains_a), list(chains_b), policy)

    decisions: List[FinalDecision] = []
    for code in sorted(dims_a):
        chain_a = by_a[code]
        chain_b = by_b[code]

        if code not in triggered:
            # "一致" = 未触发仲裁规则，不要求两分完全相等（分差<=1 且非同向漂移组
            # 也算一致）。不相等时确定性地取 chains_a 的值——不平均、不取高分。
            decisions.append(
                FinalDecision(
                    code=code,
                    final_score=chain_a.score.score,
                    source=ScoreSource.CONSENSUS,
                    unit_ids=list(chain_a.score.supporting_unit_ids),
                    rationale=chain_a.score.rationale,
                )
            )
            continue

        if rater_3_provider is None or adjudication_template is None:
            raise ValueError(
                f"观测点 '{code}' 触发仲裁但缺少 rater_3 provider/adjudication 模板"
                "——分歧一律走 Rater3，不静默降级。"
            )

        dimension = rubric.get_dimension(code)
        if dimension is None:
            raise ValueError(f"观测点 '{code}' 不在量规里")

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
                code=code,
                final_score=adjudicated_score.score,
                source=ScoreSource.ADJUDICATED,
                unit_ids=list(adjudicated_score.supporting_unit_ids),
                rationale=adjudicated_score.rationale,
            )
        )

    return decisions
