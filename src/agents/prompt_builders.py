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


def _dimension_anchor_entries(
    dim: Dict[str, Any], scale_levels: Dict[int, str]
) -> List[Dict[str, Any]]:
    """把观测点的 anchors 与量表档位标签合成锚点行 [{rank, label, text}]，按档位从高到低。

    档位标签（优秀/良好/…）来自量表、锚点正文来自观测点，渲染成 `4（优秀）：…`；
    量规里没写标签时退化成裸的 `4：…`。锚点全档非空由量规校验保证，这里不兜底。"""
    return [
        {
            "rank": rank,
            "label": f"（{label}）" if (label := str(scale_levels.get(rank, "")).strip()) else "",
            "text": text,
        }
        for rank, anchor_text in sorted(dim.get("anchors", {}).items(), reverse=True)
        if (text := str(anchor_text).strip())
    ]


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
    scale_levels: Dict[int, str],
    template: PromptTemplate,
    indicator_description: str,
    preview_bytes: int = 200,
) -> str:
    """
    为 select 阶段构建 prompt：给模型「unit_id + 每段前若干字节」做首轮相关性扫描。

    注入的上下文变量 (匹配 select.yaml)：
        dimension_name    : 来自 rubric 的可读 dimension 名称。
        dimension_anchors : 仅当前 dimension 的 anchors [{rank, label, text}]。
        units             : 全部候选单元的预览 [{id, kind, preview}]。
        indicator_description : 二级指标的完整解释（只给选段/取证看，见下）。

    Args:
        package      : 待选段的 DataPackage。
        dimension    : rubric.dimensions 中的单个观测点 dict。
        scale_levels : 量表档位标签 {档位: 名称}，与 anchors 合成锚点行。
        template     : 已加载的 select PromptTemplate。
        preview_bytes: 每个单元预览保留的 UTF-8 字节数。
        indicator_description : 二级指标的完整解释（只给选段/取证看）。

    Returns:
        渲染好准备发送给 provider 的 prompt 字符串。"""
    units = [
        {"id": unit.id, "kind": unit.kind, "preview": _preview_by_bytes(unit.text, preview_bytes)}
        for unit in package.units
    ]
    context = {
        "dimension_name": dimension.get("name", dimension.get("code", "")),
        "dimension_anchors": _dimension_anchor_entries(dimension, scale_levels),
        "indicator_description": indicator_description,
        "units": units,
    }
    return render_template(template, context)


def build_rater_extraction_prompt(
    package: DataPackage,
    selected_unit_ids: Sequence[int],
    dimension: Dict[str, Any],
    scale_levels: Dict[int, str],
    template: PromptTemplate,
    indicator_description: str,
) -> str:
    """
    为 extract 阶段构建 prompt：给模型 select 阶段选中unit 的全文，要求返回其中
    真正构成证据的单元编号。

    注入的上下文变量 (匹配 extraction.yaml)：
        dimension_name    : 来自 rubric 的可读 dimension 名称。
        dimension_anchors : 仅当前 dimension 的 anchors [{rank, label, text}]。
        units             : 被选中单元的全文 [{id, kind, text}]。
        indicator_description : 二级指标的完整解释（只给选段/取证看）。

    Returns:
        渲染好准备发送给 provider 的 prompt 字符串。"""
    context = {
        "dimension_name": dimension.get("name", dimension.get("code", "")),
        "dimension_anchors": _dimension_anchor_entries(dimension, scale_levels),
        "indicator_description": indicator_description,
        "units": _units_by_ids(package, selected_unit_ids),
    }
    return render_template(template, context)


def build_rater_scoring_prompt(
    package: DataPackage,
    evidence_unit_ids: Sequence[int],
    dimension: Dict[str, Any],
    scale_levels: Dict[int, str],
    template: PromptTemplate,
) -> str:
    """
    为 score 阶段构建 prompt：给模型 extract 阶段确认的证据单元全文 + rubric anchors，
    要求给出 DimensionScore。

    注入的上下文变量 (匹配 scoring.yaml)：
        dimension_name    : 来自 rubric 的可读 dimension 名称。
        dimension_anchors : 仅当前 dimension 的 anchors [{rank, label, text}]。
        units             : 证据单元的全文 [{id, kind, text}]。

    Returns:
        渲染好准备发送给 provider 的 prompt 字符串。"""
    context = {
        "dimension_name": dimension.get("name", dimension.get("code", "")),
        "dimension_anchors": _dimension_anchor_entries(dimension, scale_levels),
        "units": _units_by_ids(package, evidence_unit_ids),
    }
    return render_template(template, context)


def build_adjudication_prompt(
    package: DataPackage,
    dimension: Dict[str, Any],
    scale_levels: Dict[int, str],
    chain_a: RaterChainResult,
    chain_b: RaterChainResult,
    template: PromptTemplate,
) -> str:
    """
    为 adjudicate 阶段构建 prompt：Rater3 看双链各自引用的证据编号与判档理由 +
    完整原文（package 全部单元）+ rubric anchors。

    理由要给——分歧的价值就在两边怎么读同一段材料，只给证据编号集合的话 Rater3
    只能从零重造一遍判断。分数与 confidence 不给：分数往往已隐含在理由里，但那是
    带论证的隐含；自报的 confidence 是没有论证的权威感，不该成为它的偏向依据。

    注入的上下文变量 (匹配 adjudication.yaml)：
        dimension_name    : 来自 rubric 的可读 dimension 名称。
        dimension_anchors : 仅当前 dimension 的 anchors [{rank, label, text}]。
        units             : 完整原文的全部单元 [{id, kind, text}]（不止双链各自的
                             证据子集——Rater3 可以看到前两条链没看到的单元）。
        raters_evidence   : 双链各自的证据编号与判档理由
                             [{rater_id, evidence_unit_ids, rationale}]。分数与
                             confidence 仍然不给——分数由理由自己说了算的部分交给
                             Rater3 判断，自报的置信度不该成为它的偏向依据。

    Returns:
        渲染好准备发送给 provider 的 prompt 字符串。"""
    context = {
        "dimension_name": dimension.get("name", dimension.get("code", "")),
        "dimension_anchors": _dimension_anchor_entries(dimension, scale_levels),
        "units": [{"id": unit.id, "kind": unit.kind, "text": unit.text} for unit in package.units],
        "raters_evidence": [
            {
                "rater_id": chain.rater_id,
                "evidence_unit_ids": list(chain.evidence_unit_ids),
                "rationale": chain.score.rationale,
            }
            for chain in (chain_a, chain_b)
        ],
    }
    return render_template(template, context)


def build_feedback_prompt(
    package: DataPackage,
    decision: FinalDecision,
    dimension: Dict[str, Any],
    scale_levels: Dict[int, str],
    template: PromptTemplate,
) -> str:
    """
    为 feedback 阶段构建 prompt：给模型某观测点的最终分 + 引用证据全文 + 量规
    锚点，要求生成面向学生的文字反馈。

    注入的上下文变量 (匹配 feedback.yaml)：
        dimension_name    : 来自 rubric 的可读 dimension 名称。
        dimension_anchors : 仅当前 dimension 的 anchors [{rank, label, text}]。
        final_score       : 该观测点的最终分（整数）。
        rationale         : 定这个分的理由（权威那一方的），反馈据此写而不是重推一遍。
        units             : final_score 引用的证据单元全文 [{id, kind, text}]。

    Returns:
        渲染好准备发送给 provider 的 prompt 字符串。"""
    context = {
        "dimension_name": dimension.get("name", dimension.get("code", "")),
        "dimension_anchors": _dimension_anchor_entries(dimension, scale_levels),
        "final_score": decision.final_score,
        "rationale": decision.rationale,
        "units": _units_by_ids(package, decision.unit_ids),
    }
    return render_template(template, context)
