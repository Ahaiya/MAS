"""
评分 Agent，负责基于维度观察和证据生成单个评分假设。

Scorer — 调用已配置的 provider 以生成 ScoreHypothesis。

通过 prompt_builders 构建评分提示，调用 provider，
将结构化的 JSON 输出解析为 ScoreHypothesis 契约。

有效的评分范围从 RubricSnapshot 中解析；此处不包含硬编码的
评分值或维度代码。"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from src.agents.prompt_builders import build_scoring_prompt
from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.evidence import DimensionObservation, EvidenceSpan
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import ScoreHypothesis
from src.policies.rubric_core import get_descriptor_refs_for_score, get_scale_range, get_scale_ref
from src.providers.base import BaseProvider, LLMRequest, ProviderParseError
from src.providers.prompt_loader import PromptTemplate
from src.providers.structured_output import normalize_structured_output

_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["proposed_score"],
    "properties": {
        "proposed_score": {"type": "integer"},
        "descriptor_refs": {"type": "array", "items": {"type": "string"}},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
        "justification": {"type": "string"},
    },
}

_SCORING_RECOVERY_PARAMS: Dict[str, Any] = {
    "temperature": 0.0,
    "max_tokens": 2048,
}


def _merge_request_params(
    base: Optional[Dict[str, Any]],
    overrides: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """合并请求参数，保留如 extra_body 等嵌套字典。"""
    merged: Dict[str, Any] = dict(base or {})
    for key, value in (overrides or {}).items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def _make_request(
    *,
    prompt_text: str,
    observation: DimensionObservation,
    rater_id: str,
    template_used: PromptTemplate,
    evidence_span_count: int,
    node_id: str,
    stage_name: str,
    used_override_template: bool,
    params: Optional[Dict[str, Any]] = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> LLMRequest:
    metadata = {
        "node_id": node_id,
        "stage_name": stage_name,
        "dimension_id": observation.dimension_id,
        "observation_id": observation.observation_id,
        "rater_id": rater_id,
        "template_source": template_used.source_path,
        "template_version": template_used.metadata.get("template_version"),
        "used_override_template": used_override_template,
        "evidence_span_count": evidence_span_count,
    }
    metadata.update(dict(extra_metadata or {}))
    return LLMRequest(
        prompt=prompt_text,
        output_schema=_OUTPUT_SCHEMA,
        params=dict(params or {}),
        metadata=metadata,
    )


def _parse_scoring_response(response) -> Dict[str, Any]:
    if response.structured_data is not None:
        return response.structured_data
    return normalize_structured_output(response.content, schema=_OUTPUT_SCHEMA)


def run(
    observation: DimensionObservation,
    evidence_spans: List[EvidenceSpan],
    rubric: RubricSnapshot,
    provider: BaseProvider,
    template: PromptTemplate,
    rater_id: str,
    scoring_context: Optional[dict] = None,
    override_template: Optional[PromptTemplate] = None,
    prior_hypotheses: Optional[List[ScoreHypothesis]] = None,
    node_id: str = "node_scorer",
    stage_name: str = "scoring",
    evidence_focus: str = "",
) -> ScoreHypothesis:
    """
    使用已配置的 provider 对单个维度进行评分。
    
    参数：
        observation  : 该维度的 DimensionObservation。
        evidence_spans: 观察中引用的 Evidence spans。
        rubric       : 用于 scale/descriptor 查找的 RubricSnapshot。
        provider     : 要调用的已配置 BaseProvider。
        template     : 已加载的评分提示模板。
        rater_id     : 评分者标识符（例如 "rater_1"）。
        scoring_context: 可选的任务级评分上下文（完整文件字典）。
        override_template: 可选的单维度评分覆盖模板。
        evidence_focus   : 可选的任务级证据焦点字符串。
    
    返回：
        ScoreHypothesis，其分数被限制在有效的 rubric scale 范围内。"""
    prompt_text = build_scoring_prompt(
        observation,
        evidence_spans,
        rubric,
        template,
        scoring_context=scoring_context,
        override_template=override_template,
        prior_hypotheses=prior_hypotheses,
        evidence_focus=evidence_focus,
    )
    template_used = override_template or template
    request = _make_request(
        prompt_text=prompt_text,
        observation=observation,
        rater_id=rater_id,
        template_used=template_used,
        evidence_span_count=len(evidence_spans),
        node_id=node_id,
        stage_name=stage_name,
        used_override_template=override_template is not None,
    )
    try:
        response = provider.complete(request)
        data = _parse_scoring_response(response)
    except ProviderParseError:
        recovery_request = _make_request(
            prompt_text=prompt_text,
            observation=observation,
            rater_id=rater_id,
            template_used=template_used,
            evidence_span_count=len(evidence_spans),
            node_id=node_id,
            stage_name=stage_name,
            used_override_template=override_template is not None,
            params=_merge_request_params(request.params, _SCORING_RECOVERY_PARAMS),
            extra_metadata={
                "recovery_reason": "parse_error",
                "scoring_retry_attempt": 2,
            },
        )
        response = provider.complete(recovery_request)
        data = _parse_scoring_response(response)

    dim_id = observation.dimension_id
    scale_min, scale_max = get_scale_range(rubric, dim_id)
    scale_ref = get_scale_ref(rubric, dim_id)

    raw_score = int(data.get("proposed_score", scale_min))
    score_val = max(scale_min, min(scale_max, raw_score))
    score = create_score_representation(score_val, scale_ref)

    descriptor_refs: List[str] = list(data.get("descriptor_refs") or [])
    if not descriptor_refs:
        descriptor_refs = get_descriptor_refs_for_score(rubric, dim_id, score_val) or [f"level_{score_val}"]

    raw_evidence_ids = list(data.get("evidence_ids") or [])
    valid_span_ids = {span.span_id for span in evidence_spans}
    evidence_span_ids = [
        evidence_id
        for evidence_id in raw_evidence_ids
        if evidence_id in valid_span_ids
    ]
    if not evidence_span_ids:
        evidence_span_ids = list(observation.supporting_span_ids)

    confidence = float(data.get("confidence", 0.7))
    justification = str(data.get("justification", ""))

    return ScoreHypothesis(
        hypothesis_id=f"hyp-score-{uuid.uuid4().hex[:12]}",
        observation_id=observation.observation_id,
        dimension_id=dim_id,
        rater_id=rater_id,
        score=score,
        descriptor_refs=descriptor_refs,
        evidence_span_ids=evidence_span_ids,
        rationale=justification,
        confidence=confidence,
    )
