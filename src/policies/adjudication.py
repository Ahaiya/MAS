"""
裁决策略模块，负责按配置评估冲突触发器并生成冲突记录。

Adjudication Policy — config-driven trigger evaluation.

Evaluates adjudication triggers from PolicySnapshot configuration.
Supports three trigger types:

- score_distance: Conflict when |score1 - score2| exceeds a threshold.
- pattern_match: Conflict when cross-dimension score patterns match
  (e.g., cusp rule).
- adjacent_drift: Conflict when adjacent disagreements accumulate across
  multiple dimensions in a consistent direction.

All thresholds, patterns, dimension lists, and score values are read
from config — no business logic values are hardcoded here.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Tuple

from src.contracts.artifact_bundle import PolicySnapshot
from src.contracts.scoring import (
    ConflictRecord,
    ConflictType,
    ResolutionPath,
    ScoreHypothesis,
)


def _hid(seed: str, length: int = 12) -> str:
    return hashlib.md5(seed.encode()).hexdigest()[:length]


def _compare(operator: str, threshold: int, actual: int) -> bool:
    """Evaluate a comparison operator from config."""
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
    """Map a config action string to a ResolutionPath."""
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
    """Check if a dimension is applicable for a trigger."""
    if dim_id in exclusions:
        return False
    if "*" in applies_to:
        return True
    return dim_id in applies_to


def _group_hypotheses_by_rater(
    hypotheses: List[ScoreHypothesis],
) -> Dict[str, Dict[str, ScoreHypothesis]]:
    """Group hypotheses by rater_id then dimension_id."""
    by_rater: Dict[str, Dict[str, ScoreHypothesis]] = {}
    for hyp in hypotheses:
        by_rater.setdefault(hyp.rater_id, {})[hyp.dimension_id] = hyp
    return by_rater


# ── Score Distance Trigger ────────────────────────────────────────────────────


def evaluate_score_distance_trigger(
    trigger: Dict[str, Any],
    hypotheses_by_dim: Dict[str, List[ScoreHypothesis]],
) -> List[ConflictRecord]:
    """Evaluate a score_distance trigger against hypotheses.

    For each applicable dimension, checks all pairs of hypotheses.
    If |score1 - score2| violates the threshold condition, emits a
    ConflictRecord.
    """
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


# ── Pattern Match (Cusp) Trigger ──────────────────────────────────────────────


def _check_cusp_pattern(
    scores_a: List[int],
    scores_b: List[int],
    expected_all: List[int],
    check_one_minus_one: bool,
) -> bool:
    """Check if (scores_a, scores_b) or vice versa matches the cusp pattern.

    One rater must match expected_all exactly.
    If check_one_minus_one is True, the other rater must have exactly
    one score that's 1 less than the corresponding expected value,
    with all other scores matching expected.

    All score values come from config — none are hardcoded here.
    """

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
    """Evaluate a pattern_match (cusp) trigger against hypotheses.

    Checks if two raters' scores across specified dimensions match a
    configured cross-dimension pattern.
    """
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


# ── Adjacent Drift Trigger ────────────────────────────────────────────────────


def _emit_adjacent_drift_conflicts(
    trigger_id: str,
    resolution_path: ResolutionPath,
    r_a: str,
    r_b: str,
    drifted_dims: List[Tuple[str, ScoreHypothesis, ScoreHypothesis, int]],
) -> List[ConflictRecord]:
    """Build one conflict per drifted dimension for a qualifying drift pattern."""
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
    """Evaluate adjacent one-point drift across multiple dimensions.

    A trigger matches when a pair of raters has enough exactly-adjacent
    disagreements across the configured dimensions. When
    ``require_same_direction`` is enabled, the matched disagreements must
    all point in the same direction within the emitted group.
    """
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


# ── Evaluate All Triggers ─────────────────────────────────────────────────────


def evaluate_all_triggers(
    hypotheses: List[ScoreHypothesis],
    policy: PolicySnapshot,
) -> List[ConflictRecord]:
    """Evaluate all configured adjudication triggers.

    Reads triggers from ``policy.adjudication_policy["triggers"]`` and
    evaluates each by type. Deduplicates by conflict_id and by
    dimension+hypothesis pair so the highest-priority trigger wins when
    multiple rules match the same disagreement.

    Args:
        hypotheses: All ScoreHypotheses from the scoring stage.
        policy: PolicySnapshot containing trigger definitions.

    Returns:
        Deduplicated list of ConflictRecord objects.
    """
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
