"""Programmatic batch evaluation API for outer-loop experimentation."""

from __future__ import annotations

import csv
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.agents.config_resolver import run as resolve_bundle
from src.contracts.artifact_bundle import ProviderEntryConfig, ResolvedArtifactBundle
from src.contracts.request_models import EvaluationRequest
from src.debug import DebugBundleWriter
from src.pipeline.runner import PipelineRunner
from src.providers.factory import build_provider, build_provider_map
from src.providers.logging_provider import LoggingProvider
from src.providers.prompt_loader import PromptLoader, PromptTemplate

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class RunResult:
    essay_id: str
    success: bool
    output_dir: Path
    trace_dict: dict[str, Any]
    feedback_dict: dict[str, Any]


RunSingleFn = Callable[..., RunResult]


def _load_tsv(tsv_path: Path) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with tsv_path.open(encoding="latin-1") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            essay_id = (row.get("essay_id") or "").strip()
            if essay_id:
                rows[essay_id] = row
    return rows


def _load_md_file(md_path: Path) -> dict[str, dict[str, str]]:
    """Load a single .md file as a one-entry sample dict."""
    essay_id = md_path.stem
    essay_text = md_path.read_text(encoding="utf-8")
    return {essay_id: {"essay": essay_text, "essay_id": essay_id}}


def _load_md_dir(md_dir: Path) -> dict[str, dict[str, str]]:
    """Load all .md files in a directory as sample rows keyed by file stem."""
    rows: dict[str, dict[str, str]] = {}
    for md_file in sorted(md_dir.glob("*.md")):
        essay_id = md_file.stem
        essay_text = md_file.read_text(encoding="utf-8")
        rows[essay_id] = {"essay": essay_text, "essay_id": essay_id}
    return rows


def _load_samples(samples_path: Path) -> dict[str, dict[str, str]]:
    """Load samples from a .md file, a directory of .md files, or a TSV file."""
    if samples_path.is_dir():
        return _load_md_dir(samples_path)
    if samples_path.suffix.lower() == ".md":
        return _load_md_file(samples_path)
    return _load_tsv(samples_path)


def _wrap_providers(
    default_provider: Any | None,
    rater_providers: dict[str, Any],
    stage_providers: dict[str, Any],
) -> tuple[Any | None, dict[str, Any], dict[str, Any], list[LoggingProvider]]:
    log_providers: list[LoggingProvider] = []
    wrapped_default = None
    if default_provider is not None:
        wrapped_default = LoggingProvider(default_provider, label="default")
        log_providers.append(wrapped_default)

    wrapped_raters: dict[str, Any] = {}
    for rater_id, provider in rater_providers.items():
        wrapped = LoggingProvider(provider, label=rater_id)
        wrapped_raters[rater_id] = wrapped
        log_providers.append(wrapped)

    wrapped_stages: dict[str, Any] = {}
    for stage, provider in stage_providers.items():
        wrapped = LoggingProvider(provider, label=stage)
        wrapped_stages[stage] = wrapped
        log_providers.append(wrapped)

    return wrapped_default, wrapped_raters, wrapped_stages, log_providers


def _set_debug_writer(
    log_providers: list[LoggingProvider],
    debug_writer: DebugBundleWriter | None,
) -> None:
    for provider in log_providers:
        provider.set_debug_writer(debug_writer)


def _init_providers(
    resolved: ResolvedArtifactBundle,
) -> tuple[Any | None, dict[str, Any], dict[str, Any], list[LoggingProvider]]:
    default_provider = None
    rater_providers: dict[str, Any] = {}
    stage_providers: dict[str, Any] = {}

    if resolved.provider_config is not None:
        default_provider, rater_providers, stage_providers = build_provider_map(
            resolved.provider_config
        )
    else:
        default_provider = build_provider(ProviderEntryConfig(api_key_env="LLM_API_KEY"))

    return _wrap_providers(default_provider, rater_providers, stage_providers)


def _load_prompt_templates() -> dict[str, PromptTemplate]:
    loader = PromptLoader()
    templates: dict[str, PromptTemplate] = {}
    prompts_root = _PROJECT_ROOT / "configs" / "prompts"

    for name, filename in [
        ("evidence_extraction", "evidence_extraction.yaml"),
        ("scoring", "scoring.yaml"),
        ("explanation", "explanation.yaml"),
        ("chunking", "chunking.yaml"),
        ("dimension_relevance", "dimension_relevance.yaml"),
    ]:
        path = prompts_root / filename
        if path.exists():
            templates[name] = loader.load(path)

    override_dir = prompts_root / "evidence_extraction_overrides"
    if override_dir.exists():
        for override_path in sorted(override_dir.glob("*.yaml")):
            dim_id = override_path.stem
            templates[f"evidence_extraction_override_{dim_id}"] = loader.load(override_path)

    scoring_override_dir = prompts_root / "scoring_overrides"
    if scoring_override_dir.exists():
        for override_path in sorted(scoring_override_dir.glob("*.yaml")):
            dim_id = override_path.stem
            templates[f"scoring_override_{dim_id}"] = loader.load_with_override(
                "scoring",
                dim_id,
                prompts_root=prompts_root,
            )

    explanation_override_dir = prompts_root / "explanation_overrides"
    if explanation_override_dir.exists():
        for override_path in sorted(explanation_override_dir.glob("*.yaml")):
            dim_id = override_path.stem
            templates[f"explanation_override_{dim_id}"] = loader.load_with_override(
                "explanation",
                dim_id,
                prompts_root=prompts_root,
            )
    return templates


def _normalize_iter_id(iter_id: str | None) -> str | None:
    if iter_id is None:
        return None
    stripped = str(iter_id).strip()
    if not stripped:
        return None
    if stripped.startswith("iter_"):
        return stripped
    return f"iter_{stripped}"


def _output_dir_for(
    *,
    output_base: Path,
    essay_id: str,
    iter_id: str | None,
) -> Path:
    normalized_iter = _normalize_iter_id(iter_id)
    if normalized_iter is None:
        return output_base / essay_id
    return output_base / normalized_iter / essay_id


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
    """Run one essay through the pipeline and persist standard artifacts."""
    _ = verbose  # kept for signature compatibility with script-level wrappers
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


def batch_eval(
    sample_ids: list[str],
    bundle_path: Path,
    tsv_path: Path,
    output_base: Path,
    iter_id: str | None = None,
    *,
    debug_bundle: bool = False,
    delay_seconds: float = 0.0,
    verbose: bool = False,
    run_single_fn: RunSingleFn | None = None,
    rows_by_id: dict[str, dict[str, str]] | None = None,
    resolved_bundle: ResolvedArtifactBundle | None = None,
    default_provider: Any | None = None,
    rater_providers: dict[str, Any] | None = None,
    stage_providers: dict[str, Any] | None = None,
    log_providers: list[LoggingProvider] | None = None,
    prompt_templates: dict[str, PromptTemplate] | None = None,
) -> list[RunResult]:
    """Evaluate a batch of essay IDs and return per-sample results.

    Output layout:
    - iter_id is None: {output_base}/{essay_id}/
    - iter_id is set : {output_base}/iter_{iter_id}/{essay_id}/
      (if iter_id already starts with "iter_", it is used as-is)
    """

    if not sample_ids:
        return []

    output_base = Path(output_base)
    rows = rows_by_id if rows_by_id is not None else _load_samples(Path(tsv_path))
    resolved = resolved_bundle if resolved_bundle is not None else resolve_bundle(bundle_path)

    if prompt_templates is None:
        prompt_templates = _load_prompt_templates()

    if (
        rater_providers is None
        or stage_providers is None
        or log_providers is None
        or (default_provider is None and resolved.provider_config is None)
    ):
        (
            default_provider,
            rater_providers,
            stage_providers,
            log_providers,
        ) = _init_providers(resolved)

    run_fn = run_single_fn or run_single_eval
    results: list[RunResult] = []
    total = len(sample_ids)

    for idx, raw_sample_id in enumerate(sample_ids, start=1):
        sample_id = str(raw_sample_id)
        row = rows.get(sample_id)
        if row is None:
            raise ValueError(f"sample_id '{sample_id}' not found in samples source: {tsv_path}")

        essay_text = row.get("essay") or ""
        if not essay_text.strip():
            raise ValueError(f"sample_id '{sample_id}' has empty essay text in: {tsv_path}")

        sample_output_dir = _output_dir_for(
            output_base=output_base,
            essay_id=sample_id,
            iter_id=iter_id,
        )

        result = run_fn(
            essay_id=sample_id,
            essay_text=essay_text,
            tsv_row=row,
            resolved=resolved,
            default_provider=default_provider,
            rater_providers=rater_providers or {},
            stage_providers=stage_providers or {},
            log_providers=log_providers or [],
            prompt_templates=prompt_templates,
            output_dir=sample_output_dir,
            verbose=verbose,
            debug_bundle=debug_bundle,
        )
        results.append(result)

        if delay_seconds > 0 and idx < total:
            time.sleep(delay_seconds)

    return results
