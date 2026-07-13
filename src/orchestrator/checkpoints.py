"""
检查点管理器，负责记录节点级快照并保护回退重试次数。

Checkpoint Manager — 节点级快照追踪与重试限制。

管理 CheckpointRef 对象（来自 Phase 2
trace contracts）的创建与检索，并在 fallback paths 上强制执行最大重试限制。

设计不变性：
- 生成不可变的 CheckpointRef 合约对象 —— 无 ad-hoc dicts。
- 追踪每种 fallback type（re_extract、re_score 等）的重试次数。
- 强制执行可配置的 max_retries 限制；超出该限制会引发
  RetryLimitExceeded，以便 orchestrator 可以 force_fail 或路由至 HUMAN_REVIEW。
- 记录有序的 fallback_history 用于审计追踪。
- 此处不包含任何业务逻辑（adjudication thresholds、trait names 等）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4

from src.contracts.trace import CheckpointRef


class RetryLimitExceeded(Exception):
    """当 fallback path 超出其最大重试次数时引发。"""

    def __init__(self, fallback_type: str, max_retries: int) -> None:
        self.fallback_type = fallback_type
        self.max_retries = max_retries
        super().__init__(
            f"Retry limit exceeded for '{fallback_type}': "
            f"max {max_retries} retries allowed."
        )


class CheckpointManager:
    """管理每个节点的 checkpoint 与每个 fallback type 的 retry counters。

        orchestrator 的使用方式：
            mgr = CheckpointManager(run_id="run-001", max_retries=2)

            # 在每个成功节点之后：
            ckpt = mgr.create_checkpoint("node_preprocess", "preprocess", "snap/prep")

            # 在进入 fallback loop 之前：
            try:
                count = mgr.record_fallback("re_extract")
            except RetryLimitExceeded:
                graph.force_fail()  # 或路由至 HUMAN_REVIEW"""

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
        """在成功执行节点后创建并存储 checkpoint。
        
                如果此 node_id 已存在 checkpoint，则将其覆盖
                （支持 fallback 后重新运行同一节点）。
        
                返回新的 CheckpointRef。"""
        ckpt = CheckpointRef(
            checkpoint_id=f"ckpt-{uuid4().hex[:12]}",
            node_id=node_id,
            run_id=self._run_id,
            snapshot_key=snapshot_key,
            created_at=datetime.now(timezone.utc),
        )
        # 如果存在则覆盖；更新排序
        if node_id in self._checkpoints:
            self._checkpoint_order.remove(node_id)
        self._checkpoints[node_id] = ckpt
        self._checkpoint_order.append(node_id)
        return ckpt

    def get_checkpoint(self, node_id: str) -> Optional[CheckpointRef]:
        """检索某个节点的 checkpoint，如果不存在则为 None。"""
        return self._checkpoints.get(node_id)

    def get_latest_checkpoint(self) -> Optional[CheckpointRef]:
        """获取最近创建的 checkpoint，或 None。"""
        if not self._checkpoint_order:
            return None
        return self._checkpoints[self._checkpoint_order[-1]]

    def record_fallback(self, fallback_type: str) -> int:
        """记录一个 fallback event 并增加其 retry counter。
        
                Args:
                    fallback_type: Fallback 类别标签（例如："re_extract", "re_score"）。
        
                Returns:
                    此 fallback type 的新重试计数。
        
                Raises:
                    RetryLimitExceeded: 如果重试计数将超过 max_retries。"""
        current = self._retry_counts.get(fallback_type, 0)
        if current >= self._max_retries:
            raise RetryLimitExceeded(fallback_type, self._max_retries)
        new_count = current + 1
        self._retry_counts[fallback_type] = new_count
        self._fallback_history.append(fallback_type)
        return new_count

    def get_retry_count(self, fallback_type: str) -> int:
        """获取某个 fallback type 的当前重试次数。如果未追踪则为 0。"""
        return self._retry_counts.get(fallback_type, 0)

    @property
    def fallback_history(self) -> List[str]:
        """目前记录的所有 fallback events 的有序列表。"""
        return list(self._fallback_history)
