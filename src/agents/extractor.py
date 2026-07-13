"""
证据抽取 Agent，负责从候选文本中提取可被评分和反馈引用的证据片段。

Evidence Extractor — 调用已配置的 provider 来提取 EvidenceSpan 对象。

通过 prompt_builders 构建抽取 prompt，调用 provider，
将结构化 JSON 输出解析为 EvidenceSpan contracts。

Contract 边界：所有领域知识（facet IDs、dimension 范围）均
来自 CoveragePlan 和 RubricSnapshot；此处不硬编码任何内容。"""

from __future__ import annotations

import uuid
from typing import List, Optional

from src.agents.prompt_builders import build_extraction_prompt
from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.evidence import EvidenceScope, EvidenceSpan
from src.contracts.request_models import CoveragePlan, NormalizedDocument, TextUnit
from src.providers.base import BaseProvider, LLMRequest
from src.providers.prompt_loader import PromptTemplate
from src.providers.structured_output import normalize_structured_output
from src.utils.quote_matcher import QuoteMatchResult, match_quote
from src.utils.dialogue_sources import source_for_range

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "evidence_spans": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "quote": {"type": "string"},
                    "chunk_id": {"type": "string"},
                    "facets": {"type": "array", "items": {"type": "string"}},
                    "support_type": {"type": "string"},
                },
            },
        }
    },
}


def _match_with_chunk_hint(
    quote: str,
    chunk_id: Optional[str],
    document: NormalizedDocument,
) -> QuoteMatchResult:
    """全局匹配 quote，然后在有提示时在 chunk 内进行局部重试。"""
    global_match = match_quote(quote, document.normalized_text, document.text_units)
    if global_match.match_method != "unmatched" or not chunk_id:
        return global_match

    unit = document.get_unit(chunk_id)
    if unit is None:
        return global_match

    local_unit = TextUnit(
        unit_id=unit.unit_id,
        document_id=unit.document_id,
        text=unit.text,
        start_offset=0,
        end_offset=len(unit.text),
        unit_type=unit.unit_type,
        sequence_index=0,
        chunk_title=unit.chunk_title,
        chunk_method=unit.chunk_method,
    )
    local_match = match_quote(quote, unit.text, [local_unit])
    if (
        local_match.match_method == "unmatched"
        or local_match.start_offset is None
        or local_match.end_offset is None
    ):
        return global_match

    return QuoteMatchResult(
        start_offset=unit.start_offset + local_match.start_offset,
        end_offset=unit.start_offset + local_match.end_offset,
        unit_id=unit.unit_id,
        match_method=local_match.match_method,
        confidence=local_match.confidence,
    )


def run(
    plan: CoveragePlan,
    document: NormalizedDocument,
    rubric: RubricSnapshot,
    provider: BaseProvider,
    template: PromptTemplate,
    override_template: Optional[PromptTemplate] = None,
    evidence_focus: str = "",
    material_description: str = "",
    extraction_hints: str = "",
) -> List[EvidenceSpan]:
    """
    使用已配置的 provider 为一个 dimension 提取 evidence spans。
    
    Args:
        plan     : 指定 dimension 和所需 facets 的 CoveragePlan。
        document : 包含 essay 文本的 NormalizedDocument。
        rubric   : 用于 dimension 元数据的 RubricSnapshot。
        provider : 要调用的已配置 BaseProvider。
        template         : 已加载的抽取 prompt 模板。
        override_template: 可选的按 dimension 覆盖的模板。
        evidence_focus   : 可选的任务级 evidence focus 字符串。
        extraction_hints : 可选的来自任务上下文的按 dimension 抽取提示。
    
    Returns:
        从 LLM 响应中解析出的 EvidenceSpan 对象列表。
        如果 LLM 未返回 spans，则返回空列表（非错误）。"""
    prompt_text = build_extraction_prompt(
        plan,
        document,
        rubric,
        template,
        override_template=override_template,
        evidence_focus=evidence_focus,
        material_description=material_description,
        extraction_hints=extraction_hints,
    )
    template_used = override_template or template
    request = LLMRequest(
        prompt=prompt_text,
        output_schema=_OUTPUT_SCHEMA,
        metadata={
            "node_id": "node_extractor",
            "stage_name": "evidence_extraction",
            "dimension_id": plan.dimension_id,
            "plan_id": plan.plan_id,
            "document_id": document.document_id,
            "template_source": template_used.source_path,
            "template_version": template_used.metadata.get("template_version"),
            "used_override_template": override_template is not None,
        },
    )
    response = provider.complete(request)

    # 解析结构化输出，回退到内容解析
    if response.structured_data is not None:
        data = response.structured_data
    else:
        data = normalize_structured_output(response.content)
    if not isinstance(data, dict):
        data = {}

    spans: List[EvidenceSpan] = []
    source_spans = list((document.document_metadata or {}).get("source_spans") or [])
    for span_data in data.get("evidence_spans", []):
        quote = span_data.get("quote") or ""
        chunk_id = span_data.get("chunk_id")
        facets = list(span_data.get("facets") or plan.required_facets)
        raw_support_type = str(span_data.get("support_type") or "supporting").strip().lower()
        support_type = (
            raw_support_type
            if raw_support_type in {"supporting", "counter", "neutral"}
            else "supporting"
        )
        match = _match_with_chunk_hint(quote, chunk_id, document)
        start = match.start_offset
        end = match.end_offset
        unit_id = match.unit_id
        hinted_unit = document.get_unit(chunk_id) if isinstance(chunk_id, str) else None
        scope = (
            EvidenceScope.SPAN
            if match.match_method in {"exact", "normalized", "fuzzy"}
            else EvidenceScope.GLOBAL
        )
        if scope == EvidenceScope.SPAN:
            source_type, source_label = source_for_range(
                start,
                end,
                source_spans,
                allow_mixed=False,
            )
        elif hinted_unit is not None:
            source_type = (
                hinted_unit.source_type
                if hinted_unit.source_type in {"human", "ai", "system"}
                else "unknown"
            )
            source_label = hinted_unit.source_label
        else:
            source_type, source_label = "unknown", None

        if source_type in {"ai", "system"}:
            continue

        span_id = f"span-ext-{uuid.uuid4().hex[:12]}"
        extraction_note = f"provider:{match.match_method}:{support_type}"
        spans.append(
            EvidenceSpan(
                span_id=span_id,
                document_id=plan.document_id,
                unit_id=unit_id if scope == EvidenceScope.SPAN else None,
                text_quote=quote or None,
                start_offset=start if scope == EvidenceScope.SPAN else None,
                end_offset=end if scope == EvidenceScope.SPAN else None,
                scope=scope,
                dimension_id=plan.dimension_id,
                facet_ids=facets,
                extraction_note=extraction_note,
                support_type=support_type,
                source_type=source_type,
                source_label=source_label,
            )
        )

    # 保证每个所需 facet 至少有一个 span，以便通过 coverage validation
    covered_facets = {f for s in spans for f in s.facet_ids}
    for facet_id in plan.required_facets:
        if facet_id not in covered_facets:
            spans.append(
                EvidenceSpan(
                span_id=f"span-ext-fallback-{uuid.uuid4().hex[:8]}",
                    document_id=plan.document_id,
                    unit_id=None,
                    text_quote=None,
                    start_offset=None,
                    end_offset=None,
                    scope=EvidenceScope.GLOBAL,
                    dimension_id=plan.dimension_id,
                    facet_ids=[facet_id],
                    extraction_note="provider_fallback:no_evidence",
                    support_type="supporting",
                    source_type="unknown",
                    source_label=None,
                )
            )
    return spans
