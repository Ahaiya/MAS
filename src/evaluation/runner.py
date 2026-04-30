"""Single-document evaluation runner used by the main eval CLI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.contracts.artifact_bundle import ResolvedArtifactBundle
from src.contracts.request_models import EvaluationRequest
from src.debug import DebugBundleWriter
from src.pipeline.runner import PipelineRunner
from src.providers.logging_provider import LoggingProvider
from src.providers.prompt_loader import PromptTemplate


@dataclass
class RunResult:
    essay_id: str
    success: bool
    output_dir: Path
    trace_dict: dict[str, Any]
    feedback_dict: dict[str, Any]


def _set_debug_writer(
    log_providers: list[LoggingProvider],
    debug_writer: DebugBundleWriter | None,
) -> None:
    for provider in log_providers:
        provider.set_debug_writer(debug_writer)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def run_single_eval(
    essay_id: str,
    essay_text: str,
    tsv_row: dict[str, Any] | None,
    resolved: ResolvedArtifactBundle,
    default_provider: Any | None,
    rater_providers: dict[str, Any],
    stage_providers: dict[str, Any],
    log_providers: list[LoggingProvider],
    prompt_templates: dict[str, PromptTemplate],
    output_dir: Path,
    verbose: bool = False,
    debug_bundle: bool = False,
) -> RunResult:
    """Run one document through the pipeline and persist standard artifacts."""
    _ = verbose
    output_dir.mkdir(parents=True, exist_ok=True)

    debug_writer: DebugBundleWriter | None = None
    if debug_bundle:
        debug_writer = DebugBundleWriter(
            output_dir / "_debug",
            session_metadata={
                "essay_id": essay_id,
                "output_dir": str(output_dir),
            },
        )

    _set_debug_writer(log_providers, debug_writer)
    try:
        request = EvaluationRequest(
            raw_text=essay_text,
            bundle_ref=(
                f"{resolved.artifact_bundle.bundle_id}"
                f"@{resolved.artifact_bundle.bundle_version}"
            ),
            metadata={
                "essay_id": essay_id,
                "source": str(output_dir),
                "has_human_scores": bool(tsv_row),
            },
        )

        runner = PipelineRunner(
            resolved,
            provider=default_provider,
            rater_providers=rater_providers,
            stage_providers=stage_providers,
            prompt_templates=prompt_templates,
            debug_writer=debug_writer,
        )
        run_trace, feedback = runner.run(request)

        trace_payload = run_trace.to_dict()
        feedback_payload = feedback
        hypotheses_payload = {
            "run_id": run_trace.run_id,
            "hypotheses": [hypothesis.to_dict() for hypothesis in runner.last_hypotheses],
        }
        spans_payload = {
            "run_id": run_trace.run_id,
            "evidence_spans": [span.to_dict() for span in runner.last_spans],
        }
        conflicts_payload = {
            "run_id": run_trace.run_id,
            "conflicts": [conflict.to_dict() for conflict in runner.last_conflicts],
        }
        adjudication_payload = {
            "run_id": run_trace.run_id,
            "adjudication_records": [
                record.to_dict() for record in runner.last_adjudication_records
            ],
        }

        document = runner.last_document
        observations_payload = {
            "run_id": run_trace.run_id,
            "document_id": document.document_id if document is not None else None,
            "observations": [obs.to_dict() for obs in runner.last_observations],
            "coverage_plans": [plan.to_dict() for plan in runner.last_plans],
            "text_units": [
                unit.to_dict()
                for unit in (document.text_units if document is not None else [])
            ],
        }

        _write_json(output_dir / "run_trace.json", trace_payload)
        _write_json(output_dir / "feedback.json", feedback_payload)
        _write_json(output_dir / "hypotheses.json", hypotheses_payload)
        _write_json(output_dir / "evidence_spans.json", spans_payload)
        _write_json(output_dir / "observations.json", observations_payload)
        _write_json(output_dir / "conflicts.json", conflicts_payload)
        _write_json(output_dir / "adjudication_records.json", adjudication_payload)

        if debug_writer is not None and debug_writer.output_dir is not None:
            debug_writer.write_primary_artifact(
                artifact_name="run_trace",
                data=trace_payload,
                summary="Canonical run trace for this evaluation",
            )
            debug_writer.write_primary_artifact(
                artifact_name="feedback",
                data=feedback_payload,
                summary="Final feedback payload",
            )
            debug_writer.write_primary_artifact(
                artifact_name="hypotheses",
                data=hypotheses_payload,
                summary=f"{len(hypotheses_payload['hypotheses'])} score hypotheses",
            )
            debug_writer.write_primary_artifact(
                artifact_name="evidence_spans",
                data=spans_payload,
                summary=f"{len(spans_payload['evidence_spans'])} evidence spans",
            )
            debug_writer.write_primary_artifact(
                artifact_name="observations",
                data=observations_payload,
                summary=f"{len(observations_payload['observations'])} observations",
            )
            debug_writer.write_primary_artifact(
                artifact_name="conflicts",
                data=conflicts_payload,
                summary=f"{len(conflicts_payload['conflicts'])} conflicts",
            )
            debug_writer.write_primary_artifact(
                artifact_name="adjudication_records",
                data=adjudication_payload,
                summary=(
                    f"{len(adjudication_payload['adjudication_records'])} adjudication records"
                ),
            )
            debug_writer.emit_event(
                "run_finished",
                status=run_trace.status.value,
                terminal_validation_passed=run_trace.terminal_validation_passed,
            )

        return RunResult(
            essay_id=essay_id,
            success=(run_trace.status.value == "completed"),
            output_dir=output_dir,
            trace_dict=trace_payload,
            feedback_dict=feedback_payload,
        )
    finally:
        if debug_writer is not None and debug_writer.output_dir is not None:
            debug_writer.finalize()
        _set_debug_writer(log_providers, None)


__all__ = ["RunResult", "run_single_eval"]
