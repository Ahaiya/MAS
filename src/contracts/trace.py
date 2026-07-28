"""
追踪契约：轻量 trace，只记成本与性能。

  阶段执行 -> StageTrace      （stage/rater/llm_calls/tokens/ms）
  StageTrace[] -> RunTraceSummary （一次一级指标评价的汇总，run_trace.json 的内容）

收集用收集器模式：rater.py/reconcile.py/feedback.py 各阶段函数本身不产出 StageTrace、
不感知 trace——engine 把每个阶段用到的 provider 包一层旁路计数器，调用前后各拍一次
调用数/token 快照做差，配合耗时拼出 StageTrace，业务逻辑不被插桩代码侵入。

只记成本/性能，不含决策数据；v1 的 NodeTrace/RunTrace/CheckpointRef 那套
input_ref/output_ref/checkpoint 回放机制已随 orchestrator 一并删除。

设计不变式：所有模型均为冻结（不可变）的 dataclass。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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
            adjudicated_dims: 触发了 Rater3 仲裁的二级指标标识符列表。
            stage_traces: 阶段级明细（stage/rater/llm_calls/tokens/ms），
                total_tokens/total_ms 是其汇总。
            failed_dims: 评价失败被隔离的二级指标，每条 {dimension_id, error}；
                该维度未产出 FinalDecision，其余维度不受影响照常产出。"""

    run_id: str
    bundle_ref: str
    dim: str
    total_tokens: int
    total_ms: float
    adjudicated_dims: List[str]
    stage_traces: List[StageTrace]
    failed_dims: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "bundle_ref": self.bundle_ref,
            "dim": self.dim,
            "total_tokens": self.total_tokens,
            "total_ms": self.total_ms,
            "adjudicated_dims": list(self.adjudicated_dims),
            "stage_traces": [st.to_dict() for st in self.stage_traces],
            "failed_dims": [dict(fd) for fd in self.failed_dims],
        }
