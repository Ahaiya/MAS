"""
Prompt 构造器：把 typed contract 映射为各阶段模板可渲染的 Jinja2 上下文并渲染。

每个构造器接收 typed contract（DataPackage / dimension / RubricSnapshot /
RaterChainResult / FinalDecision），提取模板期望的上下文变量，调用
render_template() 返回渲染后的字符串。

此处不硬编码任何 rubric trait 名称、dimension code、分数值或 policy 阈值——所有
domain 值都来自 contract 对象与运行时从 configs/ 解析出的 RubricSnapshot。"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from src.contracts.package import DataPackage
from src.contracts.scoring import FinalDecision, RaterChainResult
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

    注入的上下文变量 (匹配 scoring.yaml)：
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


def build_adjudication_prompt(
    package: DataPackage,
    dimension: Dict[str, Any],
    chain_a: RaterChainResult,
    chain_b: RaterChainResult,
    template: PromptTemplate,
) -> str:
    """
    为 adjudicate 阶段构建 prompt：Rater3 看双链各自引用的证据编号 + 完整原文
    （package 全部单元）+ rubric anchors，但看不到双方分数（防锚定）。

    注入的上下文变量 (匹配 adjudication.yaml)：
        dimension_name    : 来自 rubric 的可读 dimension 名称。
        dimension_anchors : 仅当前 dimension 的 anchors [{rank, text}]。
        units             : 完整原文的全部单元 [{id, kind, text}]（不止双链各自的
                             证据子集——Rater3 可以看到前两条链没看到的单元）。
        raters_evidence   : 双链各自引用的证据编号 [{rater_id, evidence_unit_ids}]，
                             不含任何分数字段。

    Returns:
        渲染好准备发送给 provider 的 prompt 字符串。"""
    context = {
        "dimension_name": dimension.get("name", dimension.get("dimension_id", "")),
        "dimension_anchors": _dimension_anchor_entries(dimension),
        "units": [{"id": unit.id, "kind": unit.kind, "text": unit.text} for unit in package.units],
        "raters_evidence": [
            {"rater_id": chain_a.rater_id, "evidence_unit_ids": list(chain_a.evidence_unit_ids)},
            {"rater_id": chain_b.rater_id, "evidence_unit_ids": list(chain_b.evidence_unit_ids)},
        ],
    }
    return render_template(template, context)


def build_feedback_prompt(
    package: DataPackage,
    decision: FinalDecision,
    dimension: Dict[str, Any],
    template: PromptTemplate,
) -> str:
    """
    为 feedback 阶段构建 prompt：给模型某二级指标的最终分 + 引用证据全文 + 量规
    锚点，要求生成面向学生的文字反馈。

    注入的上下文变量 (匹配 feedback.yaml)：
        dimension_name    : 来自 rubric 的可读 dimension 名称。
        dimension_anchors : 仅当前 dimension 的 anchors [{rank, text}]。
        final_score       : 该二级指标的最终分（canonical_score）。
        units             : final_score 引用的证据单元全文 [{id, kind, text}]。

    Returns:
        渲染好准备发送给 provider 的 prompt 字符串。"""
    context = {
        "dimension_name": dimension.get("name", dimension.get("dimension_id", "")),
        "dimension_anchors": _dimension_anchor_entries(dimension),
        "final_score": decision.final_score.canonical_score,
        "units": _units_by_ids(package, decision.unit_ids),
    }
    return render_template(template, context)
