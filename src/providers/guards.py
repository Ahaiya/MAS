"""
Provider 守护层，负责补充重试、超时和解析失败保护。

Provider guardrails — retry, timeout, and parse-failure protection.

GuardedProvider wraps any BaseProvider and adds:
- Automatic retry with configurable delay for transient ProviderCallErrors.
- Timeout enforcement via a background thread; exceeding the limit raises
  ProviderCallError with a "timeout" message.
- ProviderParseError is NOT retried (it indicates a data problem, not a
  transient network/API problem).

RetryConfig holds all guardrail parameters and validates them at construction.

This module has no knowledge of rubric dimensions, policies, or orchestration.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
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
    Configuration for GuardedProvider retry and timeout behaviour.

    Attributes:
        max_retries          : Number of *additional* attempts after the first
                               call fails.  0 means no retries (fail fast).
                               Must be >= 0.
        retry_delay_seconds  : Seconds to wait between attempts.  0.0 for
                               immediate retry.
        timeout_seconds      : Per-call wall-clock timeout in seconds.
                               None disables timeout enforcement.
    """

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
    Decorator that wraps a BaseProvider with retry and timeout guardrails.

    Usage::

        inner = OpenAICompatibleProvider(api_key=..., model_id=...)
        guarded = GuardedProvider(inner, RetryConfig(max_retries=3, timeout_seconds=30))
        response = guarded.complete(request)
    """

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
        Execute the inner provider's complete() with retry and timeout.

        Retry policy:
        - Only ProviderCallError triggers a retry.
        - ProviderParseError is re-raised immediately (no retry).
        - After max_retries+1 total attempts, the last ProviderCallError
          is re-raised.

        Timeout policy:
        - When timeout_seconds is set, the call runs in a background thread.
        - If it does not complete within timeout_seconds, ProviderCallError
          is raised with "timeout" in the message.
        """
        last_exc: Exception = ProviderCallError("no attempts made")

        for attempt in range(self._config.max_retries + 1):
            try:
                return self._call_with_timeout(request)
            except ProviderParseError:
                raise  # Not retryable
            except ProviderCallError as exc:
                last_exc = exc
                if attempt < self._config.max_retries:
                    if self._config.retry_delay_seconds > 0:
                        time.sleep(self._config.retry_delay_seconds)

        raise last_exc

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _call_with_timeout(self, request: LLMRequest) -> LLMResponse:
        """Run inner.complete() optionally under a wall-clock timeout."""
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
