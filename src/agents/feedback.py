"""
二级指标反馈生成：聚合 + 雷达数据 + 每观测点文字反馈，产出 feedback.json 的
内容；并把双链完整证据 + 最终决策整理成 rater_chains.json 的内容（审计用）。

"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from src.agents.prompt_builders import build_feedback_prompt
from src.contracts.configuration import RubricSnapshot
from src.contracts.package import DataPackage
from src.contracts.scoring import FinalDecision, RaterChainResult
from src.policies.aggregation import aggregate_final_decisions
from src.providers.base import BaseProvider, LLMRequest
from src.providers.prompt_loader import PromptTemplate


def build_radar_data(decisions: Sequence[FinalDecision]) -> List[Dict[str, Any]]:
    """雷达图数据：各观测点分数数组，按 code 排序，供前端渲染。"""
    return [
        {"code": d.code, "score": d.final_score}
        for d in sorted(decisions, key=lambda d: d.code)
    ]


def generate_feedback_text(
    package: DataPackage,
    decision: FinalDecision,
    dimension: Dict[str, Any],
    scale_levels: Dict[int, str],
    provider: BaseProvider,
    template: PromptTemplate,
) -> str:
    """调用 LLM，基于最终分 + 引用证据全文，为一个观测点生成学生可读的文字反馈。"""
    prompt_text = build_feedback_prompt(package, decision, dimension, scale_levels, template)
    response = provider.complete(
        LLMRequest(
            prompt=prompt_text,
            metadata={
                "node_id": "node_feedback",
                "stage_name": "feedback",
                "code": decision.code,
                "template_source": template.source_path,
            },
        )
    )
    return response.content.strip()


def build_feedback_report(
    package: DataPackage,
    decisions: Sequence[FinalDecision],
    rubric: RubricSnapshot,
    provider: BaseProvider,
    template: PromptTemplate,
) -> Dict[str, Any]:
    """产出 feedback.json 的完整内容：二级指标分 + 雷达 + 各观测点
    final_score/source/证据 unit_ids/文字反馈（证据存 unit_ids，不存复述原文）。"""
    dimensions_out: Dict[str, Any] = {}
    for decision in sorted(decisions, key=lambda d: d.code):
        dimension = rubric.get_dimension(decision.code)
        if dimension is None:
            raise ValueError(f"观测点 '{decision.code}' 不在量规里")
        dimensions_out[decision.code] = {
            "final_score": decision.final_score,
            "source": decision.source.value,
            "unit_ids": list(decision.unit_ids),
            "feedback": generate_feedback_text(
                package, decision, dimension, rubric.scale_levels, provider, template
            ),
        }

    return {
        "primary_score": aggregate_final_decisions(
            list(decisions), {d["code"]: d["weight"] for d in rubric.dimensions}
        ),
        "radar": build_radar_data(decisions),
        "dimensions": dimensions_out,
    }


def build_rater_chains_report(
    chains_a: Sequence[RaterChainResult],
    chains_b: Sequence[RaterChainResult],
    decisions: Sequence[FinalDecision],
) -> Dict[str, Any]:
    """产出 rater_chains.json 的完整内容：双链完整证据（各自 rationale/confidence
    俱全）+ 最终决策（source 标记哪些经过仲裁），审计用。

    "chains" 是一个扁平列表而非按 rater_id 做键的 dict——每条 RaterChainResult
    的 to_dict() 里自带 rater_id 字段，用它做外层 dict 的键在两条链恰好同
    rater_id 时会静默互相覆盖，风险不值得为省一层嵌套去冒。"""
    all_chains = sorted(list(chains_a) + list(chains_b), key=lambda c: (c.code, c.rater_id))
    return {
        "chains": [c.to_dict() for c in all_chains],
        "final_decisions": [d.to_dict() for d in sorted(decisions, key=lambda d: d.code)],
    }
