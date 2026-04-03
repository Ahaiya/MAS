"""
Unit tests for LLM-assisted chunker (Stage C).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from src.agents import chunker
from src.contracts.request_models import EvaluationRequest
from src.providers.base import BaseProvider, LLMRequest, LLMResponse, ProviderCapability, TokenUsage
from src.providers.prompt_loader import PromptLoader


class _FixedProvider(BaseProvider):
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload
        self.calls = 0

    @property
    def name(self) -> str:
        return "fixed"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.TEXT_COMPLETION, ProviderCapability.STRUCTURED_OUTPUT})

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content="{}",
            structured_data=self._payload,
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            provider_name=self.name,
            model_id="fixed-v1",
        )


def _load_chunking_template():
    root = Path(__file__).resolve().parents[3]
    return PromptLoader().load(root / "configs" / "prompts" / "chunking.yaml")


def test_chunking_requires_real_provider_and_template():
    request = EvaluationRequest(
        raw_text="A first sentence. A second sentence.",
        bundle_ref="bundle://test/v1",
        request_id="req-chunker-001",
    )
    with pytest.raises(RuntimeError, match="Semantic chunking failed"):
        chunker.run(request, provider=None, template=None)  # type: ignore[arg-type]


def test_short_doc_calls_llm_once():
    request = EvaluationRequest(
        raw_text="First part text. Second part text.",
        bundle_ref="bundle://test/v1",
        request_id="req-chunker-002",
    )
    provider = _FixedProvider(
        {
            "chunks": [
                {"id": "c0", "title": "Opening", "text": "First part text."},
                {"id": "c1", "title": "Follow-up", "text": "Second part text."},
            ]
        }
    )
    template = _load_chunking_template()

    _, doc = chunker.run(request, provider=provider, template=template, token_threshold=4000)
    assert provider.calls == 1
    assert len(doc.text_units) == 2
    assert all(u.chunk_method == "llm_semantic" for u in doc.text_units)


def test_long_doc_uses_hierarchical_branch():
    long_text = " ".join(f"word{i}" for i in range(4201))
    request = EvaluationRequest(
        raw_text=long_text,
        bundle_ref="bundle://test/v1",
        request_id="req-chunker-003",
    )
    provider = _FixedProvider(
        {
            "chunks": [
                {"id": "c0", "title": "Block 1", "text": "placeholder"},
                {"id": "c1", "title": "Block 2", "text": "placeholder"},
                {"id": "c2", "title": "Block 3", "text": "placeholder"},
            ]
        }
    )
    template = _load_chunking_template()

    _, doc = chunker.run(request, provider=provider, template=template, token_threshold=4000)
    assert provider.calls == 1
    assert len(doc.text_units) == 3
    assert all(u.chunk_method == "llm_hierarchical" for u in doc.text_units)
    assert doc.token_estimate >= 4000


def test_llm_json_parse_failure_raises_error():
    request = EvaluationRequest(
        raw_text="First sentence. Second sentence.",
        bundle_ref="bundle://test/v1",
        request_id="req-chunker-004",
    )
    provider = _FixedProvider({"unexpected": "shape"})
    template = _load_chunking_template()

    with pytest.raises(RuntimeError, match="Semantic chunking failed"):
        chunker.run(request, provider=provider, template=template, token_threshold=4000)
    assert provider.calls == 1


def test_chunk_title_populated_in_text_units():
    request = EvaluationRequest(
        raw_text="Alpha sentence. Beta sentence.",
        bundle_ref="bundle://test/v1",
        request_id="req-chunker-005",
    )
    provider = _FixedProvider(
        {
            "chunks": [
                {"id": "c0", "title": "Alpha Chunk", "text": "Alpha sentence."},
                {"id": "c1", "title": "Beta Chunk", "text": "Beta sentence."},
            ]
        }
    )
    template = _load_chunking_template()

    _, doc = chunker.run(request, provider=provider, template=template, token_threshold=4000)
    titles = [u.chunk_title for u in doc.text_units]
    assert titles == ["Alpha Chunk", "Beta Chunk"]


def test_token_estimate_chinese_text():
    text = "这是一个中文句子。"
    assert chunker._estimate_tokens(text) == math.ceil(len(text) * 1.5)


def test_dialogue_markdown_units_are_source_annotated():
    raw_text = """# sample

## 记录
### session_init
标签: x
时间: 2025/1/1 10:00:00
```text
人类原始想法。
```

### training_chat_response
标签: chat
时间: 2025/1/1 10:00:10
```text
这是 AI 的总结。
```"""
    request = EvaluationRequest(
        raw_text=raw_text,
        bundle_ref="bundle://test/v1",
        request_id="req-chunker-dialogue",
    )
    provider = _FixedProvider(
        {
            "chunks": [
                {"id": "c0", "title": "Human", "text": "人类原始想法。"},
                {"id": "c1", "title": "AI", "text": "这是 AI 的总结。"},
            ]
        }
    )
    template = _load_chunking_template()

    _, doc = chunker.run(request, provider=provider, template=template, token_threshold=4000)
    assert doc.document_type == "dialogue"
    assert [u.source_type for u in doc.text_units] == ["human", "ai"]
    assert [u.source_label for u in doc.text_units] == ["session_init", "training_chat_response"]
    assert len(doc.document_metadata.get("source_spans") or []) == 2
