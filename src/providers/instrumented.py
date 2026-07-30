"""
trace 收集：给 provider 包一层旁路记录器，让 engine 不必在业务代码里插桩。

每次 LLM 调用记一条 StageTrace，stage/rater/观测点 code 全部取自请求 metadata
（llm_json.call_llm 与 feedback 已经在每个请求里带上）——rater.py / reconcile.py /
adjudicator.py / feedback.py 因此完全不感知 trace，而仲裁这种"跑不跑要看结果"的
调用也不会漏记。"""

from __future__ import annotations

import threading
import time
from typing import List, Optional

from src.contracts.trace import StageTrace
from src.providers.base import BaseProvider, LLMRequest, LLMResponse, ProviderCapability


class InstrumentedProvider(BaseProvider):
    """包装一个 BaseProvider，旁路记录每次调用的阶段/观测点/token/耗时；
    不改变被包装 provider 的行为。

    观测点级并发下同一个 provider 实例被多个 worker 线程并发调用，记录一律
    进同一个加锁列表，engine 在一个二级指标跑完后一次性 drain。"""

    def __init__(self, inner: BaseProvider) -> None:
        self._inner = inner
        self._lock = threading.Lock()
        self._traces: List[StageTrace] = []

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def capabilities(self) -> "frozenset[ProviderCapability]":
        return self._inner.capabilities

    def complete(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        response = self._inner.complete(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        metadata = request.metadata
        trace = StageTrace(
            stage=str(metadata.get("stage_name") or "unknown"),
            rater=_optional_str(metadata.get("rater_id")),
            code=_optional_str(metadata.get("code")),
            llm_calls=1,
            tokens=response.usage.total_tokens,
            ms=elapsed_ms,
        )
        with self._lock:
            self._traces.append(trace)
        return response

    def drain_traces(self) -> List[StageTrace]:
        """取走并清空已记录的 StageTrace（按调用完成顺序）。"""
        with self._lock:
            traces, self._traces = self._traces, []
        return traces


def _optional_str(value: object) -> Optional[str]:
    return str(value) if value else None
