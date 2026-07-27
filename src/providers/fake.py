"""
测试用 Provider 双胞胎，按调用顺序回放预先脚本化的 LLMResponse。

FakeProvider 是当前代码库唯一的 fake/stub provider，建在 BaseProvider 之上——
测试策略的主接缝。每次 complete() 调用按 FIFO 顺序吐出构造时传入的一个
LLMResponse；脚本耗尽时报错而不是静默返回空响应，避免测试因脚本写少了而
悄悄通过。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from src.providers.base import (
    BaseProvider,
    LLMRequest,
    LLMResponse,
    ProviderCallError,
    ProviderCapability,
    TokenUsage,
)

_ZERO_USAGE = TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)


def fake_response(
    data: Dict[str, Any],
    *,
    provider_name: str = "fake",
    model_id: str = "fake-model",
    usage: Optional[TokenUsage] = None,
) -> LLMResponse:
    """构造一个内容为 JSON 序列化 data 的 LLMResponse，供 FakeProvider 脚本化使用。"""
    return LLMResponse(
        content=json.dumps(data, ensure_ascii=False),
        structured_data=data,
        usage=usage or _ZERO_USAGE,
        provider_name=provider_name,
        model_id=model_id,
    )


class FakeProvider(BaseProvider):
    """按调用顺序回放脚本化 LLMResponse 的测试 provider。"""

    def __init__(self, responses: Sequence[LLMResponse], *, name: str = "fake") -> None:
        self._responses: List[LLMResponse] = list(responses)
        self._name = name
        self.requests: List[LLMRequest] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.TEXT_COMPLETION, ProviderCapability.STRUCTURED_OUTPUT})

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._responses:
            raise ProviderCallError(
                f"FakeProvider '{self._name}': script exhausted after {len(self.requests)} call(s); "
                "add another scripted LLMResponse for this call."
            )
        return self._responses.pop(0)
