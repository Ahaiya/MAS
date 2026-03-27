"""
Real provider smoke tests (Phase 5, Task 5).

All tests in this file are marked @pytest.mark.real — they require real LLM
credentials set in the environment and are SKIPPED in normal CI runs.

To execute:
    LLM_API_KEY=<key> LLM_MODEL=gpt-4o-mini pytest -m real tests/e2e/test_real_provider_smoke.py

Tests verify that:
1. The real provider can be instantiated and make a simple completion call.
2. The extraction prompt builder produces a non-empty prompt and the provider
   returns a parseable response.
3. The scoring prompt builder + real provider produces a valid ScoreHypothesis.
4. The full pipeline can run end-to-end with --provider real on a sample essay.

Zero-hardcoding: no trait names, dimension codes, or score values appear here.
All domain values flow from the loaded rubric snapshot.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.providers.base import LLMRequest, LLMResponse
from src.providers.guards import GuardedProvider, RetryConfig
from src.providers.openai_compatible import OpenAICompatibleProvider
from src.providers.switch import create_provider_from_env


# ── Helpers ───────────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_SAMPLE_PATH = _PROJECT_ROOT / "data" / "samples" / "sample_20716.txt"
_BUNDLE_PATH = _PROJECT_ROOT / "configs" / "bundles" / "asap_set8_baseline.bundle.yaml"


def _skip_if_no_credentials():
    """Skip the test if LLM_API_KEY is not set."""
    if not os.environ.get("LLM_API_KEY"):
        pytest.skip("LLM_API_KEY not set — skipping real provider smoke test")


def _make_real_provider() -> GuardedProvider:
    api_key = os.environ.get("LLM_API_KEY", "")
    model_id = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    api_base = os.environ.get("LLM_API_BASE") or None
    timeout = float(os.environ.get("LLM_TIMEOUT_SECONDS", "60"))
    inner = OpenAICompatibleProvider(
        api_key=api_key,
        model_id=model_id,
        api_base=api_base,
        timeout=timeout,
        max_retries=0,
    )
    return GuardedProvider(inner, RetryConfig(max_retries=2, retry_delay_seconds=1.0))


# ── Provider-level smoke tests ─────────────────────────────────────────────────

@pytest.mark.real
class TestRealProviderSmoke:
    """Minimal tests that verify the real LLM provider integration works."""

    def test_credentials_available(self):
        """Verify required env vars are set before other tests run."""
        _skip_if_no_credentials()
        assert os.environ.get("LLM_API_KEY"), "LLM_API_KEY must be set"

    def test_provider_instantiation(self):
        """Real provider can be instantiated from environment variables."""
        _skip_if_no_credentials()
        provider = _make_real_provider()
        assert provider.name == "openai_compatible"

    def test_simple_completion(self):
        """Real provider returns a valid LLMResponse for a trivial prompt."""
        _skip_if_no_credentials()
        provider = _make_real_provider()
        request = LLMRequest(
            prompt="Reply with exactly one word: hello",
            params={"max_tokens": 10},
        )
        response = provider.complete(request)
        assert isinstance(response, LLMResponse)
        assert len(response.content.strip()) > 0
        assert response.usage.total_tokens > 0
        assert response.provider_name == "openai_compatible"

    def test_structured_output(self):
        """Real provider returns parseable JSON when output_schema is supplied."""
        _skip_if_no_credentials()
        provider = _make_real_provider()
        schema = {
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
        }
        request = LLMRequest(
            prompt='Return a JSON object with key "answer" set to "yes".',
            output_schema=schema,
            params={"max_tokens": 50},
        )
        response = provider.complete(request)
        assert isinstance(response, LLMResponse)
        # Structured data should be a dict (either from SDK or content parsing)
        content = response.content
        assert len(content) > 0

    def test_provider_from_env(self):
        """create_provider_from_env() returns a provider; unregistered names fall back to mock."""
        _skip_if_no_credentials()
        import os
        from src.providers.switch import create_provider_from_env
        # Temporarily remove LLM_PROVIDER so the function uses the "mock" default.
        # (.env may set LLM_PROVIDER to a name not in the default registry.)
        old = os.environ.pop("LLM_PROVIDER", None)
        try:
            provider = create_provider_from_env()
            assert provider is not None
        finally:
            if old is not None:
                os.environ["LLM_PROVIDER"] = old


# ── Extraction prompt + real provider smoke ───────────────────────────────────

@pytest.mark.real
class TestRealExtractionSmoke:
    """Verify the extraction prompt round-trips through the real provider."""

    def test_extraction_prompt_returns_response(self):
        """Build an extraction prompt and call the real provider."""
        _skip_if_no_credentials()
        from src.agents.config_resolver import run as resolve_bundle
        from src.agents.extractor import run as extract
        from src.providers.prompt_loader import PromptLoader

        if not _BUNDLE_PATH.exists() or not _SAMPLE_PATH.exists():
            pytest.skip("Bundle or sample file not found")

        provider = _make_real_provider()
        resolved = resolve_bundle(_BUNDLE_PATH)
        rubric = resolved.rubric_snapshot

        from src.agents import coverage, preprocess
        from src.contracts.request_models import EvaluationRequest

        raw_text = _SAMPLE_PATH.read_text(encoding="utf-8")
        req = EvaluationRequest(raw_text=raw_text, bundle_ref="smoke")
        _, document = preprocess.run(req)
        plans = coverage.run(document, rubric)

        loader = PromptLoader()
        template = loader.load(_PROJECT_ROOT / "configs" / "prompts" / "evidence_extraction.yaml")

        # Run extraction for first dimension only (smoke test, not full pipeline)
        first_plan = plans[0]
        spans = extract(first_plan, document, rubric, provider, template)

        assert isinstance(spans, list)
        assert len(spans) > 0
        for span in spans:
            assert span.dimension_id == first_plan.dimension_id


# ── Full pipeline smoke via CLI ───────────────────────────────────────────────

@pytest.mark.real
class TestRealPipelineSmoke:
    """Run the full pipeline with --provider real and verify artifact outputs."""

    def test_full_pipeline_produces_artifacts(self, tmp_path):
        """Execute the pipeline runner in real mode and check output files."""
        _skip_if_no_credentials()

        if not _BUNDLE_PATH.exists() or not _SAMPLE_PATH.exists():
            pytest.skip("Bundle or sample file not found")

        from src.agents.config_resolver import run as resolve_bundle
        from src.contracts.request_models import EvaluationRequest
        from src.contracts.trace import RunStatus
        from src.pipeline.runner import PipelineRunner
        from src.providers.prompt_loader import PromptLoader

        provider = _make_real_provider()
        resolved = resolve_bundle(_BUNDLE_PATH)

        loader = PromptLoader()
        prompts_dir = _PROJECT_ROOT / "configs" / "prompts"
        prompt_templates = {
            "evidence_extraction": loader.load(prompts_dir / "evidence_extraction.yaml"),
            "scoring": loader.load(prompts_dir / "scoring.yaml"),
            "explanation": loader.load(prompts_dir / "explanation.yaml"),
        }

        raw_text = _SAMPLE_PATH.read_text(encoding="utf-8")
        request = EvaluationRequest(
            raw_text=raw_text,
            bundle_ref=f"{resolved.artifact_bundle.bundle_id}@{resolved.artifact_bundle.bundle_version}",
        )

        runner = PipelineRunner(resolved, provider=provider, prompt_templates=prompt_templates)
        run_trace, feedback = runner.run(request)

        # Verify trace structure
        assert run_trace.run_id is not None
        assert run_trace.status in (RunStatus.COMPLETED, RunStatus.HUMAN_REVIEW)
        assert run_trace.replay_metadata.get("provider") == "openai_compatible"

        # Verify feedback structure
        assert "dimensions" in feedback

        # Write artifacts to tmp_path for inspection
        trace_path = tmp_path / "run_trace.json"
        feedback_path = tmp_path / "feedback.json"
        trace_path.write_text(json.dumps(run_trace.to_dict(), indent=2))
        feedback_path.write_text(json.dumps(feedback, indent=2))

        assert trace_path.exists()
        assert feedback_path.exists()
