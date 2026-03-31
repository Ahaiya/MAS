from __future__ import annotations

import io
import json
from pathlib import Path

from src.debug.bundle import DebugBundleWriter
from src.providers.base import (
    BaseProvider,
    LLMRequest,
    LLMResponse,
    ProviderCapability,
    TokenUsage,
)
from src.providers.guards import GuardedProvider, RetryConfig
from src.providers.logging_provider import LoggingProvider


class _FixedModelProvider(BaseProvider):
    """Minimal provider exposing a fixed _model_id for wrapper-chain tests."""

    def __init__(self, model_id: str) -> None:
        self._model_id = model_id

    @property
    def name(self) -> str:
        return "fixed"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.TEXT_COMPLETION})

    def complete(self, request: LLMRequest) -> LLMResponse:
        resolved_model = request.model_id or self._model_id
        return LLMResponse(
            content="ok",
            structured_data=None,
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            provider_name=self.name,
            model_id=resolved_model,
        )


def _start_debug_writer(tmp_path: Path) -> DebugBundleWriter:
    writer = DebugBundleWriter(tmp_path / "debug")
    writer.start_run(
        run_id="run-test-model-id",
        request={"request_id": "req-1", "metadata": {}},
        bundle_id="bundle-test",
        bundle_version="v1",
        provider_mode="real",
    )
    return writer


def test_model_id_propagates_through_guarded_wrapper_to_debug_call(tmp_path: Path):
    writer = _start_debug_writer(tmp_path)
    sink = io.StringIO()
    inner = _FixedModelProvider("deepseek-chat")
    guarded = GuardedProvider(
        inner,
        RetryConfig(max_retries=0, retry_delay_seconds=0.0, timeout_seconds=None),
    )
    provider = LoggingProvider(guarded, label="feedback", file=sink, debug_writer=writer)

    provider.complete(
        LLMRequest(
            prompt="hello",
            metadata={"node_id": "node_feedback", "stage_name": "feedback"},
        )
    )

    assert provider.model_id == "deepseek-chat"
    assert "deepseek-chat" in sink.getvalue()

    call_path = writer.output_dir / "llm_calls" / "call-0001.json"
    call_record = json.loads(call_path.read_text(encoding="utf-8"))
    assert call_record["model_id"] == "deepseek-chat"


def test_request_model_override_wins_for_call_level_model_id(tmp_path: Path):
    writer = _start_debug_writer(tmp_path)
    inner = _FixedModelProvider("deepseek-chat")
    guarded = GuardedProvider(
        inner,
        RetryConfig(max_retries=0, retry_delay_seconds=0.0, timeout_seconds=None),
    )
    provider = LoggingProvider(guarded, label="feedback", debug_writer=writer)

    provider.complete(LLMRequest(prompt="hello", model_id="override-model"))

    call_path = writer.output_dir / "llm_calls" / "call-0001.json"
    call_record = json.loads(call_path.read_text(encoding="utf-8"))
    assert call_record["model_id"] == "override-model"
    assert provider.model_id == "deepseek-chat"
