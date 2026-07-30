"""
trace schema：轻量 trace，只记成本与性能。

  LLM 调用 -> StageTrace      （stage/rater/code/llm_calls/tokens/ms）
  StageTrace[] -> RunTraceSummary （一次二级指标评价的汇总，run_trace.json 的内容）

收集用收集器模式：rater.py/reconcile.py/feedback.py 各阶段函数本身不产出 StageTrace、不感知 trace
engine 把 provider 包一层旁路记录器（InstrumentedProvider），每次LLM 调用
按请求 metadata（stage_name/rater_id/code）自动记一条，业务逻辑
不被插桩代码侵入。仲裁调用同样走 provider，因此天然被记成 stage="adjudicate"、
rater="rater_3" 的条目。

只记成本/性能，不含决策数据。

设计不变式：所有模型均为冻结（不可变）的 dataclass。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── StageTrace ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StageTrace:
    """单个流水线阶段的成本/性能记录。

        Attributes：
            stage: 阶段名称（例如 "select"、"extract"、"score"、
                   "adjudicate"、"feedback"）。
            rater: 该阶段所属的评分代理（例如 "rater_1"、仲裁的 "rater_3"）；
                   非 rater 相关阶段为 None。
            code: 该次调用针对的观测点 code（如 "A1-1"）；跨观测点的阶段为 None。
            llm_calls: 该阶段发起的 LLM 调用次数。
            tokens: 该阶段消耗的 token 总数。
            ms: 该阶段耗时（毫秒）。"""

    stage: str
    rater: Optional[str]
    code: Optional[str]
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
            "code": self.code,
            "llm_calls": self.llm_calls,
            "tokens": self.tokens,
            "ms": self.ms,
        }


# ── RunTraceSummary ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunTraceSummary:
    """一次二级指标评价运行的成本/性能汇总（run_trace.json 的内容）。

        Attributes：
            run_id: 此次评价运行的唯一标识符。
            configs_ref: 所用配置根目录的引用。
            dim: 被评价的二级指标标识符。
            total_tokens: 全部阶段的 token 总数。
            total_ms: 全部阶段的耗时总和（毫秒）。
            adjudicated_codes: 触发了 Rater3 仲裁的观测点 code 列表。
            stage_traces: 每次 LLM 调用一条（stage/rater/code/llm_calls/tokens/ms），
                total_tokens/total_ms 是其汇总。
            failed_codes: 评价失败被隔离的观测点，每条 {code, error}；
                该维度未产出 FinalDecision，其余维度不受影响照常产出。"""

    run_id: str
    configs_ref: str
    dim: str
    total_tokens: int
    total_ms: float
    adjudicated_codes: List[str]
    stage_traces: List[StageTrace]
    failed_codes: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "configs_ref": self.configs_ref,
            "dim": self.dim,
            "total_tokens": self.total_tokens,
            "total_ms": self.total_ms,
            "adjudicated_codes": list(self.adjudicated_codes),
            "stage_traces": [st.to_dict() for st in self.stage_traces],
            "failed_codes": [dict(fc) for fc in self.failed_codes],
        }
