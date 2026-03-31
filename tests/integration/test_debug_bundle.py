from __future__ import annotations

import json
from pathlib import Path

from src.agents.config_resolver import run as resolve_bundle
from src.contracts.request_models import EvaluationRequest
from src.debug import DebugBundleWriter
from src.pipeline.runner import PipelineRunner
from src.providers.logging_provider import LoggingProvider
from src.providers.mock import MockProvider
from src.providers.prompt_loader import PromptLoader


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLE_PATH = _PROJECT_ROOT / "configs" / "bundles" / "asap_set8_baseline.bundle.yaml"
_PROMPTS_DIR = _PROJECT_ROOT / "configs" / "prompts"
_SAMPLE_PATH = _PROJECT_ROOT / "data" / "samples" / "sample_20716.txt"


def _load_prompt_templates() -> dict:
    loader = PromptLoader()
    return {
        "chunking": loader.load(_PROMPTS_DIR / "chunking.yaml"),
        "dimension_relevance": loader.load(_PROMPTS_DIR / "dimension_relevance.yaml"),
        "evidence_extraction": loader.load(_PROMPTS_DIR / "evidence_extraction.yaml"),
        "scoring": loader.load(_PROMPTS_DIR / "scoring.yaml"),
        "explanation": loader.load(_PROMPTS_DIR / "explanation.yaml"),
    }


def test_debug_bundle_captures_node_and_llm_artifacts(tmp_path):
    resolved = resolve_bundle(_BUNDLE_PATH)
    writer = DebugBundleWriter(tmp_path / "debug", session_metadata={"essay_id": "20716"})
    provider = LoggingProvider(MockProvider(), label="default", debug_writer=writer)
    runner = PipelineRunner(
        resolved,
        provider=provider,
        prompt_templates=_load_prompt_templates(),
        debug_writer=writer,
    )

    request = EvaluationRequest(
        raw_text=_SAMPLE_PATH.read_text(encoding="utf-8"),
        bundle_ref=f"{resolved.artifact_bundle.bundle_id}@{resolved.artifact_bundle.bundle_version}",
        metadata={"essay_id": "20716"},
    )

    run_trace, feedback = runner.run(request)
    writer.write_primary_artifact(
        artifact_name="run_trace",
        data=run_trace.to_dict(),
        summary="Run trace",
    )
    writer.write_primary_artifact(
        artifact_name="feedback",
        data=feedback,
        summary="Feedback",
    )
    writer.emit_event(
        "run_finished",
        status=run_trace.status.value,
        terminal_validation_passed=run_trace.terminal_validation_passed,
    )
    bundle_dir = writer.finalize()

    assert (bundle_dir / "manifest.json").exists()
    assert (bundle_dir / "events.jsonl").exists()
    assert (bundle_dir / "summary.json").exists()
    assert (bundle_dir / "viewer" / "index.html").exists()

    summary = json.loads((bundle_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["node_count"] >= 6
    assert summary["llm_call_count"] > 0

    node_ids = {node["node_id"] for node in summary["nodes"]}
    assert "node_preprocess" in node_ids
    assert "node_feedback" in node_ids

    first_call = summary["llm_calls"][0]
    call_payload = json.loads((bundle_dir / first_call["call_path"]).read_text(encoding="utf-8"))
    assert call_payload["request"]["prompt_path"] is not None
    assert call_payload["debug_context"]["node_id"] is not None
    assert Path(bundle_dir / call_payload["request"]["prompt_path"]).exists()
