"""
Tests for trace and replay metadata contracts (Phase 2, Task 4).

Covers:
- CheckpointRef: snapshot pointer for a node's state at a given run step
- NodeTrace: per-node execution record (input/output refs, timing, fallback history)
- RunTrace: top-level trace envelope for an entire evaluation run
- Roundtrip to_dict() / from_dict() for all models
- Strict deserialization: reject extra/undeclared fields
- Replay invariants: bundle_version, node order, fallback history preserved

No business logic (trait names, pipeline step names as hardcoded strings beyond
enum values) is asserted as domain facts here. Enum values define the node type
vocabulary for the state machine.
"""

import pytest
from datetime import datetime, timezone

from src.contracts.trace import (
    CheckpointRef,
    NodeStatus,
    NodeTrace,
    RunTrace,
    RunStatus,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

TS = datetime(2026, 3, 12, 10, 0, 0, tzinfo=timezone.utc)
TS2 = datetime(2026, 3, 12, 10, 0, 5, tzinfo=timezone.utc)


@pytest.fixture
def checkpoint():
    return CheckpointRef(
        checkpoint_id="ckpt-001",
        node_id="node_preprocess",
        run_id="run-001",
        snapshot_key="preprocess/run-001/node_preprocess",
        created_at=TS,
    )


@pytest.fixture
def node_trace_ok(checkpoint):
    return NodeTrace(
        node_id="node_preprocess",
        run_id="run-001",
        node_type="preprocess",
        status=NodeStatus.SUCCESS,
        started_at=TS,
        finished_at=TS2,
        input_ref="inputs/run-001/node_preprocess",
        output_ref="outputs/run-001/node_preprocess",
        checkpoint=checkpoint,
        fallback_history=[],
        error_message=None,
        metadata={},
    )


@pytest.fixture
def node_trace_failed():
    return NodeTrace(
        node_id="node_extractor",
        run_id="run-001",
        node_type="extract",
        status=NodeStatus.FAILED,
        started_at=TS,
        finished_at=TS2,
        input_ref="inputs/run-001/node_extractor",
        output_ref=None,
        checkpoint=None,
        fallback_history=[],
        error_message="Coverage insufficient for dimension dim_alpha.",
        metadata={},
    )


@pytest.fixture
def run_trace(node_trace_ok, node_trace_failed):
    return RunTrace(
        run_id="run-001",
        bundle_version="asap_set8_baseline@2026-03-10",
        bundle_id="asap_set8_baseline",
        request_id="req-001",
        status=RunStatus.COMPLETED,
        started_at=TS,
        finished_at=TS2,
        node_traces=[node_trace_ok, node_trace_failed],
        terminal_validation_passed=True,
        replay_metadata={"provider": "mock", "seed": 42},
    )


# ── CheckpointRef ──────────────────────────────────────────────────────────────


class TestCheckpointRef:
    def test_creation(self, checkpoint):
        assert checkpoint.checkpoint_id == "ckpt-001"
        assert checkpoint.node_id == "node_preprocess"
        assert checkpoint.run_id == "run-001"

    def test_immutable(self, checkpoint):
        with pytest.raises((AttributeError, TypeError)):
            checkpoint.checkpoint_id = "mutated"  # type: ignore[misc]

    def test_roundtrip_serialization(self, checkpoint):
        restored = CheckpointRef.from_dict(checkpoint.to_dict())
        assert restored == checkpoint

    def test_from_dict_rejects_extra_fields(self, checkpoint):
        d = checkpoint.to_dict()
        d["extra"] = "bad"
        with pytest.raises((TypeError, ValueError)):
            CheckpointRef.from_dict(d)


# ── NodeStatus enum ────────────────────────────────────────────────────────────


class TestNodeStatus:
    def test_values_exist(self):
        assert NodeStatus.SUCCESS is not None
        assert NodeStatus.FAILED is not None
        assert NodeStatus.SKIPPED is not None
        assert NodeStatus.PENDING is not None

    def test_roundtrip_value(self):
        assert NodeStatus("success") == NodeStatus.SUCCESS
        assert NodeStatus("failed") == NodeStatus.FAILED


# ── NodeTrace ──────────────────────────────────────────────────────────────────


class TestNodeTrace:
    def test_creation_success(self, node_trace_ok):
        assert node_trace_ok.node_id == "node_preprocess"
        assert node_trace_ok.status == NodeStatus.SUCCESS
        assert node_trace_ok.output_ref is not None
        assert node_trace_ok.error_message is None

    def test_creation_failed(self, node_trace_failed):
        assert node_trace_failed.status == NodeStatus.FAILED
        assert node_trace_failed.output_ref is None
        assert node_trace_failed.error_message is not None

    def test_immutable(self, node_trace_ok):
        with pytest.raises((AttributeError, TypeError)):
            node_trace_ok.node_id = "mutated"  # type: ignore[misc]

    def test_roundtrip_serialization_success(self, node_trace_ok):
        restored = NodeTrace.from_dict(node_trace_ok.to_dict())
        assert restored == node_trace_ok

    def test_roundtrip_serialization_failed(self, node_trace_failed):
        restored = NodeTrace.from_dict(node_trace_failed.to_dict())
        assert restored == node_trace_failed

    def test_roundtrip_with_fallback_history(self):
        entry = NodeTrace(
            node_id="node_scorer",
            run_id="run-001",
            node_type="score",
            status=NodeStatus.SUCCESS,
            started_at=TS,
            finished_at=TS2,
            input_ref="inputs/run-001/node_scorer",
            output_ref="outputs/run-001/node_scorer",
            checkpoint=None,
            fallback_history=["re_extract_attempt_1", "re_score_attempt_1"],
            error_message=None,
            metadata={"retry_count": 2},
        )
        restored = NodeTrace.from_dict(entry.to_dict())
        assert restored == entry
        assert restored.fallback_history == ["re_extract_attempt_1", "re_score_attempt_1"]

    def test_from_dict_rejects_extra_fields(self, node_trace_ok):
        d = node_trace_ok.to_dict()
        d["hidden"] = "bad"
        with pytest.raises((TypeError, ValueError)):
            NodeTrace.from_dict(d)

    def test_elapsed_seconds(self, node_trace_ok):
        elapsed = node_trace_ok.elapsed_seconds()
        assert elapsed is not None
        assert elapsed == pytest.approx(5.0)

    def test_elapsed_seconds_no_finish(self):
        nt = NodeTrace(
            node_id="node_pending",
            run_id="run-001",
            node_type="preprocess",
            status=NodeStatus.PENDING,
            started_at=TS,
            finished_at=None,
            input_ref=None,
            output_ref=None,
            checkpoint=None,
            fallback_history=[],
            error_message=None,
            metadata={},
        )
        assert nt.elapsed_seconds() is None


# ── RunStatus enum ─────────────────────────────────────────────────────────────


class TestRunStatus:
    def test_values_exist(self):
        assert RunStatus.COMPLETED is not None
        assert RunStatus.FAILED is not None
        assert RunStatus.IN_PROGRESS is not None
        assert RunStatus.HUMAN_REVIEW is not None

    def test_roundtrip_value(self):
        assert RunStatus("completed") == RunStatus.COMPLETED


# ── RunTrace ───────────────────────────────────────────────────────────────────


class TestRunTrace:
    def test_creation(self, run_trace):
        assert run_trace.run_id == "run-001"
        assert run_trace.bundle_version == "asap_set8_baseline@2026-03-10"
        assert run_trace.status == RunStatus.COMPLETED
        assert len(run_trace.node_traces) == 2
        assert run_trace.terminal_validation_passed is True

    def test_bundle_version_preserved(self, run_trace):
        """Bundle version must be preserved verbatim for replay fidelity."""
        assert isinstance(run_trace.bundle_version, str)
        assert len(run_trace.bundle_version) > 0

    def test_immutable(self, run_trace):
        with pytest.raises((AttributeError, TypeError)):
            run_trace.run_id = "mutated"  # type: ignore[misc]

    def test_roundtrip_serialization(self, run_trace):
        restored = RunTrace.from_dict(run_trace.to_dict())
        assert restored == run_trace

    def test_from_dict_rejects_extra_fields(self, run_trace):
        d = run_trace.to_dict()
        d["injected"] = "bad"
        with pytest.raises((TypeError, ValueError)):
            RunTrace.from_dict(d)

    def test_get_node_trace(self, run_trace):
        nt = run_trace.get_node_trace("node_preprocess")
        assert nt is not None
        assert nt.node_id == "node_preprocess"

    def test_get_node_trace_missing(self, run_trace):
        assert run_trace.get_node_trace("nonexistent") is None

    def test_failed_nodes(self, run_trace):
        failed = run_trace.failed_nodes()
        assert len(failed) == 1
        assert failed[0].node_id == "node_extractor"

    def test_replay_metadata_preserved(self, run_trace):
        assert run_trace.replay_metadata["provider"] == "mock"
        assert run_trace.replay_metadata["seed"] == 42
