"""
流水线导出模块，负责把最终决策整理成稳定的结构化输出。

Pipeline Export — 结构化输出组装。

显式地将 trait 级别的维度输出与可选的
聚合指标输出分离。消费者（例如 feedback assembler、evaluation
harness）应参考此模块以访问规范的输出格式。

Design:
- build_pipeline_output() 接收 FinalDimensionDecision[] 和一个可选的
  CompositeDecision，并返回一个带有三个顶级键的普通 dict：
    * "trait_scores"    — 每个维度分数条目的列表
    * "indicator_score" — 聚合指标分数负载，或 None
    * "composite"       — indicator_score 的兼容别名，或 None
- 这里不包含业务逻辑或聚合公式；此模块仅
  根据已计算的结果组装输出结构。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.contracts.scoring import CompositeDecision, FinalDimensionDecision


def build_indicator_score_payload(
    composite: Optional[CompositeDecision],
    *,
    bundle_metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """使用以指标为中心的命名方式序列化聚合分数。
    
        `composite` 仍然是内部契约类型，但对于工程
        评估，面向用户的含义是“所选指标分数”
        （例如，跨越其观察点的 A4 聚合分数）。"""
    if composite is None:
        return None

    payload = composite.to_dict()
    metadata = bundle_metadata if isinstance(bundle_metadata, dict) else {}
    raw_indicator_ids = metadata.get("selected_indicator_ids")
    indicator_ids = [
        str(item).strip()
        for item in (raw_indicator_ids if isinstance(raw_indicator_ids, list) else [])
        if str(item).strip()
    ]
    active_task_id = metadata.get("active_task_id")
    if len(indicator_ids) == 1:
        display_label = f"{indicator_ids[0]} 聚合分"
    else:
        display_label = "聚合指标得分"

    payload.update(
        {
            "score_kind": "indicator_score",
            "score_scope": "selected_indicator",
            "indicator_ids": indicator_ids,
            "display_label": display_label,
        }
    )
    if len(indicator_ids) == 1:
        payload["indicator_id"] = indicator_ids[0]
    if isinstance(active_task_id, str) and active_task_id.strip():
        payload["task_id"] = active_task_id.strip()
    return payload


def build_pipeline_output(
    decisions: List[FinalDimensionDecision],
    composite: Optional[CompositeDecision],
    *,
    bundle_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """组装最终的流水线输出 dict。
    
        Args:
            decisions: 权威的维度级决策。
            composite: 可选的聚合 composite 分数。
    
        Returns:
            包含以下内容的 dict：
                "trait_scores": 每个维度分数 dict 的列表
                "indicator_score": 以指标为中心的聚合负载或 None
                "composite": 面向旧版读者的兼容别名"""
    trait_scores = [
        {
            "dimension_id": d.dimension_id,
            "decision_id": d.decision_id,
            "canonical_score": d.final_score.canonical_score,
            "display_score": d.final_score.display_score,
            "scale_ref": d.final_score.scale_ref,
            "adjudication_id": d.adjudication_id,
            "decision_confidence": d.decision_confidence,
            "descriptor_refs": list(d.descriptor_refs),
            "evidence_span_ids": list(d.evidence_span_ids),
        }
        for d in decisions
    ]
    indicator_score = build_indicator_score_payload(
        composite,
        bundle_metadata=bundle_metadata,
    )

    return {
        "trait_scores": trait_scores,
        "indicator_score": indicator_score,
        "composite": indicator_score,
    }
