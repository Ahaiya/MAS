"""LoggingProvider 的行为测试。

只断言外部可观察行为：透传被包装 provider 的响应、累计统计正确、每次调用打两行
日志到给定的流。日志文本本身只断言关键字段（调用序号 / token / 预览），不逐字比对
排版——那是实现细节。"""

from __future__ import annotations

import io
import json

import pytest
from typing import List

from src.providers.base import BaseProvider, LLMRequest, LLMResponse, ProviderCapability, TokenUsage
from src.providers.logging_provider import LoggingProvider


class _StubProvider(BaseProvider):
    def __init__(self, responses: List[LLMResponse]) -> None:
        self._responses = list(responses)
        self.requests: List[LLMRequest] = []
        self.model_id = "stub-model"

    @property
    def name(self) -> str:
        return "stub"

    @property
    def capabilities(self) -> "frozenset[ProviderCapability]":
        return frozenset({ProviderCapability.TEXT_COMPLETION})

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return self._responses.pop(0)


def _response(content: str, tokens: int = 10) -> LLMResponse:
    return LLMResponse(
        content=content,
        structured_data=None,
        usage=TokenUsage(tokens, tokens, tokens * 2),
        provider_name="stub",
        model_id="stub-model",
    )


def _wrapped(content: str, tokens: int = 10) -> "tuple[LoggingProvider, io.StringIO, _StubProvider]":
    inner = _StubProvider([_response(content, tokens)])
    sink = io.StringIO()
    return LoggingProvider(inner, label="rater_1", file=sink), sink, inner


# ── 透传与统计 ────────────────────────────────────────────────────────────────


def test_passes_response_through_unchanged() -> None:
    provider, _sink, inner = _wrapped('{"proposed_score": 4}')

    response = provider.complete(LLMRequest(prompt="hi"))

    assert response.content == '{"proposed_score": 4}'
    assert len(inner.requests) == 1


def test_accumulates_call_count_and_tokens() -> None:
    inner = _StubProvider([_response("a", 5), _response("b", 7)])
    provider = LoggingProvider(inner, file=io.StringIO())

    provider.complete(LLMRequest(prompt="one"))
    provider.complete(LLMRequest(prompt="two"))

    assert provider.call_count == 2
    assert provider.total_tokens == (5 * 2) + (7 * 2)
    assert provider.total_elapsed >= 0.0


def test_logs_a_line_before_and_after_each_call() -> None:
    provider, sink, _inner = _wrapped('{"proposed_score": 4}', tokens=11)

    provider.complete(LLMRequest(prompt="hello"))

    out = sink.getvalue()
    assert "rater_1" in out
    assert "#1" in out
    assert "11+11=22 tok" in out
    assert "score=4" in out


def test_exceptions_from_inner_propagate() -> None:
    class _Boom(_StubProvider):
        def complete(self, request: LLMRequest) -> LLMResponse:
            raise RuntimeError("boom")

    provider = LoggingProvider(_Boom([]), file=io.StringIO())

    # inner 的异常必须原样抛出，不能被日志包装吞掉
    with pytest.raises(RuntimeError, match="boom"):
        provider.complete(LLMRequest(prompt="x"))


# ── 预览渲染（v2 证据一律是 unit_ids，不复述原文） ────────────────────────────


def _preview_line(content: str) -> str:
    """跑一次真实调用，取日志里的"调用后"那行——预览是它的可观察产物。"""
    provider, sink, _inner = _wrapped(content)
    provider.complete(LLMRequest(prompt="x"))
    return [line for line in sink.getvalue().splitlines() if "✓" in line][0]


def test_preview_shows_score_for_scoring_responses() -> None:
    assert "score=3" in _preview_line(json.dumps({"proposed_score": 3}))


def test_preview_shows_unit_ids_for_select_and_extract_responses() -> None:
    assert "selected_unit_ids=3 [1, 2, 3]" in _preview_line(json.dumps({"selected_unit_ids": [1, 2, 3]}))
    assert "evidence_unit_ids=0 []" in _preview_line(json.dumps({"evidence_unit_ids": []}))


def test_preview_truncates_long_unit_id_lists() -> None:
    line = _preview_line(json.dumps({"selected_unit_ids": list(range(12))}))

    assert "selected_unit_ids=12 [" in line
    assert line.rstrip().endswith("…]")


def test_preview_falls_back_to_snippet_for_non_json() -> None:
    assert '"这是一段自由文本反馈"' in _preview_line("这是一段自由文本反馈")


def test_preview_keeps_the_log_line_single_line() -> None:
    """多行响应不能把日志撑成多行——预览必须把换行折掉。"""
    provider, sink, _inner = _wrapped("第一行\n第二行")
    provider.complete(LLMRequest(prompt="x"))

    assert len([line for line in sink.getvalue().splitlines() if "✓" in line]) == 1


# ── model_id 解析（穿透嵌套包装器） ───────────────────────────────────────────


def test_model_id_resolves_through_nested_wrappers() -> None:
    inner = _StubProvider([_response("x")])
    provider = LoggingProvider(LoggingProvider(inner, file=io.StringIO()), file=io.StringIO())

    assert provider.model_id == "stub-model"


def test_model_id_falls_back_to_question_mark() -> None:
    class _NoModel(BaseProvider):
        @property
        def name(self) -> str:
            return "nomodel"

        @property
        def capabilities(self) -> "frozenset[ProviderCapability]":
            return frozenset()

        def complete(self, request: LLMRequest) -> LLMResponse:
            raise NotImplementedError

    assert LoggingProvider(_NoModel(), file=io.StringIO()).model_id == "?"
