"""
追踪存储模块，负责累计单次运行中的节点轨迹与事件摘要。

追踪存储 (Trace Store) —— 在 pipeline 运行期间累计 NodeTrace 记录。

提供一个可变的会话，供 orchestrator 用于记录节点
生命周期事件（start, success, failure, force_fail）。当运行
完成时，build_run_trace() 会生成一个不可变的 RunTrace 契约。

设计不变性：
- 所有生成的对象都是 Phase 2 trace 契约（NodeTrace, RunTrace,
  CheckpointRef）。没有临时的 dicts 或 tuples。
- 同一时间最多只能有一个节点处于 "active"（已启动但尚未完成）状态。
  这为 MVP 强制执行了顺序 orchestrator 模型。
- 在 active 节点期间调用 force_fail() 会首先将该节点标记为 FAILED，
  然后追加一个 __force_fail__ sentinel trace。
- Fallback 标签会附加到当前 active 节点的 trace 上。
- build_run_trace() 是终止性的 —— 它会对累计状态进行快照。"""

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
    """为已启动但尚未完成的节点进行 Mutable bookkeeping。"""

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
    """累计 NodeTrace 记录并构建最终的 RunTrace。

        orchestrator 的使用方式：
            store = TraceStore("run-001", "bundle", "bundle@v1", "req-001")

            store.record_node_start("node_preprocess", "preprocess", input_ref="in/prep")
            nt = store.record_node_success("node_preprocess", output_ref="out/prep")

            # 失败时：
            store.record_node_start("node_extractor", "extract")
            nt = store.record_node_failure("node_extractor", "coverage insufficient")

            # force_fail 时：
            nt = store.record_force_fail("unrecoverable error")

            # 构建最终 trace：
            rt = store.build_run_trace(RunStatus.COMPLETED, ...)"""

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
        """记录节点已开始执行。
        
                如果另一个节点已经处于 active 状态，则引发 ValueError。"""
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
        """记录 active 节点的成功完成。
        
                返回不可变的 NodeTrace。
                如果 node_id 与 active 节点不匹配，则引发 ValueError。"""
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
        """记录 active 节点的失败。
        
                返回状态为 FAILED 的不可变 NodeTrace。
                如果 node_id 与 active 节点不匹配，则引发 ValueError。"""
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
        """记录 orchestrator 级别的 force_fail 事件。
        
                如果当前有节点处于 active 状态，它会首先被标记为 FAILED，
                然后会追加一个 __force_fail__ sentinel trace。
        
                返回 __force_fail__ NodeTrace。"""
        now = datetime.now(timezone.utc)

        # 将任何 active 节点作为 FAILED 关闭
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

        # 追加 sentinel trace
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
        """将 fallback 事件标签附加到当前 active 的节点上。
        
                如果没有 active 的节点，则引发 ValueError。"""
        if self._active is None:
            raise ValueError(
                f"Cannot add fallback label '{fallback_label}': "
                f"no node is currently active."
            )
        self._active.fallback_history.append(fallback_label)

    def get_node_traces(self) -> List[NodeTrace]:
        """返回所有已完成的 NodeTrace 记录（按完成时间排序）。"""
        return list(self._completed_traces)

    def build_run_trace(
        self,
        status: RunStatus,
        terminal_validation_passed: Optional[bool] = None,
        replay_metadata: Optional[Dict[str, Any]] = None,
    ) -> RunTrace:
        """从累计状态构建最终的不可变 RunTrace。
        
                Args:
                    status: 终端运行状态 (COMPLETED, FAILED, HUMAN_REVIEW)。
                    terminal_validation_passed: 如果终端验证通过则为 True。
                    replay_metadata: 用于重放的 Provider/seed/fixture 元数据。"""
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
        """返回 active 节点或引发 ValueError。"""
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
