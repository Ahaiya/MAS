"""
Mock Adjudicator — deterministic conflict resolution and final decision production.

For each ConflictRecord, produces an AdjudicationRecord with is_resolved=True
by selecting the winning hypothesis (lexicographically smallest hypothesis_id
among the conflicting set — deterministic without needing a real third rater).

For dimensions with no conflict, a FinalDimensionDecision is created directly
from the rater with the lexicographically smallest rater_id (deterministic).

Every dimension present in the input hypotheses receives exactly one
FinalDimensionDecision in the output.
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


def _resolve_conflict(
    conflict: ConflictRecord,
    hypotheses_by_id: Dict[str, ScoreHypothesis],
    policy: PolicySnapshot,
) -> Tuple[AdjudicationRecord, Optional[ScoreRepresentation], Optional[str]]:
    """Resolve a single conflict deterministically.

    Resolution strategy: pick the conflicting hypothesis with the
    lexicographically smallest hypothesis_id as the authoritative score.
    Returns (AdjudicationRecord, resolved_score, primary_hypothesis_id).
    """
    resolution_strategy = (
        policy.adjudication_policy
        .get("resolution_strategy", {})
        .get("default", "use_rater_3_as_authoritative")
    )

    # Gather the conflicting hypotheses; sort for determinism
    conflict_hyps = sorted(
        [hypotheses_by_id[hid] for hid in conflict.hypothesis_ids if hid in hypotheses_by_id],
        key=lambda h: h.hypothesis_id,
    )

    if not conflict_hyps:
        adj_id = f"adj-{_hid(f'empty:{conflict.conflict_id}')}"
        return (
            AdjudicationRecord(
                adjudication_id=adj_id,
                conflict_id=conflict.conflict_id,
                dimension_id=conflict.dimension_id,
                resolution_path=conflict.recommended_path,
                policy_rule_id=conflict.trigger_rule_id,
                resolved_score=None,
                resolver_hypothesis_id=None,
                resolution_note="No hypotheses found for conflict; unresolved.",
                is_resolved=False,
            ),
            None,
            None,
        )

    # Pick the winner (smallest hypothesis_id for determinism)
    winner = conflict_hyps[0]
    adj_id = f"adj-{_hid(f'{conflict.conflict_id}:{winner.hypothesis_id}')}"
    adj_record = AdjudicationRecord(
        adjudication_id=adj_id,
        conflict_id=conflict.conflict_id,
        dimension_id=conflict.dimension_id,
        resolution_path=ResolutionPath.THIRD_RATER,
        policy_rule_id=conflict.trigger_rule_id,
        resolved_score=winner.score,
        resolver_hypothesis_id=winner.hypothesis_id,
        resolution_note=f"Mock resolution via {resolution_strategy}; winner: {winner.rater_id}",
        is_resolved=True,
    )
    return adj_record, winner.score, winner.hypothesis_id


def run(
    conflicts: List[ConflictRecord],
    hypotheses: List[ScoreHypothesis],
    policy: PolicySnapshot,
) -> Tuple[List[AdjudicationRecord], List[FinalDimensionDecision]]:
    """Resolve conflicts and produce final dimension decisions.

    For every dimension represented in hypotheses:
    - If a conflict was adjudicated for that dimension, use the resolved score.
    - If no conflict, use the first rater's hypothesis (sorted by rater_id).

    Args:
        conflicts: ConflictRecords from the consistency checker.
        hypotheses: All ScoreHypotheses from the scoring stage.
        policy: PolicySnapshot supplying resolution strategy metadata.

    Returns:
        Tuple of (List[AdjudicationRecord], List[FinalDimensionDecision]).
    """
    hypotheses_by_id: Dict[str, ScoreHypothesis] = {h.hypothesis_id: h for h in hypotheses}
    conflicted_dims: Dict[str, ConflictRecord] = {c.dimension_id: c for c in conflicts}

    # Resolve each conflict
    adj_records: List[AdjudicationRecord] = []
    resolved_per_dim: Dict[str, Tuple[ScoreRepresentation, str, str]] = {}
    # Maps dimension_id -> (resolved_score, primary_hypothesis_id, adjudication_id)

    for conflict in conflicts:
        adj_record, resolved_score, primary_hyp_id = _resolve_conflict(
            conflict, hypotheses_by_id, policy
        )
        adj_records.append(adj_record)
        if adj_record.is_resolved and resolved_score is not None:
            resolved_per_dim[conflict.dimension_id] = (
                resolved_score,
                primary_hyp_id,
                adj_record.adjudication_id,
            )

    # Group hypotheses by dimension for non-conflicted dims
    by_dim: Dict[str, List[ScoreHypothesis]] = {}
    for h in hypotheses:
        by_dim.setdefault(h.dimension_id, []).append(h)

    # Build FinalDimensionDecision for every dimension
    decisions: List[FinalDimensionDecision] = []
    for dim_id, dim_hyps in by_dim.items():
        if dim_id in resolved_per_dim:
            final_score, primary_hyp_id, adj_id = resolved_per_dim[dim_id]
        else:
            # No conflict — pick the hypothesis with the lexicographically smallest rater_id
            winner = sorted(dim_hyps, key=lambda h: h.rater_id)[0]
            final_score = winner.score
            primary_hyp_id = winner.hypothesis_id
            adj_id = None

        # Gather evidence span IDs from all hypotheses for this dimension
        all_evidence_ids = list(
            {sid for h in dim_hyps for sid in h.evidence_span_ids}
        )
        all_descriptor_refs = list(
            {ref for h in dim_hyps for ref in h.descriptor_refs}
        )

        decision_id = f"dec-{_hid(f'{dim_id}:{primary_hyp_id}')}"
        decisions.append(
            FinalDimensionDecision(
                decision_id=decision_id,
                dimension_id=dim_id,
                final_score=final_score,
                primary_hypothesis_id=primary_hyp_id,
                adjudication_id=adj_id,
                evidence_span_ids=sorted(all_evidence_ids),
                descriptor_refs=sorted(all_descriptor_refs),
                decision_confidence=0.85,
                decision_note=None,
            )
        )

    return adj_records, decisions
