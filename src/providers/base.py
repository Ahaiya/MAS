"""
Provider 抽象层，定义模型调用请求、响应与错误边界。

Provider interface — capability boundary definition.

This module defines ONLY the transport-level abstractions:
- LLMRequest  : structured input to a provider call
- LLMResponse : structured output from a provider call
- TokenUsage  : token accounting
- ProviderCapability : advertised capabilities of a provider
- ProviderError hierarchy : call-level and parse-level errors
- BaseProvider : abstract base all concrete providers must implement

BOUNDARY RULE (enforced by test_provider_interface.py):
  This module must remain free of policy, orchestrator, and rubric imports.
  It handles only transport-level concerns: call formatting, execution, parsing.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Optional


# ── Value objects ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TokenUsage:
    """Token accounting returned by a provider call."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class LLMRequest:
    """
    Provider-agnostic request envelope.

    Fields:
        prompt        : The main user/task prompt text (required, non-empty).
        system        : Optional system instruction passed separately.
        model_id      : Optional model identifier override.
        params        : Provider-specific call parameters (temperature, max_tokens, …).
        output_schema : Optional JSON schema dict requesting structured output.
        metadata      : Optional non-provider metadata for tracing / debug tools.
    """

    prompt: str
    system: Optional[str] = None
    model_id: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    output_schema: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.prompt:
            raise ValueError("LLMRequest.prompt must be a non-empty string")


@dataclass(frozen=True)
class LLMResponse:
    """
    Provider-agnostic response envelope.

    Fields:
        content         : Raw text content from the model.
        structured_data : Parsed structured output (set when output_schema was provided
                          and parsing succeeded; None otherwise).
        usage           : Token usage accounting.
        provider_name   : Name of the provider that produced this response.
        model_id        : Model identifier used for this call.
    """

    content: str
    structured_data: Optional[Dict[str, Any]]
    usage: TokenUsage
    provider_name: str
    model_id: str


# ── Capability enum ────────────────────────────────────────────────────────────

class ProviderCapability(Enum):
    """Capabilities a provider may advertise."""

    TEXT_COMPLETION = auto()
    STRUCTURED_OUTPUT = auto()


# ── Error hierarchy ────────────────────────────────────────────────────────────

class ProviderError(Exception):
    """Base class for all provider-level errors."""


class ProviderCallError(ProviderError):
    """Raised when the underlying API call fails (network, auth, rate-limit, …)."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProviderParseError(ProviderError):
    """Raised when the provider response cannot be parsed into the expected shape."""

    def __init__(self, message: str, *, raw_content: str = "") -> None:
        super().__init__(message)
        self.raw_content = raw_content


# ── Abstract base provider ─────────────────────────────────────────────────────

class BaseProvider(abc.ABC):
    """
    Abstract base class for all LLM provider adapters.

    A concrete provider is responsible for:
    - Formatting an LLMRequest into the API-specific payload.
    - Executing the API call (with retry/timeout managed internally).
    - Parsing the API response into an LLMResponse.
    - Raising ProviderCallError or ProviderParseError on failure.

    A concrete provider MUST NOT:
    - Contain rubric semantics (dimension names, score ranges, descriptor logic).
    - Contain policy logic (conflict resolution, score combining, citation rules).
    - Perform state machine routing.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Unique identifier for this provider (e.g. "openai", "mock")."""

    @property
    @abc.abstractmethod
    def capabilities(self) -> frozenset[ProviderCapability]:
        """Set of capabilities this provider supports."""

    @abc.abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse:
        """
        Execute a single completion call.

        Args:
            request: The LLMRequest containing prompt, optional system message,
                     optional model override, optional output schema, and params.

        Returns:
            LLMResponse with content, optional structured_data, and usage info.

        Raises:
            ProviderCallError: If the API call itself fails.
            ProviderParseError: If the response cannot be parsed.
        """
