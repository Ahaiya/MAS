"""
Rater3 复议：双链分歧时触发。

输入双链各自引用的证据 unit_ids + 各自的判档理由 + 完整原文（DataPackage 全部
单元）+ 量规锚点；**不含双方分数与 confidence**。输出格式与 Rater1/2 的 score
阶段一致（DimensionScore），强制引用证据 unit_ids。Rater3 看得到完整原文，引用
范围是整个 DataPackage，不局限于前两条链各自圈定的证据子集。

"""

from __future__ import annotations

from typing import Any, Dict

from src.agents.llm_json import call_llm, coerce_int_ids, reject_out_of_bounds
from src.agents.prompt_builders import build_adjudication_prompt
from src.contracts.configuration import RubricSnapshot
from src.contracts.package import DataPackage
from src.contracts.scoring import DimensionScore, RaterChainResult
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
    """Rater3 复议一个观测点：看双链各自的证据 unit_ids 与判档理由 + 完整原文 +
    量规锚点（不看双方分数/confidence），产出自己的 DimensionScore。

    引用范围是整个 package（不是 chain_a/chain_b 各自的证据子集）；越界拒绝。"""
    code = str(dimension["code"])
    prompt_text = build_adjudication_prompt(
        package, dimension, rubric.scale_levels, chain_a, chain_b, template
    )
    data = call_llm(
        provider,
        prompt_text,
        _ADJUDICATION_OUTPUT_SCHEMA,
        node_id="node_adjudicator",
        stage_name="adjudicate",
        code=code,
        rater_id=rater_id,
        template=template,
    )

    scale_min, scale_max = rubric.scale_min, rubric.scale_max
    raw_score = int(data.get("proposed_score", scale_min))
    score_val = max(scale_min, min(scale_max, raw_score))

    valid_ids = {unit.id for unit in package.units}
    supporting_unit_ids = coerce_int_ids(data.get("supporting_unit_ids"))
    reject_out_of_bounds(supporting_unit_ids, valid_ids, rater_id=rater_id, stage="adjudicate")
    if not supporting_unit_ids:
        # 强制引用证据 unit_ids：仲裁分必须可核验，不允许零引用的"裸分数"。
        raise ValueError(f"rater '{rater_id}' adjudicate: 未引用任何证据 unit_ids，仲裁输出被拒绝。")

    return DimensionScore(
        score=score_val,
        supporting_unit_ids=supporting_unit_ids,
        rationale=str(data.get("rationale", "")),
        confidence=float(data.get("confidence", 0.7)),
    )
