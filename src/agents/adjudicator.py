"""
裁决器 — 按 adjudication policy 的 use_rater_3_as_authoritative 策略处理冲突。

职责：
- 接收冲突记录（ConflictRecord）、全部评分假设（ScoreHypothesis，含 rater_3），
  以及策略快照（PolicySnapshot），产出 AdjudicationRecord 列表与
  FinalDimensionDecision 列表。

裁决规则（对应 ASAP Set 8）：
- 有冲突的维度：优先使用 resolution rater（默认 rater_3）的 ScoreHypothesis
  作为权威最终分数，is_resolved=True。
  若 rater_3 hypothesis 不存在（异常情况），标记 is_resolved=False，
  并以 rater_id 字典序最小者作为兜底，保证流水线不中断。
- 无冲突的维度：从 rater_id 字典序最小的 hypothesis 中取分
  （R1/R2 分数已收敛，任取等价）。

注意：resolution rater 标签从 policy.adjudication_policy.raters.resolution_rater_label
读取，零硬编码。所有 FinalDimensionDecision 的 descriptor_refs / evidence_span_ids
聚合自该维度的全部 hypothesis，不限评分者。
"""

from __future__ import annotations

import hashlib
from typing import Dict, List, Optional, Tuple

from src.contracts.artifact_bundle import PolicySnapshot
from src.contracts.score_representation import ScoreRepresentation
from src.contracts.scoring import (
    AdjudicationRecord,
    ConflictRecord,
    FinalDimensionDecision,
    ResolutionPath,
    ScoreHypothesis,
)


def _hid(seed: str, length: int = 12) -> str:
    return hashlib.md5(seed.encode()).hexdigest()[:length]


def _resolution_rater_label(policy: PolicySnapshot) -> str:
    """从 adjudication policy 读取 resolution rater 标签，默认 'rater_3'。"""
    return (
        policy.adjudication_policy
        .get("raters", {})
        .get("resolution_rater_label", "rater_3")
    )


def run(
    conflicts: List[ConflictRecord],
    hypotheses: List[ScoreHypothesis],
    policy: PolicySnapshot,
) -> Tuple[List[AdjudicationRecord], List[FinalDimensionDecision]]:
    """解决冲突并产出最终维度决定。

    对每个有冲突的维度，查找 resolution rater 的 hypothesis 并以其分数为权威。
    对无冲突的维度，直接从已收敛的 rater 中取分（字典序最小者）。

    Args:
        conflicts:   ConsistencyChecker 产出的 ConflictRecord 列表。
        hypotheses:  所有评分阶段产出的 ScoreHypothesis（含 rater_3）。
        policy:      PolicySnapshot，提供 resolution rater 标签等配置。

    Returns:
        (adj_records, decisions)：
        - adj_records：每条冲突对应一条 AdjudicationRecord。
        - decisions：每个维度对应一条 FinalDimensionDecision。
    """
    resolution_rater = _resolution_rater_label(policy)
    conflicted_dims: Dict[str, ConflictRecord] = {c.dimension_id: c for c in conflicts}

    # 按维度分组全部 hypothesis
    by_dim: Dict[str, List[ScoreHypothesis]] = {}
    for h in hypotheses:
        by_dim.setdefault(h.dimension_id, []).append(h)

    adj_records: List[AdjudicationRecord] = []
    decisions: List[FinalDimensionDecision] = []

    for dim_id, dim_hyps in sorted(by_dim.items()):
        all_evidence_ids = sorted({sid for h in dim_hyps for sid in h.evidence_span_ids})
        all_descriptor_refs = sorted({ref for h in dim_hyps for ref in h.descriptor_refs})

        if dim_id in conflicted_dims:
            conflict = conflicted_dims[dim_id]
            r3_hyps = [h for h in dim_hyps if h.rater_id == resolution_rater]

            if r3_hyps:
                # 正常路径：rater_3 存在，以其分数为权威
                winner = r3_hyps[0]
                adj_id = f"adj-{_hid(f'{conflict.conflict_id}:{winner.hypothesis_id}')}"
                adj = AdjudicationRecord(
                    adjudication_id=adj_id,
                    conflict_id=conflict.conflict_id,
                    dimension_id=dim_id,
                    resolution_path=ResolutionPath.THIRD_RATER,
                    policy_rule_id=conflict.trigger_rule_id,
                    resolved_score=winner.score,
                    resolver_hypothesis_id=winner.hypothesis_id,
                    resolution_note=(
                        f"{resolution_rater} 分数作为权威裁决结果。"
                    ),
                    is_resolved=True,
                )
                final_score = winner.score
                primary_hyp_id = winner.hypothesis_id
                confidence = winner.confidence
            else:
                # 兜底路径：rater_3 不存在（异常），标记未解决并升级人工复核。
                # resolution_path 显式设为 HUMAN_REVIEW，确保 route_after_adjudication
                # 路由到合法终止状态，而非触发非法状态转换 ADJUDICATED→ADJUDICATED。
                fallback = sorted(dim_hyps, key=lambda h: h.rater_id)[0]
                adj_id = f"adj-{_hid(f'{conflict.conflict_id}:fallback')}"
                adj = AdjudicationRecord(
                    adjudication_id=adj_id,
                    conflict_id=conflict.conflict_id,
                    dimension_id=dim_id,
                    resolution_path=ResolutionPath.HUMAN_REVIEW,
                    policy_rule_id=conflict.trigger_rule_id,
                    resolved_score=None,
                    resolver_hypothesis_id=None,
                    resolution_note=(
                        f"未找到 {resolution_rater} 的 hypothesis，裁决无法完成，"
                        f"升级人工复核。"
                    ),
                    is_resolved=False,
                )
                final_score = fallback.score
                primary_hyp_id = fallback.hypothesis_id
                confidence = 0.4

            adj_records.append(adj)
            decisions.append(
                FinalDimensionDecision(
                    decision_id=f"dec-{_hid(f'{dim_id}:{primary_hyp_id}')}",
                    dimension_id=dim_id,
                    final_score=final_score,
                    primary_hypothesis_id=primary_hyp_id,
                    adjudication_id=adj_id,
                    evidence_span_ids=all_evidence_ids,
                    descriptor_refs=all_descriptor_refs,
                    decision_confidence=confidence,
                    decision_note=None,
                )
            )

        else:
            # 无冲突：取 rater_id 字典序最小者（R1/R2 已收敛，任取等价）
            winner = sorted(dim_hyps, key=lambda h: h.rater_id)[0]
            decisions.append(
                FinalDimensionDecision(
                    decision_id=f"dec-{_hid(f'{dim_id}:{winner.hypothesis_id}')}",
                    dimension_id=dim_id,
                    final_score=winner.score,
                    primary_hypothesis_id=winner.hypothesis_id,
                    adjudication_id=None,
                    evidence_span_ids=all_evidence_ids,
                    descriptor_refs=all_descriptor_refs,
                    decision_confidence=winner.confidence,
                    decision_note=None,
                )
            )

    return adj_records, decisions
