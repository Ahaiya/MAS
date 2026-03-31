from __future__ import annotations

import sys
from types import SimpleNamespace

from src.providers.base import LLMRequest
from src.providers.openai_compatible import OpenAICompatibleProvider


class _FakeCompletions:
    def __init__(self) -> None:
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18)
        message = SimpleNamespace(content='{"ok": true}')
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice], usage=usage)


class _FakeOpenAIClient:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


class _FakeOpenAIModule:
    class APIStatusError(Exception):
        def __init__(self, status_code=None, message=""):
            self.status_code = status_code
            self.message = message

    class APIConnectionError(Exception):
        pass

    class APITimeoutError(Exception):
        pass

    def __init__(self, completions: _FakeCompletions) -> None:
        self._completions = completions

    def OpenAI(self, **kwargs):
        return _FakeOpenAIClient(self._completions)


def test_provider_default_params_merge_with_request_params(monkeypatch):
    completions = _FakeCompletions()
    monkeypatch.setitem(sys.modules, "openai", _FakeOpenAIModule(completions))

    provider = OpenAICompatibleProvider(
        api_key="test-key",
        model_id="qwen3.5-plus",
        api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_params={
            "temperature": 0.0,
            "max_tokens": 2048,
            "extra_body": {
                "enable_thinking": True,
                "thinking_budget": 512,
            },
        },
    )

    provider.complete(
        LLMRequest(
            prompt="score this",
            params={
                "max_tokens": 1024,
                "extra_body": {
                    "enable_thinking": False,
                },
            },
        )
    )

    assert completions.last_kwargs is not None
    assert completions.last_kwargs["temperature"] == 0.0
    assert completions.last_kwargs["max_tokens"] == 1024
    assert completions.last_kwargs["extra_body"]["enable_thinking"] is False
    assert completions.last_kwargs["extra_body"]["thinking_budget"] == 512
