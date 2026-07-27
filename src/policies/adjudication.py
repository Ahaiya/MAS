"""
裁决策略模块，负责按配置评估冲突触发器并生成冲突记录。

Adjudication Policy — 配置驱动的触发器评估。

从 PolicySnapshot 配置中评估裁决触发器。
支持三种触发器类型：

- score_distance: 当 |score1 - score2| 超过阈值时发生冲突。
- pattern_match: 当跨维度分数模式匹配时发生冲突
  （例如，cusp rule）。
- adjacent_drift: 当相邻分歧在多个维度上沿一致方向累积时发生冲突。

所有阈值、模式、维度列表和分数值均从配置中读取
—— 此处没有硬编码任何业务逻辑值。"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Tuple

from src.contracts.artifact_bundle import PolicySnapshot
from src.contracts.scoring import (
    ConflictRecord,
    ConflictType,
    RaterChainResult,
    ResolutionPath,
    ScoreHypothesis,
)


def _hid(seed: str, length: int = 12) -> str:
    return hashlib.md5(seed.encode()).hexdigest()[:length]


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


def _action_to_resolution_path(action: str) -> ResolutionPath:
    """将 config 动作字符串映射到 ResolutionPath。"""
    mapping = {
        "invoke_resolution": ResolutionPath.THIRD_RATER,
        "re_extract": ResolutionPath.RE_EXTRACT,
        "re_score": ResolutionPath.RE_SCORE,
        "human_review": ResolutionPath.HUMAN_REVIEW,
    }
    return mapping.get(action, ResolutionPath.THIRD_RATER)


def _dimension_matches(
    dim_id: str, applies_to: List[str], exclusions: List[str]
) -> bool:
    """检查某个维度是否适用于触发器。"""
    if dim_id in exclusions:
        return False
    if "*" in applies_to:
        return True
    return dim_id in applies_to


def _group_hypotheses_by_rater(
    hypotheses: List[ScoreHypothesis],
) -> Dict[str, Dict[str, ScoreHypothesis]]:
    """按 rater_id 然后 dimension_id 对假设进行分组。"""
    by_rater: Dict[str, Dict[str, ScoreHypothesis]] = {}
    for hyp in hypotheses:
        by_rater.setdefault(hyp.rater_id, {})[hyp.dimension_id] = hyp
    return by_rater


# ── 分数差距触发器 ────────────────────────────────────────────────────────────


def evaluate_score_distance_trigger(
    trigger: Dict[str, Any],
    hypotheses_by_dim: Dict[str, List[ScoreHypothesis]],
) -> List[ConflictRecord]:
    """针对假设评估 score_distance 触发器。
    
        对于每个适用的维度，检查所有假设对。
        如果 |score1 - score2| 违反阈值条件，则生成一个
        ConflictRecord。"""
    trigger_id = trigger.get("trigger_id", "unknown_trigger")
    applies_to = trigger.get("applies_to_dimensions", ["*"])
    exclusions = trigger.get("exclusions", [])
    threshold_config = trigger.get("threshold", {})
    operator = threshold_config.get("operator", ">")
    threshold_value = int(threshold_config.get("value", 1))
    action = trigger.get("action", "invoke_resolution")
    resolution_path = _action_to_resolution_path(action)

    conflicts: List[ConflictRecord] = []

    for dim_id, dim_hyps in sorted(hypotheses_by_dim.items()):
        if not _dimension_matches(dim_id, applies_to, exclusions):
            continue

        sorted_hyps = sorted(dim_hyps, key=lambda h: h.hypothesis_id)
        for i in range(len(sorted_hyps)):
            for j in range(i + 1, len(sorted_hyps)):
                h1, h2 = sorted_hyps[i], sorted_hyps[j]
                diff = abs(h1.score.canonical_score - h2.score.canonical_score)
                if _compare(operator, threshold_value, diff):
                    conflict_id = (
                        f"conflict-{_hid(f'{h1.hypothesis_id}:{h2.hypothesis_id}')}"
                    )
                    conflicts.append(
                        ConflictRecord(
                            conflict_id=conflict_id,
                            dimension_id=dim_id,
                            hypothesis_ids=[h1.hypothesis_id, h2.hypothesis_id],
                            conflict_type=ConflictType.NON_ADJACENT,
                            trigger_rule_id=trigger_id,
                            conflict_detail=(
                                f"Score diff {diff} {operator} {threshold_value}: "
                                f"{h1.rater_id}={h1.score.canonical_score} vs "
                                f"{h2.rater_id}={h2.score.canonical_score}"
                            ),
                            recommended_path=resolution_path,
                        )
                    )

    return conflicts


# ── 模式匹配（尖点）触发器 ────────────────────────────────────────────────────


def _check_cusp_pattern(
    scores_a: List[int],
    scores_b: List[int],
    expected_all: List[int],
    check_one_minus_one: bool,
) -> bool:
    """检查 (scores_a, scores_b) 或反之是否匹配 cusp pattern。
    
        一个评分者必须完全匹配 expected_all。
        如果 check_one_minus_one 为 True，另一个评分者必须恰好有
        一个分数比对应的期望值小 1，
        且所有其他分数均与期望匹配。
    
        所有分数值均来自 config —— 此处没有任何硬编码。"""

    def matches_expected(scores: List[int]) -> bool:
        return scores == expected_all

    def matches_cusp_variant(scores: List[int]) -> bool:
        if not check_one_minus_one:
            return False
        diffs = [e - s for s, e in zip(scores, expected_all)]
        return diffs.count(1) == 1 and diffs.count(0) == len(diffs) - 1

    return (matches_expected(scores_a) and matches_cusp_variant(scores_b)) or (
        matches_expected(scores_b) and matches_cusp_variant(scores_a)
    )


def evaluate_pattern_match_trigger(
    trigger: Dict[str, Any],
    hypotheses: List[ScoreHypothesis],
) -> List[ConflictRecord]:
    """针对假设评估 pattern_match (cusp) 触发器。
    
        检查两个评分者在指定维度上的分数是否匹配
        配置的跨维度模式。"""
    trigger_id = trigger.get("trigger_id", "unknown_trigger")
    applies_to = trigger.get("applies_to_dimensions", [])
    exclusions = trigger.get("exclusions", [])
    pattern = trigger.get("pattern", {})
    expected_all_scores = pattern.get("one_rater_all_scores", [])
    check_one_minus_one = pattern.get("other_rater_has_one_3_and_three_4s", False)
    action = trigger.get("action", "invoke_resolution")
    resolution_path = _action_to_resolution_path(action)

    if not applies_to or not expected_all_scores:
        return []

    applicable_dims = [d for d in applies_to if d not in exclusions]
    if len(applicable_dims) != len(expected_all_scores):
        return []

    by_rater = _group_hypotheses_by_rater(hypotheses)

    rater_ids = sorted(by_rater.keys())
    if len(rater_ids) < 2:
        return []

    conflicts: List[ConflictRecord] = []

    for ri in range(len(rater_ids)):
        for rj in range(ri + 1, len(rater_ids)):
            r_a, r_b = rater_ids[ri], rater_ids[rj]

            scores_a: List[int] = []
            scores_b: List[int] = []
            hyps_a: List[ScoreHypothesis] = []
            hyps_b: List[ScoreHypothesis] = []
            complete = True

            for dim_id in applicable_dims:
                ha = by_rater.get(r_a, {}).get(dim_id)
                hb = by_rater.get(r_b, {}).get(dim_id)
                if ha is None or hb is None:
                    complete = False
                    break
                scores_a.append(ha.score.canonical_score)
                scores_b.append(hb.score.canonical_score)
                hyps_a.append(ha)
                hyps_b.append(hb)

            if not complete:
                continue

            if _check_cusp_pattern(
                scores_a, scores_b, expected_all_scores, check_one_minus_one
            ):
                for idx, dim_id in enumerate(applicable_dims):
                    ha = hyps_a[idx]
                    hb = hyps_b[idx]
                    conflict_id = (
                        f"conflict-cusp-"
                        f"{_hid(f'{trigger_id}:{r_a}:{r_b}:{dim_id}')}"
                    )
                    conflicts.append(
                        ConflictRecord(
                            conflict_id=conflict_id,
                            dimension_id=dim_id,
                            hypothesis_ids=[ha.hypothesis_id, hb.hypothesis_id],
                            conflict_type=ConflictType.CUSP,
                            trigger_rule_id=trigger_id,
                            conflict_detail=(
                                f"Cusp pattern: {r_a}={scores_a} vs "
                                f"{r_b}={scores_b} on {applicable_dims}"
                            ),
                            recommended_path=resolution_path,
                        )
                    )

    return conflicts


# ── 相邻漂移触发器 ────────────────────────────────────────────────────────────


def _emit_adjacent_drift_conflicts(
    trigger_id: str,
    resolution_path: ResolutionPath,
    r_a: str,
    r_b: str,
    drifted_dims: List[Tuple[str, ScoreHypothesis, ScoreHypothesis, int]],
) -> List[ConflictRecord]:
    """为符合条件的漂移模式按每个漂移维度构建一个冲突。"""
    direction = "higher" if drifted_dims[0][3] > 0 else "lower"
    matched_dims = [dim_id for dim_id, *_ in drifted_dims]
    conflict_detail = (
        f"Systematic adjacent drift: {r_b} is {direction} than {r_a} on "
        f"{len(matched_dims)} dimensions {matched_dims}"
    )

    conflicts: List[ConflictRecord] = []
    for dim_id, ha, hb, _ in drifted_dims:
        conflict_id = f"conflict-adjacent-{_hid(f'{trigger_id}:{r_a}:{r_b}:{dim_id}')}"
        conflicts.append(
            ConflictRecord(
                conflict_id=conflict_id,
                dimension_id=dim_id,
                hypothesis_ids=[ha.hypothesis_id, hb.hypothesis_id],
                conflict_type=ConflictType.ADJACENT_DRIFT,
                trigger_rule_id=trigger_id,
                conflict_detail=conflict_detail,
                recommended_path=resolution_path,
            )
        )
    return conflicts


def evaluate_adjacent_drift_trigger(
    trigger: Dict[str, Any],
    hypotheses: List[ScoreHypothesis],
) -> List[ConflictRecord]:
    """评估跨多个维度的相邻一分漂移。
    
        当一对评分者在配置的维度上具有足够的完全相邻
        分歧时，触发器匹配。当启用
        ``require_same_direction`` 时，匹配的分歧必须
        在生成的组内全部指向同一方向。"""
    trigger_id = trigger.get("trigger_id", "unknown_trigger")
    applies_to = trigger.get("applies_to_dimensions", ["*"])
    exclusions = trigger.get("exclusions", [])
    pattern = trigger.get("pattern", {})
    score_gap = int(pattern.get("score_gap", 1))
    min_matching_dimensions = int(pattern.get("min_matching_dimensions", 2))
    require_same_direction = bool(pattern.get("require_same_direction", False))
    action = trigger.get("action", "invoke_resolution")
    resolution_path = _action_to_resolution_path(action)

    if min_matching_dimensions <= 0 or score_gap <= 0:
        return []

    by_rater = _group_hypotheses_by_rater(hypotheses)
    rater_ids = sorted(by_rater.keys())
    if len(rater_ids) < 2:
        return []

    conflicts: List[ConflictRecord] = []
    for ri in range(len(rater_ids)):
        for rj in range(ri + 1, len(rater_ids)):
            r_a, r_b = rater_ids[ri], rater_ids[rj]
            adjacent_matches: List[Tuple[str, ScoreHypothesis, ScoreHypothesis, int]] = []

            candidate_dims = sorted(
                {
                    dim_id
                    for dim_id in set(by_rater.get(r_a, {})) | set(by_rater.get(r_b, {}))
                    if _dimension_matches(dim_id, applies_to, exclusions)
                }
            )
            for dim_id in candidate_dims:
                ha = by_rater.get(r_a, {}).get(dim_id)
                hb = by_rater.get(r_b, {}).get(dim_id)
                if ha is None or hb is None:
                    continue
                signed_diff = hb.score.canonical_score - ha.score.canonical_score
                if abs(signed_diff) == score_gap:
                    adjacent_matches.append((dim_id, ha, hb, signed_diff))

            if require_same_direction:
                grouped: Dict[int, List[Tuple[str, ScoreHypothesis, ScoreHypothesis, int]]] = {
                    -1: [],
                    1: [],
                }
                for match in adjacent_matches:
                    sign = 1 if match[3] > 0 else -1
                    grouped[sign].append(match)
                for sign in (-1, 1):
                    drifted_dims = grouped[sign]
                    if len(drifted_dims) >= min_matching_dimensions:
                        conflicts.extend(
                            _emit_adjacent_drift_conflicts(
                                trigger_id,
                                resolution_path,
                                r_a,
                                r_b,
                                drifted_dims,
                            )
                        )
                continue

            if len(adjacent_matches) >= min_matching_dimensions:
                conflicts.extend(
                    _emit_adjacent_drift_conflicts(
                        trigger_id,
                        resolution_path,
                        r_a,
                        r_b,
                        adjacent_matches,
                    )
                )

    return conflicts


# ── 评估所有触发器 ────────────────────────────────────────────────────────────


def evaluate_all_triggers(
    hypotheses: List[ScoreHypothesis],
    policy: PolicySnapshot,
) -> List[ConflictRecord]:
    """评估所有配置的裁决触发器。
    
        从 ``policy.adjudication_policy["triggers"]`` 读取触发器，并
        按类型评估每个触发器。通过 conflict_id 和
        dimension+hypothesis 对进行去重，以便在
        多条规则匹配同一分歧时，优先级最高的触发器胜出。
    
        Args:
            hypotheses: 来自评分阶段的所有 ScoreHypotheses。
            policy: 包含触发器定义的 PolicySnapshot。
    
        Returns:
            去重后的 ConflictRecord 对象列表。"""
    triggers = policy.adjudication_policy.get("triggers", [])
    sorted_triggers = sorted(triggers, key=lambda t: t.get("priority", 0))

    by_dim: Dict[str, List[ScoreHypothesis]] = {}
    for h in hypotheses:
        by_dim.setdefault(h.dimension_id, []).append(h)

    all_conflicts: List[ConflictRecord] = []
    seen_ids: set[str] = set()
    seen_signatures: set[Tuple[str, Tuple[str, ...]]] = set()

    for trigger in sorted_triggers:
        trigger_type = trigger.get("type", "")

        if trigger_type == "score_distance":
            new_conflicts = evaluate_score_distance_trigger(trigger, by_dim)
        elif trigger_type == "pattern_match":
            new_conflicts = evaluate_pattern_match_trigger(trigger, hypotheses)
        elif trigger_type == "adjacent_drift":
            new_conflicts = evaluate_adjacent_drift_trigger(trigger, hypotheses)
        else:
            continue

        for c in new_conflicts:
            signature = (c.dimension_id, tuple(sorted(c.hypothesis_ids)))
            if c.conflict_id in seen_ids or signature in seen_signatures:
                continue
            seen_ids.add(c.conflict_id)
            seen_signatures.add(signature)
            all_conflicts.append(c)

    return all_conflicts


# ═══════════════════════════════════════════════════════════════════════════
# v2 —— 双链仲裁触发判断（纯函数），与上方 v1 ConflictRecord 机制并存。
#
# v2 只有恰好两条 Rater 链，且只关心 score_distance 与 adjacent_drift 两类规则
# （不含 v1 的 pattern_match/cusp）——比 evaluate_all_triggers 的通用 N-rater
# 调度更窄，因此不复用它，但复用同一份 adjudication policy 配置里的阈值，以及
# 上面已有的 _compare/_dimension_matches 纯计算辅助函数。
# ═══════════════════════════════════════════════════════════════════════════


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

        阈值来自 ``policy.adjudication_policy["triggers"]`` 里的 score_distance /
        adjacent_drift 触发器——与 v1 复用同一份 adjudication policy 配置，
        pattern_match 类触发器不适用于双链场景，忽略。

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
