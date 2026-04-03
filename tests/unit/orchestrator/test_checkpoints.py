"""
Tests for checkpoint, fallback and revert hooks (Phase 3, Task 2).

Covers:
- CheckpointManager:
  - checkpoint creation with auto-generated ID and timestamp
  - checkpoint retrieval by node_id and latest
  - fallback recording with retry counter
  - max retry enforcement (RetryLimitExceeded)
  - fallback history ordering
  - independent counters for re_extract vs re_score

- TraceStore:
  - node lifecycle: start -> success (produces NodeTrace with SUCCESS)
  - node lifecycle: start -> failure (produces NodeTrace with FAILED)
  - force_fail recording as a special NodeTrace
  - fallback labels attached to node traces
  - checkpoint attachment to successful node traces
  - build_run_trace produces valid RunTrace from accumulated traces
  - build_run_trace with terminal states (COMPLETED, FAILED, HUMAN_REVIEW)
  - node traces are ordered by recording time
  - all produced objects are Phase 2 trace contracts (not ad-hoc dicts)

Zero-hardcoding: no trait names, dimension codes, or business thresholds.
Hooks only manage orchestration lifecycle.
"""

import pytest
from datetime import datetime, timezone

from src.orchestrator.checkpoints import (
    CheckpointManager,
    RetryLimitExceeded,
)
from src.orchestrator.trace_store import TraceStore
from src.contracts.trace import (
    CheckpointRef,
    NodeTrace,
    NodeStatus,
    RunTrace,
    RunStatus,
)


# ── CheckpointManager ─────────────────────────────────────────────────────────


class TestCheckpointManager:
    @pytest.fixture
    def mgr(self):
        return CheckpointManager(run_id="run-001", max_retries=2)

    def test_create_checkpoint(self, mgr):
        ckpt = mgr.create_checkpoint(
            node_id="node_preprocess",
            node_type="preprocess",
            snapshot_key="snapshots/run-001/preprocess",
        )
        assert isinstance(ckpt, CheckpointRef)
        assert ckpt.node_id == "node_preprocess"
        assert ckpt.run_id == "run-001"
        assert ckpt.snapshot_key == "snapshots/run-001/preprocess"
        assert ckpt.checkpoint_id.startswith("ckpt-")
        assert ckpt.created_at.tzinfo is not None  # UTC aware

    def test_get_checkpoint_by_node_id(self, mgr):
        mgr.create_checkpoint("node_a", "type_a", "snap/a")
        mgr.create_checkpoint("node_b", "type_b", "snap/b")
        ckpt = mgr.get_checkpoint("node_a")
        assert ckpt is not None
        assert ckpt.node_id == "node_a"

    def test_get_checkpoint_missing_returns_none(self, mgr):
        assert mgr.get_checkpoint("nonexistent") is None

    def test_get_latest_checkpoint(self, mgr):
        mgr.create_checkpoint("node_a", "type_a", "snap/a")
        mgr.create_checkpoint("node_b", "type_b", "snap/b")
        latest = mgr.get_latest_checkpoint()
        assert latest is not None
        assert latest.node_id == "node_b"

    def test_get_latest_checkpoint_empty(self, mgr):
        assert mgr.get_latest_checkpoint() is None

    def test_overwrite_checkpoint_for_same_node(self, mgr):
        """Re-running a node overwrites its checkpoint."""
        ckpt1 = mgr.create_checkpoint("node_a", "type_a", "snap/a/v1")
        ckpt2 = mgr.create_checkpoint("node_a", "type_a", "snap/a/v2")
        assert mgr.get_checkpoint("node_a") == ckpt2
        assert ckpt1.checkpoint_id != ckpt2.checkpoint_id

    # ── Fallback / retry ───────────────────────────────────────────────────

    def test_record_fallback_increments_counter(self, mgr):
        count = mgr.record_fallback("re_extract")
        assert count == 1
        count = mgr.record_fallback("re_extract")
        assert count == 2

    def test_record_fallback_exceeds_max_retries(self, mgr):
        mgr.record_fallback("re_extract")
        mgr.record_fallback("re_extract")
        with pytest.raises(RetryLimitExceeded) as exc_info:
            mgr.record_fallback("re_extract")
        assert "re_extract" in str(exc_info.value)
        assert "2" in str(exc_info.value)

    def test_independent_counters_per_fallback_type(self, mgr):
        mgr.record_fallback("re_extract")
        mgr.record_fallback("re_score")
        mgr.record_fallback("re_extract")
        assert mgr.get_retry_count("re_extract") == 2
        assert mgr.get_retry_count("re_score") == 1

    def test_get_retry_count_untracked_returns_zero(self, mgr):
        assert mgr.get_retry_count("re_extract") == 0

    def test_fallback_history_ordered(self, mgr):
        mgr.record_fallback("re_extract")
        mgr.record_fallback("re_score")
        mgr.record_fallback("re_extract")
        assert mgr.fallback_history == [
            "re_extract", "re_score", "re_extract",
        ]

    def test_fallback_history_empty_initially(self, mgr):
        assert mgr.fallback_history == []

    def test_custom_max_retries(self):
        mgr = CheckpointManager(run_id="run-002", max_retries=1)
        mgr.record_fallback("re_extract")
        with pytest.raises(RetryLimitExceeded):
            mgr.record_fallback("re_extract")


# ── TraceStore ─────────────────────────────────────────────────────────────────


class TestTraceStore:
    @pytest.fixture
    def store(self):
        return TraceStore(
            run_id="run-001",
            bundle_id="test_bundle",
            bundle_version="test_bundle@v1",
            request_id="req-001",
        )

    # ── Node lifecycle: success ────────────────────────────────────────────

    def test_node_start_and_success(self, store):
        store.record_node_start("node_preprocess", "preprocess", input_ref="in/prep")
        nt = store.record_node_success("node_preprocess", output_ref="out/prep")
        assert isinstance(nt, NodeTrace)
        assert nt.node_id == "node_preprocess"
        assert nt.node_type == "preprocess"
        assert nt.run_id == "run-001"
        assert nt.status == NodeStatus.SUCCESS
        assert nt.input_ref == "in/prep"
        assert nt.output_ref == "out/prep"
        assert nt.started_at is not None
        assert nt.finished_at is not None
        assert nt.error_message is None

    def test_node_success_with_checkpoint(self, store):
        ckpt = CheckpointRef(
            checkpoint_id="ckpt-1",
            node_id="node_preprocess",
            run_id="run-001",
            snapshot_key="snap/prep",
            created_at=datetime.now(timezone.utc),
        )
        store.record_node_start("node_preprocess", "preprocess")
        nt = store.record_node_success("node_preprocess", checkpoint=ckpt)
        assert nt.checkpoint == ckpt

    def test_node_success_with_metadata(self, store):
        store.record_node_start("node_preprocess", "preprocess")
        nt = store.record_node_success(
            "node_preprocess", metadata={"retry_count": 0},
        )
        assert nt.metadata["retry_count"] == 0

    # ── Node lifecycle: failure ────────────────────────────────────────────

    def test_node_start_and_failure(self, store):
        store.record_node_start("node_extractor", "extract", input_ref="in/ext")
        nt = store.record_node_failure("node_extractor", "coverage insufficient")
        assert isinstance(nt, NodeTrace)
        assert nt.status == NodeStatus.FAILED
        assert nt.error_message == "coverage insufficient"
        assert nt.output_ref is None

    # ── Force fail ─────────────────────────────────────────────────────────

    def test_record_force_fail(self, store):
        nt = store.record_force_fail("unrecoverable config error")
        assert isinstance(nt, NodeTrace)
        assert nt.node_id == "__force_fail__"
        assert nt.node_type == "force_fail"
        assert nt.status == NodeStatus.FAILED
        assert nt.error_message == "unrecoverable config error"

    def test_force_fail_during_active_node(self, store):
        """If a node is in progress when force_fail is called,
        both the failed node trace and the force_fail trace are recorded."""
        store.record_node_start("node_scorer", "score")
        nt_fail = store.record_force_fail("fatal error during scoring")
        traces = store.get_node_traces()
        # The in-progress node should be marked FAILED, plus the force_fail trace
        assert len(traces) == 2
        assert traces[0].node_id == "node_scorer"
        assert traces[0].status == NodeStatus.FAILED
        assert traces[1].node_id == "__force_fail__"

    # ── Fallback labels ────────────────────────────────────────────────────

    def test_fallback_labels_on_node(self, store):
        store.record_node_start("node_scorer", "score")
        store.add_fallback_to_current("re_score_attempt_1")
        nt = store.record_node_success("node_scorer")
        assert "re_score_attempt_1" in nt.fallback_history

    def test_multiple_fallback_labels(self, store):
        store.record_node_start("node_scorer", "score")
        store.add_fallback_to_current("re_extract_attempt_1")
        store.add_fallback_to_current("re_score_attempt_1")
        nt = store.record_node_success("node_scorer")
        assert nt.fallback_history == ["re_extract_attempt_1", "re_score_attempt_1"]

    # ── Accumulated traces ─────────────────────────────────────────────────

    def test_get_node_traces_ordered(self, store):
        store.record_node_start("node_a", "type_a")
        store.record_node_success("node_a")
        store.record_node_start("node_b", "type_b")
        store.record_node_success("node_b")
        traces = store.get_node_traces()
        assert len(traces) == 2
        assert traces[0].node_id == "node_a"
        assert traces[1].node_id == "node_b"

    def test_get_node_traces_empty(self, store):
        assert store.get_node_traces() == []

    def test_get_node_traces_returns_copies(self, store):
        """Returned list must not allow mutation of internal state."""
        store.record_node_start("node_a", "type_a")
        store.record_node_success("node_a")
        t1 = store.get_node_traces()
        t2 = store.get_node_traces()
        assert t1 == t2
        assert t1 is not t2

    # ── build_run_trace ────────────────────────────────────────────────────

    def test_build_run_trace_completed(self, store):
        store.record_node_start("node_a", "type_a")
        store.record_node_success("node_a")
        rt = store.build_run_trace(
            status=RunStatus.COMPLETED,
            terminal_validation_passed=True,
            replay_metadata={"provider": "openai_compatible"},
        )
        assert isinstance(rt, RunTrace)
        assert rt.run_id == "run-001"
        assert rt.bundle_id == "test_bundle"
        assert rt.bundle_version == "test_bundle@v1"
        assert rt.request_id == "req-001"
        assert rt.status == RunStatus.COMPLETED
        assert rt.started_at is not None
        assert rt.finished_at is not None
        assert len(rt.node_traces) == 1
        assert rt.terminal_validation_passed is True
        assert rt.replay_metadata["provider"] == "openai_compatible"

    def test_build_run_trace_failed(self, store):
        store.record_force_fail("fatal")
        rt = store.build_run_trace(status=RunStatus.FAILED)
        assert rt.status == RunStatus.FAILED
        assert len(rt.node_traces) == 1

    def test_build_run_trace_human_review(self, store):
        store.record_node_start("node_a", "type_a")
        store.record_node_success("node_a")
        rt = store.build_run_trace(status=RunStatus.HUMAN_REVIEW)
        assert rt.status == RunStatus.HUMAN_REVIEW

    def test_build_run_trace_preserves_bundle_version(self, store):
        rt = store.build_run_trace(status=RunStatus.COMPLETED)
        assert rt.bundle_version == "test_bundle@v1"

    # ── Contract compliance ────────────────────────────────────────────────

    def test_node_trace_is_frozen_contract(self, store):
        store.record_node_start("node_a", "type_a")
        nt = store.record_node_success("node_a")
        # Must be a frozen dataclass — mutation should fail
        with pytest.raises((AttributeError, TypeError)):
            nt.node_id = "mutated"  # type: ignore[misc]

    def test_run_trace_is_frozen_contract(self, store):
        rt = store.build_run_trace(status=RunStatus.COMPLETED)
        with pytest.raises((AttributeError, TypeError)):
            rt.run_id = "mutated"  # type: ignore[misc]

    def test_node_trace_roundtrip_serialization(self, store):
        store.record_node_start("node_a", "type_a", input_ref="in/a")
        nt = store.record_node_success("node_a", output_ref="out/a")
        restored = NodeTrace.from_dict(nt.to_dict())
        assert restored == nt

    def test_run_trace_roundtrip_serialization(self, store):
        store.record_node_start("node_a", "type_a")
        store.record_node_success("node_a")
        rt = store.build_run_trace(
            status=RunStatus.COMPLETED,
            replay_metadata={"seed": 42},
        )
        restored = RunTrace.from_dict(rt.to_dict())
        assert restored == rt

    # ── Error handling ─────────────────────────────────────────────────────

    def test_success_without_start_raises(self, store):
        with pytest.raises(ValueError):
            store.record_node_success("node_orphan")

    def test_failure_without_start_raises(self, store):
        with pytest.raises(ValueError):
            store.record_node_failure("node_orphan", "error")

    def test_add_fallback_without_active_node_raises(self, store):
        with pytest.raises(ValueError):
            store.add_fallback_to_current("re_extract")

    def test_double_start_without_finish_raises(self, store):
        store.record_node_start("node_a", "type_a")
        with pytest.raises(ValueError):
            store.record_node_start("node_b", "type_b")
