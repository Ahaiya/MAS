"""
Rater3 独立仲裁：双链分歧时补第三条独立评分链，防锚定。

输入双链各自引用的证据 unit_ids + 完整原文（DataPackage 全部单元）+ 量规锚点，
**不含双方分数**（防止仲裁者被先前分数锚定）；输出格式与 Rater1/2 的 score 阶段
一致（DimensionScore），强制引用证据 unit_ids。Rater3 看得到完整原文，引用范围
是整个 DataPackage，不局限于前两条链各自圈定的证据子集。"""

from __future__ import annotations

from typing import Any, Dict

from src.agents.llm_json import call_llm, coerce_int_ids, reject_out_of_bounds
from src.agents.prompt_builders import build_adjudication_prompt
from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.package import DataPackage
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import DimensionScore, RaterChainResult
from src.policies.rubric_core import get_scale_range, get_scale_ref
from src.providers.base import BaseProvider
from src.providers.prompt_loader import PromptTemplate

_ADJUDICATION_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["proposed_score", "supporting_unit_ids"],
    "properties": {
        "proposed_score": {"type": "integer"},
        "supporting_unit_ids": {"type": "array", "items": {"type": "integer"}},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
}

DEFAULT_ADJUDICATOR_RATER_ID = "rater_3"


def adjudicate(
    package: DataPackage,
    dimension: Dict[str, Any],
    rubric: RubricSnapshot,
    chain_a: RaterChainResult,
    chain_b: RaterChainResult,
    provider: BaseProvider,
    template: PromptTemplate,
    rater_id: str = DEFAULT_ADJUDICATOR_RATER_ID,
) -> DimensionScore:
    """Rater3 独立仲裁一个二级指标：看双链各自引用的证据 unit_ids + 完整原文 +
    量规锚点，不看 chain_a/chain_b 的分数，产出自己的 DimensionScore。

    引用范围是整个 package（不是 chain_a/chain_b 各自的证据子集）；越界拒绝。"""
    dimension_id = dimension["dimension_id"]
    prompt_text = build_adjudication_prompt(package, dimension, chain_a, chain_b, template)
    data = call_llm(
        provider,
        prompt_text,
        _ADJUDICATION_OUTPUT_SCHEMA,
        node_id="node_adjudicator",
        stage_name="adjudicate",
        dimension_id=dimension_id,
        rater_id=rater_id,
        template=template,
    )

    scale_min, scale_max = get_scale_range(rubric, dimension_id)
    scale_ref = get_scale_ref(rubric, dimension_id)
    raw_score = int(data.get("proposed_score", scale_min))
    score_val = max(scale_min, min(scale_max, raw_score))
    score_repr = create_score_representation(score_val, scale_ref)

    valid_ids = {unit.id for unit in package.units}
    supporting_unit_ids = coerce_int_ids(data.get("supporting_unit_ids"))
    reject_out_of_bounds(supporting_unit_ids, valid_ids, rater_id=rater_id, stage="adjudicate")
    if not supporting_unit_ids:
        # 强制引用证据 unit_ids：仲裁分必须可核验，不允许零引用的"裸分数"。
        raise ValueError(f"rater '{rater_id}' adjudicate: 未引用任何证据 unit_ids，仲裁输出被拒绝。")

    return DimensionScore(
        dimension_id=dimension_id,
        score=score_repr,
        supporting_unit_ids=supporting_unit_ids,
        rationale=str(data.get("rationale", "")),
        confidence=float(data.get("confidence", 0.7)),
    )
