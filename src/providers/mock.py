"""
Mock Provider，负责在无网络测试中稳定返回确定性结果。

MockProvider — deterministic, no-network LLM provider for testing.

Contract:
- Same LLMRequest always produces the same LLMResponse (deterministic by prompt hash).
- No network calls; no external dependencies beyond the standard library.
- Supports both TEXT_COMPLETION and STRUCTURED_OUTPUT capabilities.
- Structured output returns a minimal JSON dict when output_schema is present.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from src.providers.base import (
    BaseProvider,
    LLMRequest,
    LLMResponse,
    ProviderCapability,
    TokenUsage,
)

_MODEL_ID = "mock-v1"


def _prompt_hash(prompt: str, system: Optional[str] = None) -> str:
    seed = f"{prompt}||{system or ''}"
    return hashlib.sha256(seed.encode()).hexdigest()


def _fake_content(prompt: str, system: Optional[str] = None) -> str:
    h = _prompt_hash(prompt, system)
    return f"[mock] response:{h[:16]}"


def _fake_structured(
    prompt: str,
    system: Optional[str],
    schema: Dict[str, Any],
) -> Dict[str, Any]:
    """Return a minimal deterministic dict for any output_schema."""
    h = _prompt_hash(prompt, system)
    return {"mock_result": h[:16], "schema_type": schema.get("type", "object")}


def _fake_usage(prompt: str) -> TokenUsage:
    token_estimate = max(1, len(prompt.split()))
    return TokenUsage(
        prompt_tokens=token_estimate,
        completion_tokens=token_estimate // 2 + 1,
        total_tokens=token_estimate + token_estimate // 2 + 1,
    )


class MockProvider(BaseProvider):
    """
    Deterministic mock provider for testing and development.

    Produces repeatable responses based solely on the request content.
    Never makes network calls.
    """

    @property
    def name(self) -> str:
        return "mock"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({
            ProviderCapability.TEXT_COMPLETION,
            ProviderCapability.STRUCTURED_OUTPUT,
        })

    def complete(self, request: LLMRequest) -> LLMResponse:
        content = _fake_content(request.prompt, request.system)
        structured: Optional[Dict[str, Any]] = None
        if request.output_schema is not None:
            structured = _fake_structured(request.prompt, request.system, request.output_schema)
        return LLMResponse(
            content=content,
            structured_data=structured,
            usage=_fake_usage(request.prompt),
            provider_name=self.name,
            model_id=request.model_id or _MODEL_ID,
        )
