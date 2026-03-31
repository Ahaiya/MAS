"""
检查点管理器，负责记录节点级快照并保护回退重试次数。

Checkpoint Manager — node-level snapshot tracking and retry limiting.

Manages the creation and retrieval of CheckpointRef objects (from Phase 2
trace contracts) and enforces max retry limits on fallback paths.

Design invariants:
- Produces immutable CheckpointRef contract objects — no ad-hoc dicts.
- Tracks retry counts per fallback type (re_extract, re_score, etc.).
- Enforces a configurable max_retries limit; exceeding it raises
  RetryLimitExceeded so the orchestrator can force_fail or route to HUMAN_REVIEW.
- Records an ordered fallback_history for audit trail.
- No business logic (adjudication thresholds, trait names, etc.) lives here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4

from src.contracts.trace import CheckpointRef


class RetryLimitExceeded(Exception):
    """Raised when a fallback path exceeds its max retry count."""

    def __init__(self, fallback_type: str, max_retries: int) -> None:
        self.fallback_type = fallback_type
        self.max_retries = max_retries
        super().__init__(
            f"Retry limit exceeded for '{fallback_type}': "
            f"max {max_retries} retries allowed."
        )


class CheckpointManager:
    """Manages per-node checkpoints and per-fallback-type retry counters.

    Usage by the orchestrator:
        mgr = CheckpointManager(run_id="run-001", max_retries=2)

        # After each successful node:
        ckpt = mgr.create_checkpoint("node_preprocess", "preprocess", "snap/prep")

        # Before entering a fallback loop:
        try:
            count = mgr.record_fallback("re_extract")
        except RetryLimitExceeded:
            graph.force_fail()  # or route to HUMAN_REVIEW
    """

    def __init__(self, run_id: str, max_retries: int = 2) -> None:
        self._run_id = run_id
        self._max_retries = max_retries
        self._checkpoints: Dict[str, CheckpointRef] = {}
        self._checkpoint_order: List[str] = []
        self._retry_counts: Dict[str, int] = {}
        self._fallback_history: List[str] = []

    def create_checkpoint(
        self,
        node_id: str,
        node_type: str,
        snapshot_key: str,
    ) -> CheckpointRef:
        """Create and store a checkpoint after successful node execution.

        If a checkpoint already exists for this node_id, it is overwritten
        (supports re-run of the same node after fallback).

        Returns the new CheckpointRef.
        """
        ckpt = CheckpointRef(
            checkpoint_id=f"ckpt-{uuid4().hex[:12]}",
            node_id=node_id,
            run_id=self._run_id,
            snapshot_key=snapshot_key,
            created_at=datetime.now(timezone.utc),
        )
        # Overwrite if exists; update ordering
        if node_id in self._checkpoints:
            self._checkpoint_order.remove(node_id)
        self._checkpoints[node_id] = ckpt
        self._checkpoint_order.append(node_id)
        return ckpt

    def get_checkpoint(self, node_id: str) -> Optional[CheckpointRef]:
        """Retrieve the checkpoint for a node, or None if none exists."""
        return self._checkpoints.get(node_id)

    def get_latest_checkpoint(self) -> Optional[CheckpointRef]:
        """Get the most recently created checkpoint, or None."""
        if not self._checkpoint_order:
            return None
        return self._checkpoints[self._checkpoint_order[-1]]

    def record_fallback(self, fallback_type: str) -> int:
        """Record a fallback event and increment its retry counter.

        Args:
            fallback_type: Fallback category label (e.g., "re_extract", "re_score").

        Returns:
            The new retry count for this fallback type.

        Raises:
            RetryLimitExceeded: If the retry count would exceed max_retries.
        """
        current = self._retry_counts.get(fallback_type, 0)
        if current >= self._max_retries:
            raise RetryLimitExceeded(fallback_type, self._max_retries)
        new_count = current + 1
        self._retry_counts[fallback_type] = new_count
        self._fallback_history.append(fallback_type)
        return new_count

    def get_retry_count(self, fallback_type: str) -> int:
        """Get the current retry count for a fallback type. 0 if untracked."""
        return self._retry_counts.get(fallback_type, 0)

    @property
    def fallback_history(self) -> List[str]:
        """Ordered list of all fallback events recorded so far."""
        return list(self._fallback_history)
