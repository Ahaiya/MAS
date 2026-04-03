"""
Tests for provider interface, adapter registry and capability boundary (Phase 5, Task 1).

Covers:
- LLMRequest / LLMResponse data contracts
- ProviderCapability enum
- ProviderError hierarchy
- BaseProvider abstract class (cannot be instantiated directly)
- Concrete provider implementation correctness
- ProviderRegistry register / get / list operations
- Registry error on unknown provider
- Boundary: base.py must NOT import rubric/policy/orchestrator symbols
"""

from __future__ import annotations

import importlib
import inspect
import pytest
from typing import Any, Dict, Optional

from src.providers.base import (
    LLMRequest,
    LLMResponse,
    ProviderCapability,
    ProviderError,
    ProviderCallError,
    ProviderParseError,
    BaseProvider,
    TokenUsage,
)
from src.providers.registry import ProviderRegistry, get_registry


# ── Fixtures ───────────────────────────────────────────────────────────────────

class _EchoProvider(BaseProvider):
    """Minimal concrete provider for testing — echoes the prompt back."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.TEXT_COMPLETION})

    def complete(self, request: LLMRequest) -> LLMResponse:
        content = f"ECHO: {request.prompt}"
        return LLMResponse(
            content=content,
            structured_data=None,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            provider_name=self.name,
            model_id=request.model_id or "echo-v1",
        )


class _StructuredProvider(BaseProvider):
    """Concrete provider that also supports structured output."""

    @property
    def name(self) -> str:
        return "structured"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({
            ProviderCapability.TEXT_COMPLETION,
            ProviderCapability.STRUCTURED_OUTPUT,
        })

    def complete(self, request: LLMRequest) -> LLMResponse:
        data: Optional[Dict[str, Any]] = None
        if request.output_schema is not None:
            data = {"result": "sample_structured"}
        return LLMResponse(
            content="structured response",
            structured_data=data,
            usage=TokenUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30),
            provider_name=self.name,
            model_id=request.model_id or "structured-v1",
        )


# ── LLMRequest tests ──────────────────────────────────────────────────────────

class TestLLMRequest:
    def test_minimal_construction(self):
        req = LLMRequest(prompt="hello")
        assert req.prompt == "hello"
        assert req.system is None
        assert req.model_id is None
        assert req.params == {}
        assert req.output_schema is None

    def test_full_construction(self):
        schema = {"type": "object", "properties": {"score": {"type": "integer"}}}
        req = LLMRequest(
            prompt="score this essay",
            system="You are an evaluator.",
            model_id="gpt-4o",
            params={"temperature": 0.0, "max_tokens": 512},
            output_schema=schema,
        )
        assert req.prompt == "score this essay"
        assert req.system == "You are an evaluator."
        assert req.model_id == "gpt-4o"
        assert req.params["temperature"] == 0.0
        assert req.output_schema == schema

    def test_empty_prompt_is_rejected(self):
        with pytest.raises((ValueError, Exception)):
            LLMRequest(prompt="")

    def test_no_extra_fields(self):
        """LLMRequest must not silently accept undeclared fields."""
        with pytest.raises((TypeError, Exception)):
            LLMRequest(prompt="p", undeclared_field="x")  # type: ignore[call-arg]


# ── LLMResponse tests ─────────────────────────────────────────────────────────

class TestLLMResponse:
    def test_minimal_construction(self):
        resp = LLMResponse(
            content="some text",
            structured_data=None,
            usage=TokenUsage(prompt_tokens=5, completion_tokens=3, total_tokens=8),
            provider_name="test",
            model_id="test-model",
        )
        assert resp.content == "some text"
        assert resp.structured_data is None

    def test_structured_data_carried(self):
        data = {"score": 4, "rationale": "good work"}
        resp = LLMResponse(
            content="",
            structured_data=data,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
            provider_name="test",
            model_id="test-model",
        )
        assert resp.structured_data == data
        assert resp.usage.total_tokens == 30

    def test_no_extra_fields(self):
        with pytest.raises((TypeError, Exception)):
            LLMResponse(  # type: ignore[call-arg]
                content="x",
                structured_data=None,
                usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                provider_name="p",
                model_id="m",
                undeclared="y",
            )


# ── TokenUsage tests ──────────────────────────────────────────────────────────

class TestTokenUsage:
    def test_construction(self):
        u = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        assert u.total_tokens == 150

    def test_totals_consistent(self):
        u = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        assert u.prompt_tokens + u.completion_tokens == u.total_tokens


# ── ProviderCapability tests ──────────────────────────────────────────────────

class TestProviderCapability:
    def test_text_completion_exists(self):
        assert ProviderCapability.TEXT_COMPLETION is not None

    def test_structured_output_exists(self):
        assert ProviderCapability.STRUCTURED_OUTPUT is not None

    def test_capabilities_are_distinct(self):
        assert ProviderCapability.TEXT_COMPLETION != ProviderCapability.STRUCTURED_OUTPUT


# ── ProviderError hierarchy tests ─────────────────────────────────────────────

class TestProviderErrors:
    def test_provider_error_is_exception(self):
        err = ProviderError("base error")
        assert isinstance(err, Exception)

    def test_call_error_inherits_provider_error(self):
        err = ProviderCallError("call failed", status_code=500)
        assert isinstance(err, ProviderError)
        assert err.status_code == 500

    def test_parse_error_inherits_provider_error(self):
        err = ProviderParseError("parse failed", raw_content="bad json")
        assert isinstance(err, ProviderError)
        assert err.raw_content == "bad json"

    def test_call_error_can_be_raised(self):
        with pytest.raises(ProviderCallError):
            raise ProviderCallError("upstream timeout", status_code=504)

    def test_parse_error_can_be_raised(self):
        with pytest.raises(ProviderParseError):
            raise ProviderParseError("invalid json", raw_content="{bad}")


# ── BaseProvider abstract class tests ─────────────────────────────────────────

class TestBaseProvider:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            BaseProvider()  # type: ignore[abstract]

    def test_concrete_provider_is_base_provider(self):
        provider = _EchoProvider()
        assert isinstance(provider, BaseProvider)

    def test_name_property(self):
        provider = _EchoProvider()
        assert provider.name == "echo"

    def test_capabilities_property_returns_frozenset(self):
        provider = _EchoProvider()
        caps = provider.capabilities
        assert isinstance(caps, frozenset)
        assert ProviderCapability.TEXT_COMPLETION in caps

    def test_complete_returns_llm_response(self):
        provider = _EchoProvider()
        req = LLMRequest(prompt="test prompt")
        resp = provider.complete(req)
        assert isinstance(resp, LLMResponse)
        assert "ECHO: test prompt" in resp.content

    def test_complete_carries_usage(self):
        provider = _EchoProvider()
        req = LLMRequest(prompt="x")
        resp = provider.complete(req)
        assert resp.usage.total_tokens > 0

    def test_structured_provider_capabilities(self):
        provider = _StructuredProvider()
        assert ProviderCapability.STRUCTURED_OUTPUT in provider.capabilities

    def test_structured_provider_returns_data_when_schema_given(self):
        provider = _StructuredProvider()
        schema = {"type": "object"}
        req = LLMRequest(prompt="score", output_schema=schema)
        resp = provider.complete(req)
        assert resp.structured_data is not None

    def test_structured_provider_no_data_without_schema(self):
        provider = _StructuredProvider()
        req = LLMRequest(prompt="score")
        resp = provider.complete(req)
        assert resp.structured_data is None


# ── ProviderRegistry tests ────────────────────────────────────────────────────

class TestProviderRegistry:
    def setup_method(self):
        self.registry = ProviderRegistry()

    def test_empty_registry_has_no_providers(self):
        assert self.registry.list_names() == []

    def test_register_and_get(self):
        self.registry.register("echo", _EchoProvider)
        provider = self.registry.get("echo")
        assert isinstance(provider, _EchoProvider)

    def test_get_unknown_raises_key_error(self):
        with pytest.raises(KeyError):
            self.registry.get("nonexistent")

    def test_list_names_reflects_registered(self):
        self.registry.register("echo", _EchoProvider)
        self.registry.register("structured", _StructuredProvider)
        names = self.registry.list_names()
        assert "echo" in names
        assert "structured" in names

    def test_register_overwrites_existing(self):
        self.registry.register("echo", _EchoProvider)
        self.registry.register("echo", _StructuredProvider)
        provider = self.registry.get("echo")
        assert isinstance(provider, _StructuredProvider)

    def test_get_returns_new_instance_each_time(self):
        self.registry.register("echo", _EchoProvider)
        p1 = self.registry.get("echo")
        p2 = self.registry.get("echo")
        assert p1 is not p2

    def test_global_registry_accessible(self):
        registry = get_registry()
        assert isinstance(registry, ProviderRegistry)

    def test_registry_accepts_only_base_provider_subclasses(self):
        """Registering a non-provider class should raise TypeError."""
        class NotAProvider:
            pass
        with pytest.raises(TypeError):
            self.registry.register("bad", NotAProvider)


# ── Capability boundary test ───────────────────────────────────────────────────

class TestProviderBoundary:
    """Verify that src/providers/base.py does not import rubric/policy/orchestrator symbols."""

    def test_base_does_not_import_rubric_modules(self):
        import src.providers.base as base_mod
        source = inspect.getsource(base_mod)
        forbidden = [
            "src.policies",
            "src.orchestrator",
            "rubric_core",
            "adjudication",
            "aggregation",
            "explanation",
        ]
        for symbol in forbidden:
            assert symbol not in source, (
                f"src/providers/base.py must not reference '{symbol}' — "
                "provider boundary violation"
            )

    def test_registry_does_not_import_rubric_modules(self):
        import src.providers.registry as reg_mod
        source = inspect.getsource(reg_mod)
        forbidden = [
            "src.policies",
            "src.orchestrator",
            "rubric_core",
            "adjudication",
        ]
        for symbol in forbidden:
            assert symbol not in source, (
                f"src/providers/registry.py must not reference '{symbol}' — "
                "provider boundary violation"
            )
