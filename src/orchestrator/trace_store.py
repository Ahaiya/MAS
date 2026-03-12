"""
Trace Store — accumulates NodeTrace records during a pipeline run.

Provides a mutable session that the orchestrator uses to record node
lifecycle events (start, success, failure, force_fail). When the run
completes, build_run_trace() produces an immutable RunTrace contract.

Design invariants:
- All produced objects are Phase 2 trace contracts (NodeTrace, RunTrace,
  CheckpointRef). No ad-hoc dicts or tuples.
- At most one node may be "active" (started but not yet finished) at a time.
  This enforces the sequential orchestrator model for MVP.
- force_fail() during an active node marks that node as FAILED first,
  then appends a __force_fail__ sentinel trace.
- Fallback labels are attached to the currently-active node's trace.
- build_run_trace() is terminal — it snapshots the accumulated state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.contracts.trace import (
    CheckpointRef,
    NodeTrace,
    NodeStatus,
    RunTrace,
    RunStatus,
)


class _ActiveNode:
    """Mutable bookkeeping for a node that has started but not yet finished."""

    __slots__ = (
        "node_id", "node_type", "run_id", "started_at",
        "input_ref", "fallback_history",
    )

    def __init__(
        self,
        node_id: str,
        node_type: str,
        run_id: str,
        started_at: datetime,
        input_ref: Optional[str],
    ) -> None:
        self.node_id = node_id
        self.node_type = node_type
        self.run_id = run_id
        self.started_at = started_at
        self.input_ref = input_ref
        self.fallback_history: List[str] = []


class TraceStore:
    """Accumulates NodeTrace records and builds the final RunTrace.

    Usage by the orchestrator:
        store = TraceStore("run-001", "bundle", "bundle@v1", "req-001")

        store.record_node_start("node_preprocess", "preprocess", input_ref="in/prep")
        nt = store.record_node_success("node_preprocess", output_ref="out/prep")

        # On failure:
        store.record_node_start("node_extractor", "extract")
        nt = store.record_node_failure("node_extractor", "coverage insufficient")

        # On force_fail:
        nt = store.record_force_fail("unrecoverable error")

        # Build final trace:
        rt = store.build_run_trace(RunStatus.COMPLETED, ...)
    """

    def __init__(
        self,
        run_id: str,
        bundle_id: str,
        bundle_version: str,
        request_id: str,
    ) -> None:
        self._run_id = run_id
        self._bundle_id = bundle_id
        self._bundle_version = bundle_version
        self._request_id = request_id
        self._started_at = datetime.now(timezone.utc)
        self._completed_traces: List[NodeTrace] = []
        self._active: Optional[_ActiveNode] = None

    def record_node_start(
        self,
        node_id: str,
        node_type: str,
        input_ref: Optional[str] = None,
    ) -> None:
        """Record that a node has started execution.

        Raises ValueError if another node is already active.
        """
        if self._active is not None:
            raise ValueError(
                f"Cannot start '{node_id}': node '{self._active.node_id}' "
                f"is still active. Finish it first."
            )
        self._active = _ActiveNode(
            node_id=node_id,
            node_type=node_type,
            run_id=self._run_id,
            started_at=datetime.now(timezone.utc),
            input_ref=input_ref,
        )

    def record_node_success(
        self,
        node_id: str,
        output_ref: Optional[str] = None,
        checkpoint: Optional[CheckpointRef] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> NodeTrace:
        """Record successful completion of the active node.

        Returns the immutable NodeTrace.
        Raises ValueError if node_id does not match the active node.
        """
        active = self._require_active(node_id)
        nt = NodeTrace(
            node_id=active.node_id,
            run_id=active.run_id,
            node_type=active.node_type,
            status=NodeStatus.SUCCESS,
            started_at=active.started_at,
            finished_at=datetime.now(timezone.utc),
            input_ref=active.input_ref,
            output_ref=output_ref,
            checkpoint=checkpoint,
            fallback_history=list(active.fallback_history),
            error_message=None,
            metadata=dict(metadata) if metadata else {},
        )
        self._completed_traces.append(nt)
        self._active = None
        return nt

    def record_node_failure(
        self,
        node_id: str,
        error_message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> NodeTrace:
        """Record failure of the active node.

        Returns the immutable NodeTrace with FAILED status.
        Raises ValueError if node_id does not match the active node.
        """
        active = self._require_active(node_id)
        nt = NodeTrace(
            node_id=active.node_id,
            run_id=active.run_id,
            node_type=active.node_type,
            status=NodeStatus.FAILED,
            started_at=active.started_at,
            finished_at=datetime.now(timezone.utc),
            input_ref=active.input_ref,
            output_ref=None,
            checkpoint=None,
            fallback_history=list(active.fallback_history),
            error_message=error_message,
            metadata=dict(metadata) if metadata else {},
        )
        self._completed_traces.append(nt)
        self._active = None
        return nt

    def record_force_fail(self, reason: str) -> NodeTrace:
        """Record an orchestrator-level force_fail event.

        If a node is currently active, it is marked FAILED first,
        then a __force_fail__ sentinel trace is appended.

        Returns the __force_fail__ NodeTrace.
        """
        now = datetime.now(timezone.utc)

        # Close any active node as FAILED
        if self._active is not None:
            active = self._active
            failed_nt = NodeTrace(
                node_id=active.node_id,
                run_id=active.run_id,
                node_type=active.node_type,
                status=NodeStatus.FAILED,
                started_at=active.started_at,
                finished_at=now,
                input_ref=active.input_ref,
                output_ref=None,
                checkpoint=None,
                fallback_history=list(active.fallback_history),
                error_message=f"Interrupted by force_fail: {reason}",
                metadata={},
            )
            self._completed_traces.append(failed_nt)
            self._active = None

        # Append sentinel trace
        sentinel = NodeTrace(
            node_id="__force_fail__",
            run_id=self._run_id,
            node_type="force_fail",
            status=NodeStatus.FAILED,
            started_at=now,
            finished_at=now,
            input_ref=None,
            output_ref=None,
            checkpoint=None,
            fallback_history=[],
            error_message=reason,
            metadata={},
        )
        self._completed_traces.append(sentinel)
        return sentinel

    def add_fallback_to_current(self, fallback_label: str) -> None:
        """Attach a fallback event label to the currently-active node.

        Raises ValueError if no node is active.
        """
        if self._active is None:
            raise ValueError(
                f"Cannot add fallback label '{fallback_label}': "
                f"no node is currently active."
            )
        self._active.fallback_history.append(fallback_label)

    def get_node_traces(self) -> List[NodeTrace]:
        """Return all completed NodeTrace records (ordered by completion time)."""
        return list(self._completed_traces)

    def build_run_trace(
        self,
        status: RunStatus,
        terminal_validation_passed: Optional[bool] = None,
        replay_metadata: Optional[Dict[str, Any]] = None,
    ) -> RunTrace:
        """Build the final immutable RunTrace from accumulated state.

        Args:
            status: Terminal run status (COMPLETED, FAILED, HUMAN_REVIEW).
            terminal_validation_passed: True if terminal validation passed.
            replay_metadata: Provider/seed/fixture metadata for replay.
        """
        return RunTrace(
            run_id=self._run_id,
            bundle_version=self._bundle_version,
            bundle_id=self._bundle_id,
            request_id=self._request_id,
            status=status,
            started_at=self._started_at,
            finished_at=datetime.now(timezone.utc),
            node_traces=list(self._completed_traces),
            terminal_validation_passed=terminal_validation_passed,
            replay_metadata=dict(replay_metadata) if replay_metadata else {},
        )

    def _require_active(self, node_id: str) -> _ActiveNode:
        """Return the active node or raise ValueError."""
        if self._active is None:
            raise ValueError(
                f"No active node. Cannot complete '{node_id}' "
                f"— call record_node_start() first."
            )
        if self._active.node_id != node_id:
            raise ValueError(
                f"Active node is '{self._active.node_id}', not '{node_id}'."
            )
        return self._active
