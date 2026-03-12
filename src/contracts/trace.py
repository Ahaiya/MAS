"""
Trace and Replay Metadata Contracts

Defines the audit trail and replay structures for the evaluation pipeline:

  Node execution  -> NodeTrace   (per-node input/output refs, timing, fallback history)
  NodeTrace[]     -> RunTrace    (top-level envelope, bundle version, terminal status)
  Node snapshot   -> CheckpointRef (pointer to a persisted node state snapshot)

Design invariants:
- All models are frozen (immutable) dataclasses.
- bundle_version is stored verbatim — replay must use the exact same bundle.
- node_traces are ordered; the orchestrator appends them as nodes complete.
- fallback_history records the sequence of re-try/re-route events for audit.
- input_ref and output_ref are opaque storage keys (e.g., filesystem paths or
  object storage URIs). This contract does not define storage implementation.
- from_dict() rejects unknown keys (strict schema enforcement).
- datetime fields are serialized as ISO 8601 UTC strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# ── Strict deserialization helper ──────────────────────────────────────────────


def _check_no_extra(data: Dict[str, Any], allowed: frozenset, cls_name: str) -> None:
    extra = set(data) - allowed
    if extra:
        raise TypeError(
            f"{cls_name}.from_dict() received unexpected fields: {sorted(extra)}"
        )


def _parse_dt(value: Any) -> datetime:
    """Parse an ISO 8601 string or pass through a datetime."""
    if isinstance(value, str):
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return value


def _parse_dt_optional(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    return _parse_dt(value)


# ── NodeStatus ─────────────────────────────────────────────────────────────────


class NodeStatus(str, Enum):
    """Execution status of a single pipeline node."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


# ── RunStatus ──────────────────────────────────────────────────────────────────


class RunStatus(str, Enum):
    """Overall status of an evaluation run."""

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    HUMAN_REVIEW = "human_review"


# ── CheckpointRef ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CheckpointRef:
    """A pointer to a persisted node state snapshot.

    The orchestrator writes a checkpoint after each successful node execution.
    On failure or replay, the run can be resumed from the last checkpoint
    without re-executing earlier nodes.

    Attributes:
        checkpoint_id: Unique ID for this checkpoint.
        node_id: The node whose output was snapshotted.
        run_id: The evaluation run this checkpoint belongs to.
        snapshot_key: Opaque storage key (path or URI) of the snapshot artifact.
        created_at: UTC timestamp when the checkpoint was written.
    """

    checkpoint_id: str
    node_id: str
    run_id: str
    snapshot_key: str
    created_at: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "node_id": self.node_id,
            "run_id": self.run_id,
            "snapshot_key": self.snapshot_key,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CheckpointRef:
        _check_no_extra(
            data,
            frozenset({"checkpoint_id", "node_id", "run_id", "snapshot_key",
                       "created_at"}),
            "CheckpointRef",
        )
        return cls(
            checkpoint_id=data["checkpoint_id"],
            node_id=data["node_id"],
            run_id=data["run_id"],
            snapshot_key=data["snapshot_key"],
            created_at=_parse_dt(data["created_at"]),
        )


# ── NodeTrace ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NodeTrace:
    """Execution record for a single pipeline node within a run.

    The orchestrator appends one NodeTrace per node execution. Fallback
    events (re-extract, re-score, re-route) are recorded in fallback_history
    as ordered labels so the full resolution path is auditable.

    Attributes:
        node_id: Identifier for this node instance (matches orchestrator graph node).
        run_id: Parent RunTrace.run_id.
        node_type: Semantic type label (e.g., "preprocess", "extract", "score").
        status: Terminal execution status of this node.
        started_at: UTC timestamp when node execution began.
        finished_at: UTC timestamp when node execution ended. None if still running.
        input_ref: Opaque key pointing to the serialized node input. None if pending.
        output_ref: Opaque key pointing to the serialized node output. None on failure.
        checkpoint: CheckpointRef if a snapshot was written after this node. Else None.
        fallback_history: Ordered list of fallback/retry event labels for this node.
        error_message: Error description if status is FAILED. None otherwise.
        metadata: Arbitrary key-value pairs for pipeline-specific audit data.
    """

    node_id: str
    run_id: str
    node_type: str
    status: NodeStatus
    started_at: datetime
    finished_at: Optional[datetime]
    input_ref: Optional[str]
    output_ref: Optional[str]
    checkpoint: Optional[CheckpointRef]
    fallback_history: List[str]
    error_message: Optional[str]
    metadata: Dict[str, Any]

    def elapsed_seconds(self) -> Optional[float]:
        """Wall-clock seconds between started_at and finished_at, or None."""
        if self.finished_at is None:
            return None
        delta = self.finished_at - self.started_at
        return delta.total_seconds()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "run_id": self.run_id,
            "node_type": self.node_type,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "input_ref": self.input_ref,
            "output_ref": self.output_ref,
            "checkpoint": self.checkpoint.to_dict() if self.checkpoint else None,
            "fallback_history": list(self.fallback_history),
            "error_message": self.error_message,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> NodeTrace:
        _check_no_extra(
            data,
            frozenset({
                "node_id", "run_id", "node_type", "status", "started_at",
                "finished_at", "input_ref", "output_ref", "checkpoint",
                "fallback_history", "error_message", "metadata",
            }),
            "NodeTrace",
        )
        raw_ckpt = data.get("checkpoint")
        return cls(
            node_id=data["node_id"],
            run_id=data["run_id"],
            node_type=data["node_type"],
            status=NodeStatus(data["status"]),
            started_at=_parse_dt(data["started_at"]),
            finished_at=_parse_dt_optional(data.get("finished_at")),
            input_ref=data.get("input_ref"),
            output_ref=data.get("output_ref"),
            checkpoint=CheckpointRef.from_dict(raw_ckpt) if raw_ckpt else None,
            fallback_history=list(data.get("fallback_history") or []),
            error_message=data.get("error_message"),
            metadata=dict(data.get("metadata") or {}),
        )


# ── RunTrace ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunTrace:
    """Top-level audit trace for a complete evaluation run.

    One RunTrace is produced per EvaluationRequest. It captures the frozen
    bundle version, all node execution records, and the terminal validation
    result — providing everything needed for replay and regression testing.

    Attributes:
        run_id: Globally unique identifier for this evaluation run.
        bundle_version: Verbatim frozen bundle version string. Must match exactly
                        for replay to produce identical results.
        bundle_id: Bundle identifier (without version).
        request_id: Links back to the originating EvaluationRequest.
        status: Overall run status.
        started_at: UTC timestamp when the run began.
        finished_at: UTC timestamp when the run ended. None if still running.
        node_traces: Ordered list of NodeTrace records, one per executed node.
        terminal_validation_passed: True if the final output passed schema/policy
                                    validation. None if validation was not yet run.
        replay_metadata: Arbitrary key-value pairs enabling exact replay
                         (e.g., provider, seed, mock fixture paths).
    """

    run_id: str
    bundle_version: str
    bundle_id: str
    request_id: str
    status: RunStatus
    started_at: datetime
    finished_at: Optional[datetime]
    node_traces: List[NodeTrace]
    terminal_validation_passed: Optional[bool]
    replay_metadata: Dict[str, Any]

    def get_node_trace(self, node_id: str) -> Optional[NodeTrace]:
        """Return the NodeTrace for the given node_id, or None."""
        for nt in self.node_traces:
            if nt.node_id == node_id:
                return nt
        return None

    def failed_nodes(self) -> List[NodeTrace]:
        """Return all NodeTraces with FAILED status."""
        return [nt for nt in self.node_traces if nt.status == NodeStatus.FAILED]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "bundle_version": self.bundle_version,
            "bundle_id": self.bundle_id,
            "request_id": self.request_id,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "node_traces": [nt.to_dict() for nt in self.node_traces],
            "terminal_validation_passed": self.terminal_validation_passed,
            "replay_metadata": dict(self.replay_metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RunTrace:
        _check_no_extra(
            data,
            frozenset({
                "run_id", "bundle_version", "bundle_id", "request_id", "status",
                "started_at", "finished_at", "node_traces",
                "terminal_validation_passed", "replay_metadata",
            }),
            "RunTrace",
        )
        return cls(
            run_id=data["run_id"],
            bundle_version=data["bundle_version"],
            bundle_id=data["bundle_id"],
            request_id=data["request_id"],
            status=RunStatus(data["status"]),
            started_at=_parse_dt(data["started_at"]),
            finished_at=_parse_dt_optional(data.get("finished_at")),
            node_traces=[NodeTrace.from_dict(nt) for nt in (data.get("node_traces") or [])],
            terminal_validation_passed=data.get("terminal_validation_passed"),
            replay_metadata=dict(data.get("replay_metadata") or {}),
        )
