"""
OpenAI 兼容 Provider 适配器，负责对接兼容 Chat Completions 的网关。

OpenAI-compatible provider adapter.

Supports any API endpoint that follows the OpenAI Chat Completions interface,
including OpenAI, DeepSeek, local models via LM Studio / Ollama, etc.

Lazy-imports the `openai` package so the module remains importable even when the
`real-provider` optional dependency group is not installed.  Calling `complete()`
without the package installed raises ImportError with an actionable message.

Configuration (all optional, read from constructor args):
  api_key    : Provider API key.
  api_base   : Base URL override (e.g. "https://api.deepseek.com/v1").
  model_id   : Model name (e.g. "gpt-4o", "deepseek-chat").
  timeout    : Request timeout seconds (default 60).
  max_retries: Max retry attempts on transient failures (default 3).
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from src.providers.base import (
    BaseProvider,
    LLMRequest,
    LLMResponse,
    ProviderCallError,
    ProviderCapability,
    ProviderParseError,
    TokenUsage,
)

_DEFAULT_TIMEOUT = 60
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_RETRY_DELAY = 1.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class OpenAICompatibleProvider(BaseProvider):
    """
    Adapter for OpenAI and OpenAI-compatible REST APIs.

    Wraps the `openai` Python SDK (lazy import).  The SDK must be installed
    from the `real-provider` extras group to actually call the API.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model_id: str,
        api_base: Optional[str] = None,
        default_params: Optional[Dict[str, Any]] = None,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        retry_delay: float = _DEFAULT_RETRY_DELAY,
    ) -> None:
        self._api_key = api_key
        self._model_id = model_id
        self._api_base = api_base
        self._default_params = dict(default_params or {})
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_delay = retry_delay

    @property
    def name(self) -> str:
        return "openai_compatible"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({
            ProviderCapability.TEXT_COMPLETION,
            ProviderCapability.STRUCTURED_OUTPUT,
        })

    def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            import openai as _openai
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required to use OpenAICompatibleProvider. "
                "Install it with: pip install 'mas-rubric-evaluation[real-provider]'"
            ) from exc

        client = _openai.OpenAI(
            api_key=self._api_key,
            base_url=self._api_base,
            timeout=self._timeout,
            max_retries=0,  # We handle retries ourselves
        )

        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})

        call_kwargs: Dict[str, Any] = {
            "model": request.model_id or self._model_id,
            "messages": messages,
        }
        merged_params = _merge_params(self._default_params, request.params)
        if merged_params:
            call_kwargs.update(merged_params)
        if request.output_schema is not None:
            call_kwargs["response_format"] = {"type": "json_object"}

        last_exc: Exception = RuntimeError("No attempts made")
        for attempt in range(self._max_retries + 1):
            try:
                response = client.chat.completions.create(**call_kwargs)
                return self._parse_response(request, response)
            except _openai.APIStatusError as exc:
                last_exc = exc
                if exc.status_code not in _RETRYABLE_STATUS_CODES:
                    raise ProviderCallError(
                        f"API error {exc.status_code}: {exc.message}",
                        status_code=exc.status_code,
                    ) from exc
                if attempt < self._max_retries:
                    time.sleep(self._retry_delay * (attempt + 1))
            except _openai.APIConnectionError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    time.sleep(self._retry_delay * (attempt + 1))
            except _openai.APITimeoutError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    time.sleep(self._retry_delay * (attempt + 1))

        raise ProviderCallError(
            f"Provider call failed after {self._max_retries + 1} attempts: {last_exc}"
        ) from last_exc

    def _parse_response(
        self,
        request: LLMRequest,
        response: Any,
    ) -> LLMResponse:
        try:
            choice = response.choices[0]
            content: str = choice.message.content or ""
        except (AttributeError, IndexError) as exc:
            raise ProviderParseError(
                "Unexpected response structure from provider",
                raw_content=str(response),
            ) from exc

        usage_obj = getattr(response, "usage", None)
        if usage_obj is not None:
            usage = TokenUsage(
                prompt_tokens=usage_obj.prompt_tokens,
                completion_tokens=usage_obj.completion_tokens,
                total_tokens=usage_obj.total_tokens,
            )
        else:
            word_count = len(content.split())
            usage = TokenUsage(
                prompt_tokens=0,
                completion_tokens=word_count,
                total_tokens=word_count,
            )

        structured: Optional[Dict[str, Any]] = None
        if request.output_schema is not None and content:
            import json
            try:
                structured = json.loads(content)
                if not isinstance(structured, dict):
                    structured = {"value": structured}
            except json.JSONDecodeError as exc:
                raise ProviderParseError(
                    "Failed to parse JSON from structured output response",
                    raw_content=content,
                ) from exc

        return LLMResponse(
            content=content,
            structured_data=structured,
            usage=usage,
            provider_name=self.name,
            model_id=request.model_id or self._model_id,
        )


def _merge_params(
    default_params: Dict[str, Any],
    request_params: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge provider defaults with per-request overrides.

    Request params win. When both sides provide nested dicts (for example
    ``extra_body``), merge that level as well so a retry can override only the
    specific nested key it needs.
    """
    merged: Dict[str, Any] = dict(default_params or {})
    for key, value in (request_params or {}).items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged
