"""
裁决触发判断：双链分数比较，纯计算无 LLM。

只有两类规则：score_distance（任一二级指标分差超阈值）与 adjacent_drift（多个
二级指标同向相邻漂移）。阈值、维度列表、分数值全部从 adjudication policy 配置读，
此处不硬编码业务值。

v1 那套面向通用 N-rater 的通用触发器调度已随旧流程删除——v2 恒定两条 Rater 链，
用不到它的通用性。"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from src.contracts.artifact_bundle import PolicySnapshot
from src.contracts.scoring import RaterChainResult


def _compare(operator: str, threshold: int, actual: int) -> bool:
    """评估来自 config 的比较运算符。"""
    if operator == ">":
        return actual > threshold
    if operator == ">=":
        return actual >= threshold
    if operator == "<":
        return actual < threshold
    if operator == "<=":
        return actual <= threshold
    if operator == "==":
        return actual == threshold
    if operator == "!=":
        return actual != threshold
    raise ValueError(f"Unknown operator: {operator}")


def _dimension_matches(dim_id: str, applies_to: List[str], exclusions: List[str]) -> bool:
    """检查某个维度是否适用于触发器。"""
    if dim_id in exclusions:
        return False
    if "*" in applies_to:
        return True
    return dim_id in applies_to


def _score_distance_triggered_dims(
    scores_a: Dict[str, int], scores_b: Dict[str, int], trigger: Dict[str, Any]
) -> set[str]:
    """任一二级指标分差满足 trigger 阈值条件时触发。"""
    operator = trigger.get("threshold", {}).get("operator", ">")
    threshold_value = int(trigger.get("threshold", {}).get("value", 1))
    applies_to = trigger.get("applies_to_dimensions", ["*"])
    exclusions = trigger.get("exclusions", [])

    triggered: set[str] = set()
    for dim_id in sorted(set(scores_a) & set(scores_b)):
        if not _dimension_matches(dim_id, applies_to, exclusions):
            continue
        diff = abs(scores_a[dim_id] - scores_b[dim_id])
        if _compare(operator, threshold_value, diff):
            triggered.add(dim_id)
    return triggered


def _adjacent_drift_triggered_dims(
    scores_a: Dict[str, int], scores_b: Dict[str, int], trigger: Dict[str, Any]
) -> set[str]:
    """≥min_matching_dimensions 个二级指标同向相邻漂移时触发。"""
    pattern = trigger.get("pattern", {})
    score_gap = int(pattern.get("score_gap", 1))
    min_matching_dimensions = int(pattern.get("min_matching_dimensions", 2))
    require_same_direction = bool(pattern.get("require_same_direction", False))
    applies_to = trigger.get("applies_to_dimensions", ["*"])
    exclusions = trigger.get("exclusions", [])

    if min_matching_dimensions <= 0 or score_gap <= 0:
        return set()

    matches: List[Tuple[str, int]] = []
    for dim_id in sorted(set(scores_a) & set(scores_b)):
        if not _dimension_matches(dim_id, applies_to, exclusions):
            continue
        signed_diff = scores_b[dim_id] - scores_a[dim_id]
        if abs(signed_diff) == score_gap:
            matches.append((dim_id, signed_diff))

    if require_same_direction:
        by_sign: Dict[int, List[str]] = {-1: [], 1: []}
        for dim_id, signed_diff in matches:
            by_sign[1 if signed_diff > 0 else -1].append(dim_id)
        triggered: set[str] = set()
        for dims in by_sign.values():
            if len(dims) >= min_matching_dimensions:
                triggered.update(dims)
        return triggered

    if len(matches) >= min_matching_dimensions:
        return {dim_id for dim_id, _ in matches}
    return set()


def needs_adjudication(
    chains_a: List[RaterChainResult],
    chains_b: List[RaterChainResult],
    policy: PolicySnapshot,
) -> set[str]:
    """纯函数：比较两条 Rater 链的分数，返回需要 Rater3 仲裁的 dimension_id 集合。

        阈值来自 ``policy.adjudication_policy["triggers"]`` 里的 score_distance
        与 adjacent_drift 两类触发器，此处不硬编码。

        Args:
            chains_a: rater_1（或任一方）在各二级指标上的 RaterChainResult。
            chains_b: rater_2（另一方）在各二级指标上的 RaterChainResult。
            policy  : 带有 adjudication_policy 的 PolicySnapshot。

        Returns:
            触发仲裁的 dimension_id 集合；未触发的维度视为一致（consensus）。"""
    scores_a = {c.dimension_id: c.score.score.canonical_score for c in chains_a}
    scores_b = {c.dimension_id: c.score.score.canonical_score for c in chains_b}

    triggered: set[str] = set()
    for trigger in policy.adjudication_policy.get("triggers", []):
        trigger_type = trigger.get("type", "")
        if trigger_type == "score_distance":
            triggered |= _score_distance_triggered_dims(scores_a, scores_b, trigger)
        elif trigger_type == "adjacent_drift":
            triggered |= _adjacent_drift_triggered_dims(scores_a, scores_b, trigger)
    return triggered
