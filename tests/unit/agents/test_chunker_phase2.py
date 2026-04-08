"""
Phase 2 验收测试 — Chunker material_strategy + chunk_size_hint 注入

验证：
1. chunking_policy 中的 material_strategies 按 material_type 正确查找
2. chunk_size_hint 从 policy 正确读取
3. 回落逻辑：policy 为空时使用 document_type 字符串
4. 渲染后 prompt 包含正确的 strategy 文本
5. document_type 不出现在渲染 context 中
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from src.agents import chunker
from src.agents.chunker import (
    _resolve_chunk_size_hint,
    _resolve_material_strategy,
    _render_chunking_prompt,
)
from src.contracts.request_models import EvaluationRequest
from src.providers.base import BaseProvider, LLMRequest, LLMResponse, ProviderCapability, TokenUsage
from src.providers.prompt_loader import PromptLoader


# ── 工具 ──────────────────────────────────────────────────────────────────────

def _load_chunking_template():
    root = Path(__file__).resolve().parents[3]
    return PromptLoader().load(root / "configs" / "prompts" / "chunking.yaml")


class _FixedProvider(BaseProvider):
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload
        self.last_prompt: Optional[str] = None

    @property
    def name(self) -> str:
        return "fixed"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.TEXT_COMPLETION, ProviderCapability.STRUCTURED_OUTPUT})

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.last_prompt = request.prompt
        return LLMResponse(
            content="{}",
            structured_data=self._payload,
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            provider_name=self.name,
            model_id="fixed-v1",
        )


_SAMPLE_POLICY = {
    "policy_id": "test_chunking",
    "document_processing": {
        "token_threshold": 4000,
        "chunk_size_hint": "每个chunk 200-600词，保持对话轮次和主题边界完整",
        "material_strategies": {
            "conversation": "按对话轮次和主题边界切分，保持完整的问答对，不在单轮对话中间断开",
            "code": "按函数、类或模块边界切分",
            "document": "按段落和章节边界切分",
        },
    },
}


# ── _resolve_material_strategy 单元测试 ───────────────────────────────────────

class TestResolveMaterialStrategy:
    def test_uses_material_type_first(self):
        result = _resolve_material_strategy("dialogue", _SAMPLE_POLICY, "conversation")
        assert result == "按对话轮次和主题边界切分，保持完整的问答对，不在单轮对话中间断开"

    def test_falls_back_to_document_type(self):
        result = _resolve_material_strategy("code", _SAMPLE_POLICY, None)
        assert result == "按函数、类或模块边界切分"

    def test_returns_material_type_string_when_no_match(self):
        result = _resolve_material_strategy("unknown", _SAMPLE_POLICY, "unknown_type")
        assert result == "unknown_type"

    def test_returns_document_type_string_when_both_missing(self):
        result = _resolve_material_strategy("dialogue", _SAMPLE_POLICY, None)
        # "dialogue" not in strategies, no material_type override
        assert result == "dialogue"

    def test_returns_document_type_when_policy_none(self):
        result = _resolve_material_strategy("dialogue", None, None)
        assert result == "dialogue"

    def test_returns_material_type_when_policy_none(self):
        result = _resolve_material_strategy("dialogue", None, "conversation")
        assert result == "conversation"

    def test_prefers_material_type_over_document_type_when_both_match(self):
        result = _resolve_material_strategy("code", _SAMPLE_POLICY, "conversation")
        assert result == "按对话轮次和主题边界切分，保持完整的问答对，不在单轮对话中间断开"


# ── _resolve_chunk_size_hint 单元测试 ─────────────────────────────────────────

class TestResolveChunkSizeHint:
    def test_reads_hint_from_policy(self):
        result = _resolve_chunk_size_hint(_SAMPLE_POLICY)
        assert result == "每个chunk 200-600词，保持对话轮次和主题边界完整"

    def test_returns_empty_when_policy_none(self):
        result = _resolve_chunk_size_hint(None)
        assert result == ""

    def test_returns_empty_when_no_document_processing(self):
        result = _resolve_chunk_size_hint({"policy_id": "x"})
        assert result == ""

    def test_returns_empty_when_hint_missing(self):
        policy = {"document_processing": {"token_threshold": 4000}}
        result = _resolve_chunk_size_hint(policy)
        assert result == ""


# ── _render_chunking_prompt 渲染测试 ──────────────────────────────────────────

class TestRenderChunkingPrompt:
    def test_material_strategy_in_prompt(self):
        template = _load_chunking_template()
        prompt = _render_chunking_prompt(
            template,
            material_strategy="按对话轮次切分",
            chunk_size_hint="200-600词",
            word_count=500,
            normalized_text="test text",
        )
        assert "按对话轮次切分" in prompt

    def test_chunk_size_hint_in_prompt(self):
        template = _load_chunking_template()
        prompt = _render_chunking_prompt(
            template,
            material_strategy="策略文本",
            chunk_size_hint="每chunk 200-600词",
            word_count=500,
            normalized_text="test text",
        )
        assert "每chunk 200-600词" in prompt

    def test_word_count_in_prompt(self):
        template = _load_chunking_template()
        prompt = _render_chunking_prompt(
            template,
            material_strategy="strategy",
            chunk_size_hint="hint",
            word_count=1234,
            normalized_text="test",
        )
        assert "1234" in prompt

    def test_normalized_text_in_prompt(self):
        template = _load_chunking_template()
        prompt = _render_chunking_prompt(
            template,
            material_strategy="strategy",
            chunk_size_hint="hint",
            word_count=10,
            normalized_text="unique_marker_text_xyz",
        )
        assert "unique_marker_text_xyz" in prompt

    def test_document_type_not_in_context(self):
        """document_type 不应出现在 prompt 渲染 context 中（模板无此变量）。"""
        template = _load_chunking_template()
        # Should not raise UndefinedError about document_type
        prompt = _render_chunking_prompt(
            template,
            material_strategy="strategy",
            chunk_size_hint="hint",
            word_count=10,
            normalized_text="test",
        )
        assert isinstance(prompt, str)


# ── chunker.run() 集成测试 ────────────────────────────────────────────────────

class TestChunkerRunWithPolicy:
    def _make_request(self, text: str = "First sentence. Second sentence.") -> EvaluationRequest:
        return EvaluationRequest(
            raw_text=text,
            bundle_ref="bundle://test/v1",
            request_id="req-phase2-001",
        )

    def _make_provider(self, chunks=None):
        if chunks is None:
            chunks = [
                {"id": "c0", "title": "Part 1", "text": "First sentence."},
                {"id": "c1", "title": "Part 2", "text": "Second sentence."},
            ]
        return _FixedProvider({"chunks": chunks})

    def test_prompt_contains_conversation_strategy(self):
        template = _load_chunking_template()
        provider = self._make_provider()
        chunker.run(
            self._make_request(),
            provider=provider,
            template=template,
            token_threshold=4000,
            chunking_policy=_SAMPLE_POLICY,
            material_type="conversation",
        )
        assert provider.last_prompt is not None
        assert "按对话轮次和主题边界切分" in provider.last_prompt

    def test_prompt_contains_chunk_size_hint(self):
        template = _load_chunking_template()
        provider = self._make_provider()
        chunker.run(
            self._make_request(),
            provider=provider,
            template=template,
            token_threshold=4000,
            chunking_policy=_SAMPLE_POLICY,
            material_type="conversation",
        )
        assert "每个chunk 200-600词" in provider.last_prompt

    def test_without_policy_uses_fallback(self):
        """无 policy 时不报错，回落到 document_type 字符串作为 material_strategy。"""
        template = _load_chunking_template()
        provider = self._make_provider()
        _, doc = chunker.run(
            self._make_request(),
            provider=provider,
            template=template,
            token_threshold=4000,
            chunking_policy=None,
            material_type=None,
        )
        assert len(doc.text_units) == 2

    def test_material_type_conversation_beats_dialogue(self):
        """material_type='conversation' 优先于 document_type='dialogue'。"""
        template = _load_chunking_template()
        # Create a dialogue-structured request
        dialogue_text = """# sample
## 记录
### session_init
标签: x
时间: 2025/1/1 10:00:00
```text
学生的想法。
```
### training_chat_response
标签: chat
时间: 2025/1/1 10:00:10
```text
AI的回答。
```"""
        provider = _FixedProvider(
            {
                "chunks": [
                    {"id": "c0", "title": "Human", "text": "学生的想法。"},
                    {"id": "c1", "title": "AI", "text": "AI的回答。"},
                ]
            }
        )
        chunker.run(
            EvaluationRequest(
                raw_text=dialogue_text,
                bundle_ref="bundle://test/v1",
                request_id="req-conv-vs-dialogue",
            ),
            provider=provider,
            template=template,
            token_threshold=4000,
            chunking_policy=_SAMPLE_POLICY,
            material_type="conversation",
        )
        assert "按对话轮次和主题边界切分" in provider.last_prompt
