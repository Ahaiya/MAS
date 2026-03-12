"""
Scoring and Adjudication Contracts

Defines the intermediate data shapes for the multi-rater scoring, conflict
detection, adjudication, and final decision stages:

  DimensionObservation[]  -> ScoreHypothesis[]  (one per rater per dimension)
  ScoreHypothesis[]       -> ConflictRecord[]   (consistency checker output)
  ConflictRecord[]        -> AdjudicationRecord[] (adjudicator output)
  AdjudicationRecord[]    -> FinalDimensionDecision[] (one per dimension)
  FinalDimensionDecision[] -> CompositeDecision (optional, aggregation policy)

Design invariants:
- All models are frozen (immutable) dataclasses.
- dimension_id, descriptor_refs, rater_id, policy_rule_id are all opaque strings
  sourced from rubric or policy config artifacts. No hardcoded trait names or
  adjudication thresholds appear here.
- ScoreHypothesis.score uses ScoreRepresentation — the actual valid range is
  enforced by the rubric config scale, not by this contract.
- confidence is a float in [0.0, 1.0].
- from_dict() rejects unknown keys (strict schema enforcement).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from src.contracts.score_representation import ScoreRepresentation


# ── Strict deserialization helper ──────────────────────────────────────────────


def _check_no_extra(data: Dict[str, Any], allowed: frozenset, cls_name: str) -> None:
    extra = set(data) - allowed
    if extra:
        raise TypeError(
            f"{cls_name}.from_dict() received unexpected fields: {sorted(extra)}"
        )


# ── ConflictType ───────────────────────────────────────────────────────────────


class ConflictType(str, Enum):
    """Type of scoring conflict detected by the Consistency Checker.

    NON_ADJACENT – scores differ by more than allowed adjacent range (from policy).
    CUSP         – at least one score is on a policy-defined boundary between levels.
    OTHER        – other policy-defined conflict type.
    """

    NON_ADJACENT = "non_adjacent"
    CUSP = "cusp"
    OTHER = "other"


# ── ResolutionPath ─────────────────────────────────────────────────────────────


class ResolutionPath(str, Enum):
    """Resolution path recommended or taken for a ConflictRecord.

    THIRD_RATER    – invoke a third scoring pass to break the tie.
    RE_EXTRACT     – re-run evidence extraction before re-scoring.
    RE_SCORE       – re-run scoring without new extraction.
    POLICY_AVERAGE – resolve by policy-defined averaging formula.
    HUMAN_REVIEW   – escalate to human reviewer (unresolvable automatically).
    """

    THIRD_RATER = "third_rater"
    RE_EXTRACT = "re_extract"
    RE_SCORE = "re_score"
    POLICY_AVERAGE = "policy_average"
    HUMAN_REVIEW = "human_review"


# ── ScoreHypothesis ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScoreHypothesis:
    """A single rater's score proposal for one dimension.

    Scoring subagents produce one ScoreHypothesis per (rater, dimension) pair.
    Each hypothesis must reference the descriptor levels and evidence spans
    that justify the proposed score — unsupported hypotheses are invalid.

    Attributes:
        hypothesis_id: Unique ID for this score proposal within the run.
        observation_id: The DimensionObservation this hypothesis is based on.
        dimension_id: Opaque dimension identifier from rubric config.
        rater_id: Identifies which scoring agent produced this hypothesis.
        score: ScoreRepresentation with canonical_score and scale_ref.
        descriptor_refs: Rubric descriptor level references that justify the score.
        evidence_span_ids: EvidenceSpan IDs cited as rationale.
        rationale: Free-text scoring rationale (for audit trail).
        confidence: Rater confidence in [0.0, 1.0].
    """

    hypothesis_id: str
    observation_id: str
    dimension_id: str
    rater_id: str
    score: ScoreRepresentation
    descriptor_refs: List[str]
    evidence_span_ids: List[str]
    rationale: str
    confidence: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"ScoreHypothesis '{self.hypothesis_id}': confidence must be in "
                f"[0.0, 1.0], got {self.confidence}."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "observation_id": self.observation_id,
            "dimension_id": self.dimension_id,
            "rater_id": self.rater_id,
            "score": self.score.to_dict(),
            "descriptor_refs": list(self.descriptor_refs),
            "evidence_span_ids": list(self.evidence_span_ids),
            "rationale": self.rationale,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ScoreHypothesis:
        _check_no_extra(
            data,
            frozenset({
                "hypothesis_id", "observation_id", "dimension_id", "rater_id",
                "score", "descriptor_refs", "evidence_span_ids", "rationale",
                "confidence",
            }),
            "ScoreHypothesis",
        )
        return cls(
            hypothesis_id=data["hypothesis_id"],
            observation_id=data["observation_id"],
            dimension_id=data["dimension_id"],
            rater_id=data["rater_id"],
            score=ScoreRepresentation.from_dict(data["score"]),
            descriptor_refs=list(data.get("descriptor_refs") or []),
            evidence_span_ids=list(data.get("evidence_span_ids") or []),
            rationale=data["rationale"],
            confidence=data["confidence"],
        )


# ── ConflictRecord ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConflictRecord:
    """A detected scoring disagreement between two or more ScoreHypotheses.

    Produced by the Consistency Checker when adjudication policy triggers are met
    (e.g., non-adjacent scores, cusp condition). The recommended_path field
    suggests which ResolutionPath the orchestrator should take.

    Attributes:
        conflict_id: Unique ID for this conflict within the run.
        dimension_id: Opaque dimension identifier from rubric config.
        hypothesis_ids: IDs of the conflicting ScoreHypotheses.
        conflict_type: Category of conflict (from policy definitions).
        trigger_rule_id: Adjudication policy rule that triggered this record.
        conflict_detail: Human-readable description of the conflict.
        recommended_path: Suggested resolution path from policy evaluation.
    """

    conflict_id: str
    dimension_id: str
    hypothesis_ids: List[str]
    conflict_type: ConflictType
    trigger_rule_id: str
    conflict_detail: str
    recommended_path: ResolutionPath

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "dimension_id": self.dimension_id,
            "hypothesis_ids": list(self.hypothesis_ids),
            "conflict_type": self.conflict_type.value,
            "trigger_rule_id": self.trigger_rule_id,
            "conflict_detail": self.conflict_detail,
            "recommended_path": self.recommended_path.value,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ConflictRecord:
        _check_no_extra(
            data,
            frozenset({
                "conflict_id", "dimension_id", "hypothesis_ids", "conflict_type",
                "trigger_rule_id", "conflict_detail", "recommended_path",
            }),
            "ConflictRecord",
        )
        return cls(
            conflict_id=data["conflict_id"],
            dimension_id=data["dimension_id"],
            hypothesis_ids=list(data.get("hypothesis_ids") or []),
            conflict_type=ConflictType(data["conflict_type"]),
            trigger_rule_id=data["trigger_rule_id"],
            conflict_detail=data["conflict_detail"],
            recommended_path=ResolutionPath(data["recommended_path"]),
        )


# ── AdjudicationRecord ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AdjudicationRecord:
    """The outcome of applying an adjudication policy to a ConflictRecord.

    Produced by the Adjudicator. If the conflict could not be resolved
    automatically (is_resolved=False), resolved_score will be None and the
    orchestrator routes to human review.

    Attributes:
        adjudication_id: Unique ID for this adjudication attempt.
        conflict_id: The ConflictRecord that triggered this adjudication.
        dimension_id: Opaque dimension identifier from rubric config.
        resolution_path: The path that was actually taken.
        policy_rule_id: Policy rule that governed the resolution.
        resolved_score: The adjudicated score. None if unresolved.
        resolver_hypothesis_id: If a third-rater resolved it, their hypothesis ID.
        resolution_note: Optional explanation of the resolution decision.
        is_resolved: True if a final score was produced; False if escalated.
    """

    adjudication_id: str
    conflict_id: str
    dimension_id: str
    resolution_path: ResolutionPath
    policy_rule_id: str
    resolved_score: Optional[ScoreRepresentation]
    resolver_hypothesis_id: Optional[str]
    resolution_note: Optional[str]
    is_resolved: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "adjudication_id": self.adjudication_id,
            "conflict_id": self.conflict_id,
            "dimension_id": self.dimension_id,
            "resolution_path": self.resolution_path.value,
            "policy_rule_id": self.policy_rule_id,
            "resolved_score": self.resolved_score.to_dict() if self.resolved_score else None,
            "resolver_hypothesis_id": self.resolver_hypothesis_id,
            "resolution_note": self.resolution_note,
            "is_resolved": self.is_resolved,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AdjudicationRecord:
        _check_no_extra(
            data,
            frozenset({
                "adjudication_id", "conflict_id", "dimension_id", "resolution_path",
                "policy_rule_id", "resolved_score", "resolver_hypothesis_id",
                "resolution_note", "is_resolved",
            }),
            "AdjudicationRecord",
        )
        raw_score = data.get("resolved_score")
        return cls(
            adjudication_id=data["adjudication_id"],
            conflict_id=data["conflict_id"],
            dimension_id=data["dimension_id"],
            resolution_path=ResolutionPath(data["resolution_path"]),
            policy_rule_id=data["policy_rule_id"],
            resolved_score=ScoreRepresentation.from_dict(raw_score) if raw_score else None,
            resolver_hypothesis_id=data.get("resolver_hypothesis_id"),
            resolution_note=data.get("resolution_note"),
            is_resolved=data["is_resolved"],
        )


# ── FinalDimensionDecision ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class FinalDimensionDecision:
    """The single authoritative score decision for one rubric dimension.

    Produced by the Adjudicator (or directly from scoring if no conflict arose).
    This is the object consumed by the Feedback assembler and by the Aggregation
    policy to compute an optional CompositeDecision.

    The complete audit trail is preserved through the chain:
      evidence_span_ids -> descriptor_refs -> final_score -> adjudication_id

    Attributes:
        decision_id: Unique ID for this decision within the run.
        dimension_id: Opaque dimension identifier from rubric config.
        final_score: Authoritative ScoreRepresentation for this dimension.
        primary_hypothesis_id: The hypothesis (or resolver hypothesis) that prevailed.
        adjudication_id: If conflict was adjudicated, the AdjudicationRecord ID.
                         None if no conflict occurred.
        evidence_span_ids: Evidence spans supporting the final decision.
        descriptor_refs: Rubric descriptor levels aligned to the final score.
        decision_confidence: Composite confidence for the final decision.
        decision_note: Optional explanation or note for audit.
    """

    decision_id: str
    dimension_id: str
    final_score: ScoreRepresentation
    primary_hypothesis_id: str
    adjudication_id: Optional[str]
    evidence_span_ids: List[str]
    descriptor_refs: List[str]
    decision_confidence: float
    decision_note: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "dimension_id": self.dimension_id,
            "final_score": self.final_score.to_dict(),
            "primary_hypothesis_id": self.primary_hypothesis_id,
            "adjudication_id": self.adjudication_id,
            "evidence_span_ids": list(self.evidence_span_ids),
            "descriptor_refs": list(self.descriptor_refs),
            "decision_confidence": self.decision_confidence,
            "decision_note": self.decision_note,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FinalDimensionDecision:
        _check_no_extra(
            data,
            frozenset({
                "decision_id", "dimension_id", "final_score", "primary_hypothesis_id",
                "adjudication_id", "evidence_span_ids", "descriptor_refs",
                "decision_confidence", "decision_note",
            }),
            "FinalDimensionDecision",
        )
        return cls(
            decision_id=data["decision_id"],
            dimension_id=data["dimension_id"],
            final_score=ScoreRepresentation.from_dict(data["final_score"]),
            primary_hypothesis_id=data["primary_hypothesis_id"],
            adjudication_id=data.get("adjudication_id"),
            evidence_span_ids=list(data.get("evidence_span_ids") or []),
            descriptor_refs=list(data.get("descriptor_refs") or []),
            decision_confidence=data["decision_confidence"],
            decision_note=data.get("decision_note"),
        )


# ── CompositeDecision ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CompositeDecision:
    """Optional aggregated score across selected dimensions.

    Produced by the Aggregation policy when enabled. The aggregation formula,
    participating dimensions, and weights are all stored in aggregation_detail
    which reflects the content of the aggregation policy config — nothing is
    hardcoded here.

    Attributes:
        composite_id: Unique ID for this composite result.
        aggregation_policy_ref: URI ref to the aggregation policy artifact used.
        contributing_decision_ids: FinalDimensionDecision IDs that fed this composite.
        composite_score: Aggregated ScoreRepresentation.
        aggregation_detail: Snapshot of formula/weight details from the policy.
        composite_note: Optional note about the aggregation result.
    """

    composite_id: str
    aggregation_policy_ref: str
    contributing_decision_ids: List[str]
    composite_score: ScoreRepresentation
    aggregation_detail: Dict[str, Any]
    composite_note: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "composite_id": self.composite_id,
            "aggregation_policy_ref": self.aggregation_policy_ref,
            "contributing_decision_ids": list(self.contributing_decision_ids),
            "composite_score": self.composite_score.to_dict(),
            "aggregation_detail": dict(self.aggregation_detail),
            "composite_note": self.composite_note,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CompositeDecision:
        _check_no_extra(
            data,
            frozenset({
                "composite_id", "aggregation_policy_ref", "contributing_decision_ids",
                "composite_score", "aggregation_detail", "composite_note",
            }),
            "CompositeDecision",
        )
        return cls(
            composite_id=data["composite_id"],
            aggregation_policy_ref=data["aggregation_policy_ref"],
            contributing_decision_ids=list(data.get("contributing_decision_ids") or []),
            composite_score=ScoreRepresentation.from_dict(data["composite_score"]),
            aggregation_detail=dict(data.get("aggregation_detail") or {}),
            composite_note=data.get("composite_note"),
        )
