"""
单 Rater 完整链：select → extract → score，产出 RaterChainResult。

合并了 v1 的 extractor.py + observer.py + scorer.py（三者已删除）。
一个 Rater 三趟共用同一个 provider（raters.rater_N），不拆分；取证与评分是两次
独立 LLM 调用，保证证据先于分数生成。模型引用证据只能返回已存在的单元编号——
select 阶段越界编号静默过滤（只是候选范围，不是证据主张），extract/score 阶段
越界编号直接拒绝（消除自由复述 + 模糊匹配）。"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from src.agents.llm_json import call_llm, coerce_int_ids, reject_out_of_bounds
from src.agents.prompt_builders import (
    build_rater_extraction_prompt,
    build_rater_scoring_prompt,
    build_rater_select_prompt,
)
from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.package import DataPackage
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import DimensionScore, RaterChainResult
from src.policies.rubric_core import get_scale_range, get_scale_ref
from src.providers.base import BaseProvider
from src.providers.prompt_loader import PromptTemplate

_SELECT_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["selected_unit_ids"],
    "properties": {"selected_unit_ids": {"type": "array", "items": {"type": "integer"}}},
}
_EXTRACTION_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["evidence_unit_ids"],
    "properties": {"evidence_unit_ids": {"type": "array", "items": {"type": "integer"}}},
}
_SCORING_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["proposed_score"],
    "properties": {
        "proposed_score": {"type": "integer"},
        "supporting_unit_ids": {"type": "array", "items": {"type": "integer"}},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
}

DEFAULT_SELECT_PREVIEW_BYTES = 200


def select(
    package: DataPackage,
    dimension: Dict[str, Any],
    provider: BaseProvider,
    template: PromptTemplate,
    rater_id: str,
    preview_bytes: int = DEFAULT_SELECT_PREVIEW_BYTES,
) -> List[int]:
    """看「单元号 + 每段前若干字节」选出与该二级指标相关的单元号。

    模型幻觉出的编号会被静默过滤——这一步只是缩小候选范围，不是证据主张。"""
    dimension_id = str(dimension.get("dimension_id", ""))
    prompt_text = build_rater_select_prompt(package, dimension, template, preview_bytes=preview_bytes)
    data = call_llm(
        provider,
        prompt_text,
        _SELECT_OUTPUT_SCHEMA,
        node_id="node_rater_select",
        stage_name="select",
        dimension_id=dimension_id,
        rater_id=rater_id,
        template=template,
    )
    candidate_ids = coerce_int_ids(data.get("selected_unit_ids"))
    valid_ids = {unit.id for unit in package.units}
    return [uid for uid in candidate_ids if uid in valid_ids]


def extract(
    package: DataPackage,
    selected_unit_ids: Sequence[int],
    dimension: Dict[str, Any],
    provider: BaseProvider,
    template: PromptTemplate,
    rater_id: str,
) -> List[int]:
    """选中单元全文 → 证据，返回其中真正构成证据的单元编号。

    只有 select 阶段展示过的单元才在有效范围内；越界编号直接拒绝。"""
    dimension_id = str(dimension.get("dimension_id", ""))
    prompt_text = build_rater_extraction_prompt(package, selected_unit_ids, dimension, template)
    data = call_llm(
        provider,
        prompt_text,
        _EXTRACTION_OUTPUT_SCHEMA,
        node_id="node_rater_extract",
        stage_name="extract",
        dimension_id=dimension_id,
        rater_id=rater_id,
        template=template,
    )
    evidence_ids = coerce_int_ids(data.get("evidence_unit_ids"))
    reject_out_of_bounds(evidence_ids, set(selected_unit_ids), rater_id=rater_id, stage="extract")
    return evidence_ids


def score(
    package: DataPackage,
    evidence_unit_ids: Sequence[int],
    dimension: Dict[str, Any],
    rubric: RubricSnapshot,
    provider: BaseProvider,
    template: PromptTemplate,
    rater_id: str,
) -> DimensionScore:
    """证据 + 锚点 → DimensionScore。

    supporting_unit_ids 只能引用 extract 阶段已确认的证据编号；越界直接拒绝。"""
    dimension_id = dimension["dimension_id"]
    prompt_text = build_rater_scoring_prompt(package, evidence_unit_ids, dimension, template)
    data = call_llm(
        provider,
        prompt_text,
        _SCORING_OUTPUT_SCHEMA,
        node_id="node_rater_score",
        stage_name="score",
        dimension_id=dimension_id,
        rater_id=rater_id,
        template=template,
    )

    scale_min, scale_max = get_scale_range(rubric, dimension_id)
    scale_ref = get_scale_ref(rubric, dimension_id)
    raw_score = int(data.get("proposed_score", scale_min))
    score_val = max(scale_min, min(scale_max, raw_score))
    score_repr = create_score_representation(score_val, scale_ref)

    supporting_unit_ids = coerce_int_ids(data.get("supporting_unit_ids"))
    reject_out_of_bounds(supporting_unit_ids, set(evidence_unit_ids), rater_id=rater_id, stage="score")

    return DimensionScore(
        dimension_id=dimension_id,
        score=score_repr,
        supporting_unit_ids=supporting_unit_ids,
        rationale=str(data.get("rationale", "")),
        confidence=float(data.get("confidence", 0.7)),
    )


def run_chain(
    package: DataPackage,
    dimension_id: str,
    rubric: RubricSnapshot,
    provider: BaseProvider,
    select_template: PromptTemplate,
    extraction_template: PromptTemplate,
    scoring_template: PromptTemplate,
    rater_id: str,
    preview_bytes: int = DEFAULT_SELECT_PREVIEW_BYTES,
) -> RaterChainResult:
    """跑完一个 Rater 对一个二级指标的完整链：select → extract → score。"""
    dimension = rubric.get_dimension(dimension_id)
    if dimension is None:
        raise ValueError(f"Dimension '{dimension_id}' not found in rubric")

    selected_unit_ids = select(package, dimension, provider, select_template, rater_id, preview_bytes=preview_bytes)
    evidence_unit_ids = extract(package, selected_unit_ids, dimension, provider, extraction_template, rater_id)
    dimension_score = score(package, evidence_unit_ids, dimension, rubric, provider, scoring_template, rater_id)

    return RaterChainResult(
        rater_id=rater_id,
        dimension_id=dimension_id,
        selected_unit_ids=selected_unit_ids,
        evidence_unit_ids=evidence_unit_ids,
        score=dimension_score,
    )
