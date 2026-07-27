"""
Prompt 构造器，负责把 typed contract 映射为各阶段模板可渲染的上下文。

Node prompt 构造器 —— 将 typed contract 映射为 Jinja2 上下文字典。

每个构造器函数：
1. 接收 typed contract 对象 (plan, observation, decision, spans, rubric)。
2. 提取对应模板所期望的 Jinja2 上下文变量。
3. 调用 render_template() 并返回渲染后的字符串。

此处不硬编码任何 rubric trait 名称、dimension codes、score values 或 policy thresholds。所有 domain values 均源自 contract 对象和运行时从 configs/ 解析出的 RubricSnapshot。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.evidence import DimensionObservation, EvidenceSpan
from src.contracts.package import DataPackage
from src.contracts.request_models import CoveragePlan, NormalizedDocument
from src.contracts.scoring import FinalDimensionDecision, ScoreHypothesis
from src.policies.rubric_core import get_scale_range
from src.providers.prompt_loader import PromptTemplate, render_template


def _level_anchor_text(level: Dict[str, Any]) -> str:
    """优先使用完整的 descriptor 文本，而非粗略的 scale 标签。"""
    descriptors = [
        str(item).strip()
        for item in (level.get("descriptors") or [])
        if str(item).strip()
    ]
    if descriptors:
        return "\n".join(descriptors)
    return str(level.get("summary", "")).strip()


def _dimension_anchor_entries(dim: Dict[str, Any]) -> List[Dict[str, Any]]:
    """仅返回当前 dimension 的 anchors，按从高到低排序。"""
    levels = sorted(
        dim.get("levels", []) or [],
        key=lambda item: int(item.get("rank", 0)),
        reverse=True,
    )
    anchors: List[Dict[str, Any]] = []
    for level in levels:
        anchor_text = _level_anchor_text(level)
        if not anchor_text:
            continue
        anchors.append(
            {
                "rank": int(level.get("rank", 0)),
                "text": anchor_text,
            }
        )
    return anchors


def build_extraction_prompt(
    plan: CoveragePlan,
    document: NormalizedDocument,
    rubric: RubricSnapshot,
    template: PromptTemplate,
    override_template: Optional[PromptTemplate] = None,
    evidence_focus: str = "",
    material_description: str = "",
    extraction_hints: str = "",
) -> str:
    """
    为单个 dimension 构建 evidence-extraction prompt。
    
    注入的上下文变量 (匹配 evidence_extraction.yaml v2)：
        dimension_name    : 来自 rubric 的可读 dimension 名称。
        dimension_anchors : 仅当前 dimension 的 anchors [{rank, text}]。
        evidence_focus    : 关于寻找什么的任务级指导。
        chunks            : 候选 chunks [{id, title, text}]。
    
    Args:
        plan    : 定义要覆盖哪些 dimension 和 facets 的 CoveragePlan。
        document: 包含文章文本的 NormalizedDocument。
        rubric  : 提供 dimension 元数据的 RubricSnapshot。
        template         : 已加载的默认 PromptTemplate。
        override_template: 可选的按 dimension 覆盖的 PromptTemplate。
        evidence_focus   : 可选的任务级 evidence focus 字符串。
    
    Returns:
        渲染好准备发送给 provider 的 prompt 字符串。"""
    dim: Dict[str, Any] = rubric.dimension_by_id.get(plan.dimension_id, {})
    units_by_id = {u.unit_id: u for u in document.text_units}

    if plan.coverage_strategy != "full_scan" and plan.target_unit_ids:
        selected_units = [
            units_by_id[uid]
            for uid in plan.target_unit_ids
            if uid in units_by_id
        ]
    else:
        selected_units = list(document.text_units)

    selected_units = sorted(selected_units, key=lambda unit: unit.sequence_index)
    chunks = [
        {
            "id": unit.unit_id,
            "title": unit.chunk_title or "",
            "text": unit.text,
            "source_type": unit.source_type,
            "source_label": unit.source_label or "",
        }
        for unit in selected_units
    ]

    context = {
        "dimension_name": dim.get("name", plan.dimension_id),
        "dimension_anchors": _dimension_anchor_entries(dim),
        "evidence_focus": evidence_focus,
        "material_description": material_description,
        "extraction_hints": extraction_hints,
        "chunks": chunks,
    }
    chosen_template = override_template or template
    return render_template(chosen_template, context)


def build_scoring_prompt(
    observation: DimensionObservation,
    evidence_spans: List[EvidenceSpan],
    rubric: RubricSnapshot,
    template: PromptTemplate,
    scoring_context: Optional[dict] = None,
    override_template: Optional[PromptTemplate] = None,
    prior_hypotheses: Optional[List] = None,
    evidence_focus: str = "",
) -> str:
    """
    为单个 dimension 构建 scoring prompt。
    
    注入的上下文变量 (匹配 scoring.yaml v2)：
        dimension_name      : 来自 rubric 的可读 dimension 名称。
        dimension_anchors   : 仅当前 dimension 的 anchors [{rank, text}]。
        evidence_focus      : 关于寻找什么的任务级指导。
        evidence_spans      : 扁平列表 [{span_id, chunk_id, quote, support_type}]。
        score_anchors       : 来自 scoring_context 的 Anchor 示例。
        calibration_notes   : 按 dimension 或全局的 calibration 提醒。
        prior_rater_context : 用于 adjudication 路径的先前 rater 分数。
    
    Args:
        observation   : 总结提取证据的 DimensionObservation。
        evidence_spans: 支持该 observation 的相关 EvidenceSpan 对象。
        rubric        : 用于 dimension/scale/level 查找的 RubricSnapshot。
        template      : 已加载的 PromptTemplate (应为 scoring.yaml)。
        scoring_context: 可选的任务级 scoring context (完整文件字典)。
        override_template: 可选的按 dimension 覆盖的 PromptTemplate。
        evidence_focus   : 可选的任务级 evidence focus 字符串。
    
    Returns:
        渲染好准备发送给 provider 的 prompt 字符串。"""
    dim = rubric.dimension_by_id.get(observation.dimension_id, {})
    dim_code = str(dim.get("code", ""))

    # 从所有 facet findings 构建扁平的 evidence_spans 列表
    span_by_id = {span.span_id: span for span in evidence_spans}
    seen_ids: set = set()
    flat_spans = []
    for finding in observation.facet_findings:
        for span_id in list(finding.supporting_span_ids) + list(finding.counter_span_ids):
            if span_id in seen_ids:
                continue
            seen_ids.add(span_id)
            span = span_by_id.get(span_id)
            if span is None:
                continue
            flat_spans.append({
                "span_id": span_id,
                "chunk_id": span.unit_id or "",
                "quote": span.text_quote or "",
                "support_type": span.support_type or "supporting",
            })

    # calibration_notes: 按 dimension 查找 (从 task context list 中获取)，然后全局回退
    raw_ctx = scoring_context if isinstance(scoring_context, dict) else {}
    calibration_notes = ""
    per_dim_list = raw_ctx.get("scoring_context") or []
    if isinstance(per_dim_list, list):
        for entry in per_dim_list:
            if isinstance(entry, dict) and str(entry.get("code", "")) == dim_code:
                calibration_notes = str(entry.get("calibration_notes", ""))
                break
    if not calibration_notes:
        calibration_notes = str(raw_ctx.get("calibration_notes") or "")

    score_anchors = list(raw_ctx.get("score_anchors") or [])
    human_instructions = str(raw_ctx.get("human_instructions") or "")
    material_description = str((raw_ctx.get("material_context") or {}).get("description", ""))

    prior_rater_context = [
        {
            "rater_id": hyp.rater_id,
            "score": hyp.score.canonical_score,
            "justification": hyp.rationale or "",
        }
        for hyp in (prior_hypotheses or [])
    ]

    context = {
        "dimension_name": dim.get("name", observation.dimension_id),
        "dimension_anchors": _dimension_anchor_entries(dim),
        "evidence_focus": evidence_focus,
        "material_description": material_description,
        "evidence_spans": flat_spans,
        "score_anchors": score_anchors,
        "calibration_notes": calibration_notes,
        "human_instructions": human_instructions,
        "prior_rater_context": prior_rater_context,
    }

    chosen_template = override_template or template
    return render_template(chosen_template, context)


def build_explanation_prompt(
    decision: FinalDimensionDecision,
    evidence_spans: List[EvidenceSpan],
    rubric: RubricSnapshot,
    template: PromptTemplate,
    override_template: Optional[PromptTemplate] = None,
    hypotheses: Optional[List[ScoreHypothesis]] = None,
    evidence_focus: str = "",
    audience: str = "evaluator",
    feedback_hints: str = "",
) -> str:
    """
    为已最终确定的 dimension decision 构建 explanation/feedback prompt。
    
    注入的上下文变量 (匹配 explanation.yaml v2)：
        dimension_name   : 来自 rubric 的可读 dimension 名称。
        final_score      : 来自最终 decision 的整数规范 score。
        max_score        : 此 dimension 的 rubric scale 的最高 score。
        scale_max        : max_score 的别名，用于偏好 scale 措辞的模板。
        was_adjudicated  : decision 是否经过 adjudicated。
        justification_1  : Adjudicator 理由 (adjudicated) 或 rater_1 理由。
        justification_2  : Rater_2 理由 (仅限非 adjudicated 路径)。
        evidence_spans   : 扁平列表 [{span_id, quote, support_type}]。
        evidence_focus   : 关于寻找什么的任务级指导。
        audience         : 面向学习者的为 "student"，面向专业人员的为 "evaluator"。
    
    Args:
        decision        : 包含 score 和 evidence refs 的 FinalDimensionDecision。
        evidence_spans  : 此 decision 可用的 EvidenceSpan 对象。
        rubric          : 用于 dimension 元数据的 RubricSnapshot。
        template        : 已加载的全局 explanation PromptTemplate。
        override_template: 可选的按 dimension 覆盖的 PromptTemplate。
        hypotheses      : 用于提取 rater justifications 的 ScoreHypothesis 列表。
        evidence_focus  : 可选的任务级 evidence focus 字符串。
        audience        : Feedback audience ("student" 或 "evaluator")。
    
    Returns:
        渲染好准备发送给 provider 的 prompt 字符串。"""
    dim = rubric.dimension_by_id.get(decision.dimension_id, {})
    scale_min, scale_max = get_scale_range(rubric, decision.dimension_id)
    was_adjudicated = decision.adjudication_id is not None

    # 从 hypotheses 中提取 rater justifications
    dim_hyps = [h for h in (hypotheses or []) if h.dimension_id == decision.dimension_id]
    hyps_by_rater = {h.rater_id: h for h in dim_hyps}

    if was_adjudicated:
        adj_hyp = hyps_by_rater.get("rater_3")
        justification_1 = (adj_hyp.rationale or "") if adj_hyp else ""
        justification_2 = ""
    else:
        r1_hyp = hyps_by_rater.get("rater_1")
        r2_hyp = hyps_by_rater.get("rater_2")
        justification_1 = (r1_hyp.rationale or "") if r1_hyp else ""
        justification_2 = (r2_hyp.rationale or "") if r2_hyp else ""

    # 从 decision.evidence_span_ids 构建扁平的 evidence_spans
    span_by_id = {span.span_id: span for span in evidence_spans}
    flat_spans = []
    for span_id in decision.evidence_span_ids:
        span = span_by_id.get(span_id)
        if span is None or not (span.text_quote or "").strip():
            continue
        flat_spans.append({
            "span_id": span_id,
            "quote": span.text_quote or "",
            "support_type": span.support_type or "supporting",
        })

    context = {
        "dimension_name": dim.get("name", decision.dimension_id),
        "final_score": decision.final_score.canonical_score,
        "min_score": scale_min,
        "max_score": scale_max,
        "scale_min": scale_min,
        "scale_max": scale_max,
        "scale_ref": decision.final_score.scale_ref,
        "was_adjudicated": was_adjudicated,
        "justification_1": justification_1,
        "justification_2": justification_2,
        "evidence_spans": flat_spans,
        "evidence_focus": evidence_focus,
        "audience": audience,
        "feedback_hints": feedback_hints,
    }

    chosen_template = override_template or template
    return render_template(chosen_template, context)


# ═══════════════════════════════════════════════════════════════════════════
# v2 —— Rater 完整链（select → extract → score）的 prompt 构造器。
# 与上方 v1 构造器并存；消费 DataPackage/Unit 而非 CoveragePlan/NormalizedDocument，
# 证据以 unit_ids 引用而非复述原文。
# ═══════════════════════════════════════════════════════════════════════════


def _units_by_ids(package: DataPackage, unit_ids: Sequence[int]) -> List[Dict[str, Any]]:
    """按给定编号顺序返回 [{id, kind, text}]，跳过 package 中不存在的编号。"""
    lookup = {unit.id: unit for unit in package.units}
    return [
        {"id": unit.id, "kind": unit.kind, "text": unit.text}
        for unit_id in unit_ids
        if (unit := lookup.get(unit_id)) is not None
    ]


def _preview_by_bytes(text: str, max_bytes: int) -> str:
    """按 UTF-8 字节数截断预览，而非字符数——中文字符按字符切会让预览远超预期长度。
    忽略被截断处的半个多字节字符，不做完整性校验（纯展示用途）。"""
    return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def build_rater_select_prompt(
    package: DataPackage,
    dimension: Dict[str, Any],
    template: PromptTemplate,
    preview_bytes: int = 200,
) -> str:
    """
    为 select 阶段构建 prompt：给模型「单元号 + 每段前若干字节」做首轮相关性扫描。

    注入的上下文变量 (匹配 select.yaml)：
        dimension_name    : 来自 rubric 的可读 dimension 名称。
        dimension_anchors : 仅当前 dimension 的 anchors [{rank, text}]。
        units             : 全部候选单元的预览 [{id, kind, preview}]。

    Args:
        package      : 待选段的 DataPackage。
        dimension    : rubric.dimension_by_id 中的单个 dimension dict。
        template     : 已加载的 select PromptTemplate。
        preview_bytes: 每个单元预览保留的 UTF-8 字节数。

    Returns:
        渲染好准备发送给 provider 的 prompt 字符串。"""
    units = [
        {"id": unit.id, "kind": unit.kind, "preview": _preview_by_bytes(unit.text, preview_bytes)}
        for unit in package.units
    ]
    context = {
        "dimension_name": dimension.get("name", dimension.get("dimension_id", "")),
        "dimension_anchors": _dimension_anchor_entries(dimension),
        "units": units,
    }
    return render_template(template, context)


def build_rater_extraction_prompt(
    package: DataPackage,
    selected_unit_ids: Sequence[int],
    dimension: Dict[str, Any],
    template: PromptTemplate,
) -> str:
    """
    为 extract 阶段构建 prompt：给模型 select 阶段选中单元的全文，要求返回其中
    真正构成证据的单元编号。

    注入的上下文变量 (匹配 extraction.yaml)：
        dimension_name    : 来自 rubric 的可读 dimension 名称。
        dimension_anchors : 仅当前 dimension 的 anchors [{rank, text}]。
        units             : 被选中单元的全文 [{id, kind, text}]。

    Returns:
        渲染好准备发送给 provider 的 prompt 字符串。"""
    context = {
        "dimension_name": dimension.get("name", dimension.get("dimension_id", "")),
        "dimension_anchors": _dimension_anchor_entries(dimension),
        "units": _units_by_ids(package, selected_unit_ids),
    }
    return render_template(template, context)


def build_rater_scoring_prompt(
    package: DataPackage,
    evidence_unit_ids: Sequence[int],
    dimension: Dict[str, Any],
    template: PromptTemplate,
) -> str:
    """
    为 score 阶段构建 prompt：给模型 extract 阶段确认的证据单元全文 + rubric anchors，
    要求给出 DimensionScore。

    注入的上下文变量 (匹配 rater_scoring.yaml)：
        dimension_name    : 来自 rubric 的可读 dimension 名称。
        dimension_anchors : 仅当前 dimension 的 anchors [{rank, text}]。
        units             : 证据单元的全文 [{id, kind, text}]。

    Returns:
        渲染好准备发送给 provider 的 prompt 字符串。"""
    context = {
        "dimension_name": dimension.get("name", dimension.get("dimension_id", "")),
        "dimension_anchors": _dimension_anchor_entries(dimension),
        "units": _units_by_ids(package, evidence_unit_ids),
    }
    return render_template(template, context)
