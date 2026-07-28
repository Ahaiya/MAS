"""
OpenAI 兼容 Provider 适配器，负责对接兼容 Chat Completions 的网关。

OpenAI 兼容的 Provider 适配器。

支持任何遵循 OpenAI Chat Completions 接口的 API endpoint，
包括 OpenAI、DeepSeek、通过 LM Studio / Ollama 运行的本地模型等。

配置（均为可选，从构造函数参数读取）：
  api_key       : Provider API key。
  api_base      : Base URL 覆盖（例如 "https://api.deepseek.com/v1"）。
  model_id      : 模型名称（例如 "gpt-4o"、"deepseek-chat"）。
  timeout       : 请求超时秒数（默认 60）。
  default_params: Provider 默认请求参数，会合并到每次调用中。"""

from __future__ import annotations

from typing import Any, Dict, Optional

import openai

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


class OpenAICompatibleProvider(BaseProvider):
    """
    适用于 OpenAI 及 OpenAI 兼容 REST APIs 的适配器。
    
    封装 `openai` Python SDK。"""

    def __init__(
        self,
        *,
        api_key: str,
        model_id: str,
        api_base: Optional[str] = None,
        default_params: Optional[Dict[str, Any]] = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._api_key = api_key
        self._model_id = model_id
        self._api_base = api_base
        self._default_params = dict(default_params or {})
        self._timeout = timeout

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
        client = openai.OpenAI(
            api_key=self._api_key,
            base_url=self._api_base,
            timeout=self._timeout,
            max_retries=0,  # 重试由 GuardedProvider 处理
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

        try:
            response = client.chat.completions.create(**call_kwargs)
            return self._parse_response(request, response)
        except openai.APIStatusError as exc:
            raise ProviderCallError(
                f"API error {exc.status_code}: {exc.message}",
                status_code=exc.status_code,
            ) from exc
        except openai.APIConnectionError as exc:
            raise ProviderCallError(f"API connection error: {exc}") from exc
        except openai.APITimeoutError as exc:
            raise ProviderCallError(f"API timeout: {exc}") from exc

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
    """将 Provider 默认值与单次请求的覆盖值合并。
    
        请求参数优先。当双方都提供嵌套字典时（例如
        ``extra_body``），也会合并该层级，以便重试时仅覆盖其需要的特定嵌套键。"""
    merged: Dict[str, Any] = dict(default_params or {})
    for key, value in (request_params or {}).items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged
