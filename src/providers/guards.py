"""
Provider 守护层，负责补充重试、超时和解析失败保护。

Provider 守护层 — 重试、超时和解析失败保护。

GuardedProvider 包装任何 BaseProvider 并添加：
- 针对瞬时 ProviderCallError 的带有可配置延迟的自动重试。
- 通过后台线程执行超时；超过限制会抛出
  带有 "timeout" 消息的 ProviderCallError。
- ProviderParseError 不会重试（它表示数据问题，而不是
  瞬时网络/API 问题）。

RetryConfig 包含所有守护层参数，并在构造时对其进行验证。

此模块不了解 rubric 维度、策略或编排。"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from typing import Optional

from src.providers.base import (
    BaseProvider,
    LLMRequest,
    LLMResponse,
    ProviderCallError,
    ProviderCapability,
    ProviderParseError,
)


# ── RetryConfig ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RetryConfig:
    """
    GuardedProvider 重试和超时行为的配置。
    
    属性：
        max_retries          : 首次调用失败后的*额外*尝试次数。
                               0 表示不重试（快速失败）。
                               必须 >= 0。
        retry_delay_seconds  : 尝试之间等待的秒数。0.0 表示
                               立即重试。
        timeout_seconds      : 每次调用的挂钟超时时间（秒）。
                               None 禁用超时强制执行。"""

    max_retries: int = 3
    retry_delay_seconds: float = 1.0
    timeout_seconds: Optional[float] = None

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError(
                f"RetryConfig.max_retries must be >= 0, got {self.max_retries}"
            )
        if self.retry_delay_seconds < 0.0:
            raise ValueError(
                f"RetryConfig.retry_delay_seconds must be >= 0.0, "
                f"got {self.retry_delay_seconds}"
            )


# ── GuardedProvider ───────────────────────────────────────────────────────────

class GuardedProvider(BaseProvider):
    """
    用重试和超时守护层包装 BaseProvider 的装饰器。
    
    用法::
    
        inner = OpenAICompatibleProvider(api_key=..., model_id=...)
        guarded = GuardedProvider(inner, RetryConfig(max_retries=3, timeout_seconds=30))
        response = guarded.complete(request)"""

    def __init__(self, inner: BaseProvider, config: RetryConfig) -> None:
        self._inner = inner
        self._config = config

    # ── BaseProvider interface ─────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return self._inner.capabilities

    def complete(self, request: LLMRequest) -> LLMResponse:
        """
        带重试和超时执行内部 provider 的 complete()。
        
        重试策略：
        - 只有 ProviderCallError 触发重试。
        - ProviderParseError 立即重新抛出（不重试）。
        - 在总共 max_retries+1 次尝试后，最后一个 ProviderCallError
          被重新抛出。
        
        超时策略：
        - 当设置了 timeout_seconds 时，调用在后台线程中运行。
        - 如果未在 timeout_seconds 内完成，将抛出 ProviderCallError
          并在消息中包含 "timeout"。"""
        last_exc: Exception = ProviderCallError("no attempts made")

        for attempt in range(self._config.max_retries + 1):
            try:
                return self._call_with_timeout(request)
            except ProviderParseError:
                raise  # 不可重试
            except ProviderCallError as exc:
                last_exc = exc
                if attempt < self._config.max_retries:
                    if self._config.retry_delay_seconds > 0:
                        time.sleep(self._config.retry_delay_seconds)

        raise last_exc

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _call_with_timeout(self, request: LLMRequest) -> LLMResponse:
        """在可选的挂钟超时下运行 inner.complete()。"""
        if self._config.timeout_seconds is None:
            return self._inner.complete(request)

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self._inner.complete, request)
            try:
                return future.result(timeout=self._config.timeout_seconds)
            except FuturesTimeoutError as exc:
                raise ProviderCallError(
                    f"Provider call exceeded timeout of "
                    f"{self._config.timeout_seconds}s",
                    status_code=None,
                ) from exc
