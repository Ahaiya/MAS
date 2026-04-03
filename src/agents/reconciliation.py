"""
冲突协调 Agent，统一处理一致性检查、冲突判定与裁决收口。

Reconciliation Agent — unified conflict detection and score resolution.

This module merges the consistency-check and adjudication responsibilities into
a single policy-driven interface:

1) run()     -> detect conflicts and decide whether resolution scoring is needed
2) resolve() -> produce adjudication records + final per-dimension decisions

The runner can still execute these as two distinct pipeline nodes/states
(CONSISTENCY_CHECKED, ADJUDICATED) while sharing one implementation surface.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from src.contracts.artifact_bundle import PolicySnapshot
from src.contracts.score_representation import (
    ScoreRepresentation,
    create_score_representation,
)
from src.contracts.scoring import (
    AdjudicationRecord,
    ConflictRecord,
    FinalDimensionDecision,
    ResolutionPath,
    ScoreHypothesis,
)
from src.policies.adjudication import evaluate_all_triggers


def _hid(seed: str, length: int = 12) -> str:
    return hashlib.md5(seed.encode()).hexdigest()[:length]


def _resolution_rater_label(policy: PolicySnapshot) -> str:
    return (
        policy.adjudication_policy
        .get("raters", {})
        .get("resolution_rater_label", "rater_3")
    )


def _resolution_strategy(policy: PolicySnapshot) -> Dict[str, str]:
    raw = policy.adjudication_policy.get("resolution_strategy", {})
    return raw if isinstance(raw, dict) else {}


def _normalize_strategy_name(name: str) -> str:
    # Backward compatibility for pre-L stage policy naming.
    if name == "use_rater_3_as_authoritative":
        return "use_resolution_rater_as_authoritative"
    return name


def _normalize_scope(scope: str) -> str:
    if scope in {"all_dimensions", "conflicted_only"}:
        return scope
    return "all_dimensions"


def _round_half_up(value: float) -> int:
    return int(value + 0.5)


def _average_score(dim_hyps: List[ScoreHypothesis]) -> Tuple[ScoreRepresentation, float]:
    avg_score = sum(h.score.canonical_score for h in dim_hyps) / len(dim_hyps)
    canonical = _round_half_up(avg_score)
    scale_ref = dim_hyps[0].score.scale_ref
    return create_score_representation(canonical, scale_ref), avg_score


def _average_confidence(dim_hyps: List[ScoreHypothesis]) -> float:
    if not dim_hyps:
        return 0.0
    return sum(h.confidence for h in dim_hyps) / len(dim_hyps)


def _pick_default_hypothesis(dim_hyps: List[ScoreHypothesis]) -> ScoreHypothesis:
    # Deterministic fallback: smallest rater_id, then hypothesis_id.
    return sorted(dim_hyps, key=lambda h: (h.rater_id, h.hypothesis_id))[0]


def _pick_highest_hypothesis(dim_hyps: List[ScoreHypothesis]) -> ScoreHypothesis:
    # For optional fallback strategies such as "use_higher_score".
    return sorted(
        dim_hyps,
        key=lambda h: (-h.score.canonical_score, h.rater_id, h.hypothesis_id),
    )[0]


@dataclass(frozen=True)
class ReconciliationResult:
    conflicts: List[ConflictRecord]
    needs_resolution_scoring: bool
    resolution_dimension_ids: List[str]
    resolution_rater_label: str


def run(
    hypotheses: List[ScoreHypothesis],
    policy: PolicySnapshot,
) -> ReconciliationResult:
    """Detect conflicts and compute resolution scoring scope.

    Args:
        hypotheses: All current ScoreHypothesis records.
        policy: PolicySnapshot with adjudication triggers/strategy.
    """
    conflicts = evaluate_all_triggers(hypotheses, policy)

    strategy = _resolution_strategy(policy)
    default_strategy = _normalize_strategy_name(
        str(strategy.get("default", "use_resolution_rater_as_authoritative"))
    )
    re_score_scope = _normalize_scope(str(strategy.get("re_score_scope", "all_dimensions")))

    needs_resolution_scoring = bool(
        conflicts and default_strategy == "use_resolution_rater_as_authoritative"
    )

    if not needs_resolution_scoring:
        resolution_dimension_ids: List[str] = []
    elif re_score_scope == "conflicted_only":
        resolution_dimension_ids = sorted({c.dimension_id for c in conflicts})
    else:
        resolution_dimension_ids = sorted({h.dimension_id for h in hypotheses})

    return ReconciliationResult(
        conflicts=conflicts,
        needs_resolution_scoring=needs_resolution_scoring,
        resolution_dimension_ids=resolution_dimension_ids,
        resolution_rater_label=_resolution_rater_label(policy),
    )


def resolve(
    conflicts: List[ConflictRecord],
    hypotheses: List[ScoreHypothesis],
    policy: PolicySnapshot,
) -> Tuple[List[AdjudicationRecord], List[FinalDimensionDecision]]:
    """Resolve conflicts and build final per-dimension decisions."""
    resolution_rater = _resolution_rater_label(policy)
    strategy_cfg = _resolution_strategy(policy)
    default_strategy = _normalize_strategy_name(
        str(strategy_cfg.get("default", "use_resolution_rater_as_authoritative"))
    )
    fallback_strategy = _normalize_strategy_name(
        str(strategy_cfg.get("fallback_if_no_resolution", "average_of_raters"))
    )

    by_dim: Dict[str, List[ScoreHypothesis]] = {}
    for hyp in hypotheses:
        by_dim.setdefault(hyp.dimension_id, []).append(hyp)

    conflicts_by_dim: Dict[str, ConflictRecord] = {}
    for conflict in conflicts:
        conflicts_by_dim.setdefault(conflict.dimension_id, conflict)

    adj_records: List[AdjudicationRecord] = []
    decisions: List[FinalDimensionDecision] = []

    for dim_id, raw_hyps in sorted(by_dim.items()):
        dim_hyps = sorted(raw_hyps, key=lambda h: (h.rater_id, h.hypothesis_id))
        all_evidence_ids = sorted({sid for h in dim_hyps for sid in h.evidence_span_ids})
        all_descriptor_refs = sorted({ref for h in dim_hyps for ref in h.descriptor_refs})
        conflict = conflicts_by_dim.get(dim_id)

        if conflict is None:
            winner = _pick_default_hypothesis(dim_hyps)
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
                    decision_note=(
                        f"no conflict, {winner.rater_id} score used (raters converged)"
                    ),
                )
            )
            continue

        adj_id = f"adj-{_hid(f'{conflict.conflict_id}:{dim_id}')}"
        primary_hyp = _pick_default_hypothesis(dim_hyps)
        final_score: ScoreRepresentation = primary_hyp.score
        decision_confidence = primary_hyp.confidence
        decision_note = f"conflict unresolved, fallback to {primary_hyp.rater_id}"

        if default_strategy == "average_of_raters":
            final_score, _ = _average_score(dim_hyps)
            decision_confidence = _average_confidence(dim_hyps)
            decision_note = "conflict resolved via average_of_raters"
            adj = AdjudicationRecord(
                adjudication_id=adj_id,
                conflict_id=conflict.conflict_id,
                dimension_id=dim_id,
                resolution_path=ResolutionPath.POLICY_AVERAGE,
                policy_rule_id=conflict.trigger_rule_id,
                resolved_score=final_score,
                resolver_hypothesis_id=None,
                resolution_note="Resolved by averaging all available rater scores.",
                is_resolved=True,
            )
        else:
            # Default strategy: use resolution rater as authoritative.
            resolution_hyps = [
                hyp for hyp in dim_hyps if hyp.rater_id == resolution_rater
            ]
            if resolution_hyps:
                winner = sorted(resolution_hyps, key=lambda h: h.hypothesis_id)[0]
                primary_hyp = winner
                final_score = winner.score
                decision_confidence = winner.confidence
                decision_note = (
                    "conflict resolved via use_resolution_rater_as_authoritative, "
                    f"{resolution_rater} score used"
                )
                adj = AdjudicationRecord(
                    adjudication_id=adj_id,
                    conflict_id=conflict.conflict_id,
                    dimension_id=dim_id,
                    resolution_path=ResolutionPath.THIRD_RATER,
                    policy_rule_id=conflict.trigger_rule_id,
                    resolved_score=winner.score,
                    resolver_hypothesis_id=winner.hypothesis_id,
                    resolution_note=f"{resolution_rater} score used as authoritative.",
                    is_resolved=True,
                )
            elif fallback_strategy == "average_of_raters":
                final_score, _ = _average_score(dim_hyps)
                decision_confidence = _average_confidence(dim_hyps)
                decision_note = (
                    "conflict resolved via average_of_raters "
                    f"(fallback; missing {resolution_rater})"
                )
                adj = AdjudicationRecord(
                    adjudication_id=adj_id,
                    conflict_id=conflict.conflict_id,
                    dimension_id=dim_id,
                    resolution_path=ResolutionPath.POLICY_AVERAGE,
                    policy_rule_id=conflict.trigger_rule_id,
                    resolved_score=final_score,
                    resolver_hypothesis_id=None,
                    resolution_note=(
                        f"Missing {resolution_rater}; fallback strategy average_of_raters."
                    ),
                    is_resolved=True,
                )
            elif fallback_strategy == "use_higher_score":
                winner = _pick_highest_hypothesis(dim_hyps)
                primary_hyp = winner
                final_score = winner.score
                decision_confidence = winner.confidence
                decision_note = (
                    "conflict resolved via use_higher_score "
                    f"(fallback; missing {resolution_rater})"
                )
                adj = AdjudicationRecord(
                    adjudication_id=adj_id,
                    conflict_id=conflict.conflict_id,
                    dimension_id=dim_id,
                    resolution_path=ResolutionPath.POLICY_AVERAGE,
                    policy_rule_id=conflict.trigger_rule_id,
                    resolved_score=winner.score,
                    resolver_hypothesis_id=winner.hypothesis_id,
                    resolution_note=(
                        f"Missing {resolution_rater}; fallback strategy use_higher_score."
                    ),
                    is_resolved=True,
                )
            else:
                # Escalate unresolved, while still preserving a deterministic fallback
                # score so downstream consumers always receive one decision per dimension.
                decision_confidence = min(primary_hyp.confidence, 0.4)
                decision_note = (
                    f"conflict unresolved, fallback to {primary_hyp.rater_id}"
                )
                adj = AdjudicationRecord(
                    adjudication_id=adj_id,
                    conflict_id=conflict.conflict_id,
                    dimension_id=dim_id,
                    resolution_path=ResolutionPath.HUMAN_REVIEW,
                    policy_rule_id=conflict.trigger_rule_id,
                    resolved_score=None,
                    resolver_hypothesis_id=None,
                    resolution_note=(
                        f"Missing {resolution_rater}; unresolved by policy fallback "
                        f"'{fallback_strategy}'."
                    ),
                    is_resolved=False,
                )

        adj_records.append(adj)
        decisions.append(
            FinalDimensionDecision(
                decision_id=f"dec-{_hid(f'{dim_id}:{primary_hyp.hypothesis_id}')}",
                dimension_id=dim_id,
                final_score=final_score,
                primary_hypothesis_id=primary_hyp.hypothesis_id,
                adjudication_id=adj.adjudication_id,
                evidence_span_ids=all_evidence_ids,
                descriptor_refs=all_descriptor_refs,
                decision_confidence=decision_confidence,
                decision_note=decision_note,
            )
        )

    # Defensive guard: if a conflict references a dimension with no hypotheses,
    # preserve the unresolved adjudication signal for routing.
    for dim_id, conflict in sorted(conflicts_by_dim.items()):
        if dim_id in by_dim:
            continue
        adj_id = f"adj-{_hid(f'{conflict.conflict_id}:{dim_id}:missing')}"
        adj_records.append(
            AdjudicationRecord(
                adjudication_id=adj_id,
                conflict_id=conflict.conflict_id,
                dimension_id=dim_id,
                resolution_path=ResolutionPath.HUMAN_REVIEW,
                policy_rule_id=conflict.trigger_rule_id,
                resolved_score=None,
                resolver_hypothesis_id=None,
                resolution_note="Conflict dimension has no hypotheses; unresolved.",
                is_resolved=False,
            )
        )

    return adj_records, decisions
