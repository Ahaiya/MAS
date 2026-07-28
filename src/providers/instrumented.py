"""
trace 收集：给 provider 包一层旁路计数器，让 engine 不必在业务代码里插桩。

engine 在调用每个阶段前后各拍一次「调用数 / token 用量」快照做差，配合耗时拼出
StageTrace——rater.py / reconcile.py / feedback.py 因此完全不感知 trace。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeVar

from src.contracts.trace import StageTrace
from src.providers.base import BaseProvider, LLMRequest, LLMResponse, ProviderCapability

_T = TypeVar("_T")


@dataclass
class _ProviderMetrics:
    llm_calls: int = 0
    total_tokens: int = 0


class InstrumentedProvider(BaseProvider):
    """包装一个 BaseProvider，旁路记录调用次数与 token 用量，供 engine 的收集器
    模式使用；不改变被包装 provider 的行为。

    二级指标级并发下，同一个 rater 的 provider 实例被多个 worker 线程并发调用。
    call_with_trace 靠"调用前后各拍一次快照做差"取得这次调用的用量——如果
    metrics 是共享计数器，另一个线程在快照窗口之间插入的调用会污染这次差值。
    按线程隔离 metrics（而非加锁）天然解决这个问题：同一时刻只有发起这次调用
    的那个线程会读写自己的 metrics，快照差值只反映它自己的调用。"""

    def __init__(self, inner: BaseProvider) -> None:
        self._inner = inner
        self._local = threading.local()

    @property
    def metrics(self) -> _ProviderMetrics:
        if not hasattr(self._local, "metrics"):
            self._local.metrics = _ProviderMetrics()
        metrics: _ProviderMetrics = self._local.metrics  # threading.local 的属性对类型检查是 Any
        return metrics

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def capabilities(self) -> "frozenset[ProviderCapability]":
        return self._inner.capabilities

    def complete(self, request: LLMRequest) -> LLMResponse:
        response = self._inner.complete(request)
        metrics = self.metrics
        metrics.llm_calls += 1
        metrics.total_tokens += response.usage.total_tokens
        return response


def call_with_trace(
    stage: str,
    rater_id: Optional[str],
    provider: Optional[InstrumentedProvider],
    fn: Callable[..., _T],
    *args: Any,
    **kwargs: Any,
) -> "tuple[_T, StageTrace]":
    """调用 fn，用 provider 累计的调用数/token 数在前后各拍一次快照做差，产出
    这次调用的 StageTrace。provider 为 None 时（如 rater_3 未配置且未触发
    仲裁）llm_calls/tokens 记为 0，只记耗时。"""
    before = (provider.metrics.llm_calls, provider.metrics.total_tokens) if provider is not None else (0, 0)
    started = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed_ms = (time.perf_counter() - started) * 1000
    after = (provider.metrics.llm_calls, provider.metrics.total_tokens) if provider is not None else (0, 0)
    trace = StageTrace(
        stage=stage,
        rater=rater_id,
        llm_calls=after[0] - before[0],
        tokens=after[1] - before[1],
        ms=elapsed_ms,
    )
    return result, trace
