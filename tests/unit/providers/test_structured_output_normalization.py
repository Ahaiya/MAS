"""
Unit tests for structured output normalization, retry, timeout and
parse-failure guardrails (Phase 5, Task 4).

Covers:
- normalize_structured_output(): JSON parse, schema validation, error paths
- RetryConfig defaults and construction
- GuardedProvider: retry on transient errors, no retry on parse errors,
  max-retry exhaustion, success-on-2nd-attempt, timeout enforcement
- Boundary: no rubric/policy/orchestrator imports in guards.py or structured_output.py
"""

from __future__ import annotations

import inspect
import time
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from src.providers.base import (
    BaseProvider,
    LLMRequest,
    LLMResponse,
    ProviderCallError,
    ProviderCapability,
    ProviderParseError,
    TokenUsage,
)
from src.providers.structured_output import normalize_structured_output
from src.providers.guards import RetryConfig, GuardedProvider


# ── Helpers ───────────────────────────────────────────────────────────────────

def _usage() -> TokenUsage:
    return TokenUsage(prompt_tokens=5, completion_tokens=3, total_tokens=8)


def _resp(content: str, structured: Optional[Dict[str, Any]] = None) -> LLMResponse:
    return LLMResponse(
        content=content,
        structured_data=structured,
        usage=_usage(),
        provider_name="test",
        model_id="test-model",
    )


class _FixedProvider(BaseProvider):
    """Returns a fixed response every time."""

    def __init__(self, response: LLMResponse) -> None:
        self._response = response

    @property
    def name(self) -> str:
        return "fixed"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.TEXT_COMPLETION})

    def complete(self, request: LLMRequest) -> LLMResponse:
        return self._response


class _FailThenSucceedProvider(BaseProvider):
    """Fails with ProviderCallError N times then succeeds."""

    def __init__(self, fail_times: int, success_response: LLMResponse) -> None:
        self._fail_times = fail_times
        self._call_count = 0
        self._success = success_response

    @property
    def name(self) -> str:
        return "fail_then_succeed"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.TEXT_COMPLETION})

    def complete(self, request: LLMRequest) -> LLMResponse:
        self._call_count += 1
        if self._call_count <= self._fail_times:
            raise ProviderCallError(f"transient error #{self._call_count}", status_code=503)
        return self._success


class _AlwaysFailProvider(BaseProvider):
    """Always raises ProviderCallError."""

    @property
    def name(self) -> str:
        return "always_fail"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.TEXT_COMPLETION})

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise ProviderCallError("permanent failure", status_code=500)


class _ParseErrorProvider(BaseProvider):
    """Always raises ProviderParseError."""

    @property
    def name(self) -> str:
        return "parse_error"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.TEXT_COMPLETION})

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise ProviderParseError("bad json", raw_content="{bad}")


class _SlowProvider(BaseProvider):
    """Sleeps for a specified duration before returning."""

    def __init__(self, sleep_seconds: float) -> None:
        self._sleep = sleep_seconds

    @property
    def name(self) -> str:
        return "slow"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.TEXT_COMPLETION})

    def complete(self, request: LLMRequest) -> LLMResponse:
        time.sleep(self._sleep)
        return _resp("done")


# ── normalize_structured_output() tests ──────────────────────────────────────

class TestNormalizeStructuredOutput:
    def test_valid_json_returns_dict(self):
        result = normalize_structured_output('{"score": 4, "rationale": "good"}')
        assert isinstance(result, dict)
        assert result["score"] == 4

    def test_json_with_whitespace(self):
        result = normalize_structured_output('  \n{"key": "value"}\n  ')
        assert result["key"] == "value"

    def test_invalid_json_raises_parse_error(self):
        with pytest.raises(ProviderParseError) as exc_info:
            normalize_structured_output("{invalid json}")
        assert exc_info.value.raw_content == "{invalid json}"

    def test_empty_string_raises_parse_error(self):
        with pytest.raises(ProviderParseError):
            normalize_structured_output("")

    def test_whitespace_only_raises_parse_error(self):
        with pytest.raises(ProviderParseError):
            normalize_structured_output("   ")

    def test_json_array_wrapped_in_dict(self):
        result = normalize_structured_output("[1, 2, 3]")
        assert isinstance(result, dict)
        assert "items" in result or "value" in result

    def test_json_scalar_wrapped_in_dict(self):
        result = normalize_structured_output("42")
        assert isinstance(result, dict)

    def test_json_string_wrapped_in_dict(self):
        result = normalize_structured_output('"hello"')
        assert isinstance(result, dict)

    def test_nested_object_preserved(self):
        result = normalize_structured_output(
            '{"outer": {"inner": [1, 2, 3]}}'
        )
        assert result["outer"]["inner"] == [1, 2, 3]

    def test_raw_content_in_parse_error(self):
        bad = "not json at all"
        with pytest.raises(ProviderParseError) as exc_info:
            normalize_structured_output(bad)
        assert bad in exc_info.value.raw_content

    def test_required_field_missing_raises_when_schema_provided(self):
        schema = {
            "type": "object",
            "required": ["score"],
            "properties": {"score": {"type": "integer"}},
        }
        with pytest.raises(ProviderParseError):
            normalize_structured_output('{"rationale": "ok"}', schema=schema)

    def test_required_field_present_passes(self):
        schema = {
            "type": "object",
            "required": ["score"],
            "properties": {"score": {"type": "integer"}},
        }
        result = normalize_structured_output('{"score": 3}', schema=schema)
        assert result["score"] == 3

    def test_no_schema_skips_field_validation(self):
        # Without schema, any valid JSON dict passes through
        result = normalize_structured_output('{"anything": true}')
        assert result["anything"] is True


# ── RetryConfig tests ────────────────────────────────────────────────────────

class TestRetryConfig:
    def test_default_construction(self):
        cfg = RetryConfig()
        assert cfg.max_retries >= 0
        assert cfg.retry_delay_seconds >= 0.0

    def test_custom_values(self):
        cfg = RetryConfig(max_retries=5, retry_delay_seconds=2.0)
        assert cfg.max_retries == 5
        assert cfg.retry_delay_seconds == 2.0

    def test_zero_retries_allowed(self):
        cfg = RetryConfig(max_retries=0)
        assert cfg.max_retries == 0

    def test_negative_retries_rejected(self):
        with pytest.raises((ValueError, Exception)):
            RetryConfig(max_retries=-1)


# ── GuardedProvider tests ────────────────────────────────────────────────────

class TestGuardedProvider:
    def test_is_base_provider(self):
        inner = _FixedProvider(_resp("ok"))
        guarded = GuardedProvider(inner, RetryConfig())
        assert isinstance(guarded, BaseProvider)

    def test_delegates_name(self):
        inner = _FixedProvider(_resp("ok"))
        guarded = GuardedProvider(inner, RetryConfig())
        assert guarded.name == inner.name

    def test_delegates_capabilities(self):
        inner = _FixedProvider(_resp("ok"))
        guarded = GuardedProvider(inner, RetryConfig())
        assert guarded.capabilities == inner.capabilities

    def test_passes_through_on_success(self):
        resp = _resp("hello")
        inner = _FixedProvider(resp)
        guarded = GuardedProvider(inner, RetryConfig())
        result = guarded.complete(LLMRequest(prompt="test"))
        assert result.content == "hello"

    def test_retries_on_call_error(self):
        success = _resp("success")
        inner = _FailThenSucceedProvider(fail_times=2, success_response=success)
        cfg = RetryConfig(max_retries=3, retry_delay_seconds=0.0)
        guarded = GuardedProvider(inner, cfg)
        result = guarded.complete(LLMRequest(prompt="test"))
        assert result.content == "success"
        assert inner._call_count == 3

    def test_succeeds_on_second_attempt(self):
        success = _resp("ok on retry")
        inner = _FailThenSucceedProvider(fail_times=1, success_response=success)
        cfg = RetryConfig(max_retries=2, retry_delay_seconds=0.0)
        guarded = GuardedProvider(inner, cfg)
        result = guarded.complete(LLMRequest(prompt="test"))
        assert result.content == "ok on retry"
        assert inner._call_count == 2

    def test_raises_after_max_retries_exhausted(self):
        inner = _AlwaysFailProvider()
        cfg = RetryConfig(max_retries=2, retry_delay_seconds=0.0)
        guarded = GuardedProvider(inner, cfg)
        with pytest.raises(ProviderCallError):
            guarded.complete(LLMRequest(prompt="test"))

    def test_does_not_retry_on_parse_error(self):
        inner = _ParseErrorProvider()
        cfg = RetryConfig(max_retries=3, retry_delay_seconds=0.0)
        guarded = GuardedProvider(inner, cfg)
        with pytest.raises(ProviderParseError):
            guarded.complete(LLMRequest(prompt="test"))

    def test_zero_retries_raises_immediately(self):
        inner = _AlwaysFailProvider()
        cfg = RetryConfig(max_retries=0, retry_delay_seconds=0.0)
        guarded = GuardedProvider(inner, cfg)
        with pytest.raises(ProviderCallError):
            guarded.complete(LLMRequest(prompt="test"))

    def test_timeout_raises_call_error(self):
        inner = _SlowProvider(sleep_seconds=5.0)
        cfg = RetryConfig(max_retries=0, retry_delay_seconds=0.0, timeout_seconds=0.1)
        guarded = GuardedProvider(inner, cfg)
        with pytest.raises(ProviderCallError) as exc_info:
            guarded.complete(LLMRequest(prompt="test"))
        assert "timeout" in str(exc_info.value).lower()

    def test_no_timeout_when_not_configured(self):
        inner = _FixedProvider(_resp("fast"))
        cfg = RetryConfig(max_retries=0, retry_delay_seconds=0.0, timeout_seconds=None)
        guarded = GuardedProvider(inner, cfg)
        result = guarded.complete(LLMRequest(prompt="test"))
        assert result.content == "fast"

    def test_call_count_does_not_exceed_max_retries_plus_one(self):
        """Total attempts = max_retries + 1 (initial + retries)."""
        call_count = 0

        class _CountingProvider(BaseProvider):
            @property
            def name(self) -> str:
                return "counting"

            @property
            def capabilities(self) -> frozenset[ProviderCapability]:
                return frozenset({ProviderCapability.TEXT_COMPLETION})

            def complete(self, request: LLMRequest) -> LLMResponse:
                nonlocal call_count
                call_count += 1
                raise ProviderCallError("fail", status_code=500)

        inner = _CountingProvider()
        cfg = RetryConfig(max_retries=3, retry_delay_seconds=0.0)
        guarded = GuardedProvider(inner, cfg)
        with pytest.raises(ProviderCallError):
            guarded.complete(LLMRequest(prompt="test"))
        assert call_count == 4  # 1 initial + 3 retries


# ── Boundary tests ────────────────────────────────────────────────────────────

class TestProviderLayerBoundary:
    def test_structured_output_has_no_policy_imports(self):
        import src.providers.structured_output as mod
        source = inspect.getsource(mod)
        for symbol in ["src.policies", "src.orchestrator", "rubric_core"]:
            assert symbol not in source, (
                f"structured_output.py must not reference '{symbol}'"
            )

    def test_guards_has_no_policy_imports(self):
        import src.providers.guards as mod
        source = inspect.getsource(mod)
        for symbol in ["src.policies", "src.orchestrator", "rubric_core"]:
            assert symbol not in source, (
                f"guards.py must not reference '{symbol}'"
            )
