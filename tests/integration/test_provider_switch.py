"""
Integration tests for MockProvider, OpenAI-compatible adapter, and provider switch
(Phase 5, Task 2).

Tests are split into two groups:
  - Always runnable: MockProvider behaviour and provider switch with "mock"
  - @pytest.mark.real: require real LLM credentials; skipped in CI unless explicitly
    selected with  `pytest -m real`

Zero-hardcoding: no rubric trait names, dimension codes or score values appear here.
"""

from __future__ import annotations

import hashlib
import os
import pytest
from typing import Any, Dict

from src.providers.base import (
    LLMRequest,
    LLMResponse,
    ProviderCapability,
    BaseProvider,
)
from src.providers.mock import MockProvider
from src.providers.registry import ProviderRegistry, get_registry
from src.providers.switch import create_provider, create_provider_from_env


# ── MockProvider unit-style tests (always runnable) ───────────────────────────

class TestMockProvider:
    def setup_method(self):
        self.provider = MockProvider()

    def test_is_base_provider(self):
        assert isinstance(self.provider, BaseProvider)

    def test_name(self):
        assert self.provider.name == "mock"

    def test_capabilities_include_text_completion(self):
        assert ProviderCapability.TEXT_COMPLETION in self.provider.capabilities

    def test_capabilities_include_structured_output(self):
        assert ProviderCapability.STRUCTURED_OUTPUT in self.provider.capabilities

    def test_complete_returns_llm_response(self):
        req = LLMRequest(prompt="evaluate this essay")
        resp = self.provider.complete(req)
        assert isinstance(resp, LLMResponse)

    def test_complete_is_deterministic(self):
        req = LLMRequest(prompt="same prompt")
        r1 = self.provider.complete(req)
        r2 = self.provider.complete(req)
        assert r1.content == r2.content

    def test_same_prompt_same_response_always(self):
        """Call the same prompt 5 times; all results must be identical."""
        req = LLMRequest(prompt="hello world test")
        results = [self.provider.complete(req).content for _ in range(5)]
        assert len(set(results)) == 1

    def test_different_prompts_different_responses(self):
        r1 = self.provider.complete(LLMRequest(prompt="prompt_alpha")).content
        r2 = self.provider.complete(LLMRequest(prompt="prompt_beta")).content
        assert r1 != r2

    def test_response_carries_provider_name(self):
        resp = self.provider.complete(LLMRequest(prompt="x"))
        assert resp.provider_name == "mock"

    def test_response_carries_model_id(self):
        resp = self.provider.complete(LLMRequest(prompt="x"))
        assert resp.model_id is not None and resp.model_id != ""

    def test_usage_is_positive(self):
        resp = self.provider.complete(LLMRequest(prompt="check usage"))
        assert resp.usage.prompt_tokens > 0
        assert resp.usage.completion_tokens > 0
        assert resp.usage.total_tokens == resp.usage.prompt_tokens + resp.usage.completion_tokens

    def test_no_structured_data_without_schema(self):
        resp = self.provider.complete(LLMRequest(prompt="plain text"))
        assert resp.structured_data is None

    def test_structured_data_returned_when_schema_given(self):
        schema: Dict[str, Any] = {"type": "object", "properties": {"score": {"type": "integer"}}}
        resp = self.provider.complete(LLMRequest(prompt="score me", output_schema=schema))
        assert resp.structured_data is not None
        assert isinstance(resp.structured_data, dict)

    def test_structured_output_is_deterministic(self):
        schema: Dict[str, Any] = {"type": "object"}
        req = LLMRequest(prompt="structured prompt", output_schema=schema)
        d1 = self.provider.complete(req).structured_data
        d2 = self.provider.complete(req).structured_data
        assert d1 == d2

    def test_system_message_accepted(self):
        req = LLMRequest(prompt="with system", system="You are an evaluator.")
        resp = self.provider.complete(req)
        assert isinstance(resp, LLMResponse)

    def test_model_id_override_carried_in_response(self):
        req = LLMRequest(prompt="custom model", model_id="custom-v2")
        resp = self.provider.complete(req)
        assert resp.model_id == "custom-v2"


# ── Provider switch logic tests (always runnable) ─────────────────────────────

class TestCreateProvider:
    def test_create_mock_provider(self):
        provider = create_provider("mock")
        assert isinstance(provider, MockProvider)

    def test_create_mock_is_base_provider(self):
        provider = create_provider("mock")
        assert isinstance(provider, BaseProvider)

    def test_create_unknown_raises_key_error(self):
        with pytest.raises(KeyError):
            create_provider("totally_unknown_provider_xyz")

    def test_create_provider_returns_new_instance_each_time(self):
        p1 = create_provider("mock")
        p2 = create_provider("mock")
        assert p1 is not p2


class TestCreateProviderFromEnv:
    def test_defaults_to_mock_when_no_env(self, monkeypatch):
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        provider = create_provider_from_env()
        assert isinstance(provider, MockProvider)

    def test_reads_mock_from_env(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "mock")
        provider = create_provider_from_env()
        assert isinstance(provider, MockProvider)

    def test_unknown_env_value_raises(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "nonexistent_provider")
        with pytest.raises(KeyError):
            create_provider_from_env()


# ── Default registry has "mock" pre-registered ────────────────────────────────

class TestDefaultRegistry:
    def test_global_registry_has_mock(self):
        registry = get_registry()
        assert "mock" in registry.list_names()

    def test_global_registry_returns_mock_provider(self):
        registry = get_registry()
        provider = registry.get("mock")
        assert isinstance(provider, MockProvider)


# ── OpenAI-compatible adapter import boundary test (always runnable) ──────────

class TestOpenAIAdapterImport:
    def test_module_importable(self):
        """The adapter module must be importable even if openai SDK is absent."""
        import importlib
        mod = importlib.import_module("src.providers.openai_compatible")
        assert mod is not None

    def test_adapter_class_accessible(self):
        from src.providers.openai_compatible import OpenAICompatibleProvider
        assert OpenAICompatibleProvider is not None

    def test_adapter_is_base_provider_subclass(self):
        from src.providers.openai_compatible import OpenAICompatibleProvider
        assert issubclass(OpenAICompatibleProvider, BaseProvider)

    def test_adapter_name(self):
        from src.providers.openai_compatible import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(api_key="test-key", model_id="test-model")
        assert provider.name == "openai_compatible"

    def test_adapter_capabilities(self):
        from src.providers.openai_compatible import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(api_key="test-key", model_id="test-model")
        assert ProviderCapability.TEXT_COMPLETION in provider.capabilities
        assert ProviderCapability.STRUCTURED_OUTPUT in provider.capabilities

    def test_adapter_raises_without_openai_package_when_called(self):
        """Calling complete() without openai installed must raise ImportError."""
        import sys
        import importlib
        from src.providers.openai_compatible import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider(api_key="fake-key", model_id="gpt-4o")

        # If openai is installed, skip this test (it would actually try to call the API)
        try:
            import openai  # noqa: F401
            pytest.skip("openai package is installed; skipping import-absent test")
        except ImportError:
            req = LLMRequest(prompt="test")
            with pytest.raises(ImportError):
                provider.complete(req)


# ── Real provider smoke tests (require credentials, skipped by default) ────────

@pytest.mark.real
class TestRealProviderSwitch:
    """
    These tests require LLM_PROVIDER and LLM_API_KEY to be set in the environment.
    Run with:  pytest -m real tests/integration/test_provider_switch.py
    """

    def test_real_provider_env_readable(self):
        provider_name = os.environ.get("LLM_PROVIDER", "mock")
        assert isinstance(provider_name, str)

    def test_real_provider_complete_returns_response(self):
        provider = create_provider_from_env()
        req = LLMRequest(
            prompt="Reply with exactly one word: hello",
            params={"max_tokens": 10},
        )
        resp = provider.complete(req)
        assert isinstance(resp, LLMResponse)
        assert len(resp.content) > 0

    def test_real_provider_usage_reported(self):
        provider = create_provider_from_env()
        req = LLMRequest(prompt="Say: ok", params={"max_tokens": 5})
        resp = provider.complete(req)
        assert resp.usage.total_tokens > 0
