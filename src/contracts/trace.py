"""
追踪契约，定义运行轨迹、节点记录与回放所需的元数据结构。

追踪与回放元数据契约

为评估流水线定义审计轨迹与回放结构：

  节点执行   -> NodeTrace   （每个节点的输入/输出引用、耗时、回退历史）
  NodeTrace[] -> RunTrace   （顶层封装、bundle 版本、最终状态）
  节点快照   -> CheckpointRef （指向持久化节点状态快照的指针）

设计不变式：
- 所有模型均为冻结（不可变）的 dataclass。
- bundle_version 按原样存储 — 回放时必须使用完全相同的 bundle。
- node_traces 是有序的；编排器在节点完成后依次追加。
- fallback_history 记录重新尝试/重新路由事件的序列，用于审计。
- input_ref 和 output_ref 是不透明的存储键（例如文件系统路径或
  对象存储 URI）。本契约不定义存储实现。
- datetime 字段以 ISO 8601 UTC 字符串形式序列化。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


def _parse_dt(value: Any) -> datetime:
    """解析 ISO 8601 字符串，或原样传递 datetime 对象。"""
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
    """单个流水线节点的执行状态。"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


# ── RunStatus ──────────────────────────────────────────────────────────────────


class RunStatus(str, Enum):
    """一次评估运行的整体状态。"""

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    HUMAN_REVIEW = "human_review"


# ── CheckpointRef ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CheckpointRef:
    """指向一个持久化节点状态快照的指针。
    
    编排器在每个节点成功执行后写入检查点。在失败或回放时，运行可以从最后一个检查点恢复，而无需重新执行前面的节点。
    
    属性：
        checkpoint_id: 此检查点的唯一 ID。
        node_id: 被快照的节点。
        run_id: 此检查点所属的评估运行。
        snapshot_key: 快照制品的不透明存储键（路径或 URI）。
        created_at: 检查点写入时的 UTC 时间戳。"""

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



# ── NodeTrace ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NodeTrace:
    """单个流水线节点在一次运行中的执行记录。
    
    编排器为每个节点执行追加一条 NodeTrace。回退事件（重新提取、重新打分、重新路由）以有序标签形式记录在 fallback_history 中，从而使完整解析路径可审计。
    
    属性：
        node_id: 该节点实例的标识符（与编排器图节点一致）。
        run_id: 父级 RunTrace.run_id。
        node_type: 语义类型标签（例如 "preprocess"、"extract"、"score"）。
        status: 该节点的最终执行状态。
        started_at: 节点执行开始时的 UTC 时间戳。
        finished_at: 节点执行结束时的 UTC 时间戳。如果仍在运行，则为 None。
        input_ref: 指向序列化节点输入的不透明键。如果尚未启动，则为 None。
        output_ref: 指向序列化节点输出的不透明键。如果失败，则为 None。
        checkpoint: 如果此节点之后写入了快照，则为 CheckpointRef，否则为 None。
        fallback_history: 该节点回退/重试事件标签的有序列表。
        error_message: 如果 status 为 FAILED，则为错误描述，否则为 None。
        metadata: 用于流水线特定审计数据的任意键值对。"""

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
        """started_at 与 finished_at 之间的挂钟秒数，若无则为 None。"""
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



# ── RunTrace ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunTrace:
    """一次完整评估运行的顶层审计轨迹。
    
    每个 EvaluationRequest 生成一个 RunTrace。它记录冻结的 bundle 版本、所有节点执行记录以及最终的验证结果 — 提供回放和回归测试所需的一切。
    
    属性：
        run_id: 此次评估运行的全局唯一标识符。
        bundle_version: 按原样记录的冻结 bundle 版本字符串。回放时必须精确匹配，才能产生相同结果。
        bundle_id: Bundle 标识符（不含版本）。
        request_id: 链接回原始 EvaluationRequest。
        status: 整体运行状态。
        started_at: 运行开始时的 UTC 时间戳。
        finished_at: 运行结束时的 UTC 时间戳。如果仍在运行，则为 None。
        node_traces: 有序 NodeTrace 记录列表，每个已执行节点一条。
        terminal_validation_passed: 如果最终输出通过 schema/policy 验证，则为 True。如果尚未运行验证，则为 None。
        replay_metadata: 用于实现精确回放的任意键值对（例如 provider、seed、制品清单路径）。"""

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
        """返回给定 node_id 对应的 NodeTrace，若不存在则返回 None。"""
        for nt in self.node_traces:
            if nt.node_id == node_id:
                return nt
        return None

    def failed_nodes(self) -> List[NodeTrace]:
        """返回所有状态为 FAILED 的 NodeTrace。"""
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


# ═══════════════════════════════════════════════════════════════════════════
# v2 契约 —— 轻量 trace（收集器模式，与上方 v1 RunTrace/NodeTrace 并存）
#
# 阶段函数（segment/rate/reconcile/adjudicate/feedback）返回结果时附带
# StageTrace；engine 只收集，不手动插桩。只记成本/性能，不含决策数据、
# 不含 checkpoint/replay 机制。
# ═══════════════════════════════════════════════════════════════════════════


# ── StageTrace ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StageTrace:
    """单个流水线阶段的成本/性能记录。

        属性：
            stage: 阶段名称（例如 "select"、"extract"、"score"、"reconcile"、
                   "adjudicate"、"feedback"）。
            rater: 该阶段所属的评分代理（例如 "rater_1"）；非 rater 相关阶段为 None。
            llm_calls: 该阶段发起的 LLM 调用次数。
            tokens: 该阶段消耗的 token 总数。
            ms: 该阶段耗时（毫秒）。"""

    stage: str
    rater: Optional[str]
    llm_calls: int
    tokens: int
    ms: float

    def __post_init__(self) -> None:
        if self.llm_calls < 0:
            raise ValueError(
                f"StageTrace '{self.stage}': llm_calls must be >= 0, got {self.llm_calls}."
            )
        if self.tokens < 0:
            raise ValueError(
                f"StageTrace '{self.stage}': tokens must be >= 0, got {self.tokens}."
            )
        if self.ms < 0:
            raise ValueError(
                f"StageTrace '{self.stage}': ms must be >= 0, got {self.ms}."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "rater": self.rater,
            "llm_calls": self.llm_calls,
            "tokens": self.tokens,
            "ms": self.ms,
        }


# ── RunTraceSummary ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunTraceSummary:
    """一次一级指标评价运行的成本/性能汇总（run_trace.json 的内容）。

        属性：
            run_id: 此次评价运行的唯一标识符。
            bundle_ref: 所用量规 bundle 的引用。
            dim: 被评价的一级指标标识符。
            total_tokens: 全部阶段的 token 总数。
            total_ms: 全部阶段的耗时总和（毫秒）。
            adjudicated_dims: 触发了 Rater3 仲裁的二级指标标识符列表。"""

    run_id: str
    bundle_ref: str
    dim: str
    total_tokens: int
    total_ms: float
    adjudicated_dims: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "bundle_ref": self.bundle_ref,
            "dim": self.dim,
            "total_tokens": self.total_tokens,
            "total_ms": self.total_ms,
            "adjudicated_dims": list(self.adjudicated_dims),
        }
