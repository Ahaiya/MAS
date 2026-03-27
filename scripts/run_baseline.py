#!/usr/bin/env python3
"""Run MAS evaluation baseline.

This script executes the full multi-agent evaluation pipeline on a given text input.
Supports --provider mock (deterministic, no LLM calls) for baseline validation.
"""

import json
import sys
from pathlib import Path

import os
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
load_dotenv()

# Ensure project root is on sys.path when run as `python scripts/run_baseline.py`
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import os
from pathlib import Path as _Path

from src.agents.config_resolver import run as resolve_bundle
from src.contracts.request_models import EvaluationRequest
from src.pipeline.runner import PipelineRunner
from src.providers.prompt_loader import PromptLoader

import typer

app = typer.Typer(
    name="run-baseline",
    help="Run MAS evaluation baseline on input text.",
)


@app.command()
def main(
    bundle: Path = typer.Option(
        ...,
        "--bundle",
        "-b",
        help="Path to configuration bundle YAML file.",
        exists=True,
    ),
    provider: str = typer.Option(
        "mock",
        "--provider",
        "-p",
        help="LLM provider to use: 'mock' (default) or 'real'.",
    ),
    input_file: Path = typer.Option(
        ...,
        "--input-file",
        "-i",
        help="Path to input text file (student essay, report, etc.).",
        exists=True,
    ),
    output_dir: Path = typer.Option(
        ...,
        "--output-dir",
        "-o",
        help="Directory to write evaluation results and artifacts.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed execution logs.",
    ),
) -> None:
    """Run the MAS baseline evaluation pipeline (mock or real provider)."""
    if provider not in ("mock", "real"):
        typer.echo(
            f"Error: provider '{provider}' is not supported. Use 'mock' or 'real'.",
            err=True,
        )
        raise typer.Exit(code=1)

    if verbose:
        typer.echo("=" * 60)
        typer.echo("MAS Baseline Evaluation Pipeline ")
        typer.echo("=" * 60)
        typer.echo(f"Bundle:   {bundle}")
        typer.echo(f"Provider: {provider}")
        typer.echo(f"Input:    {input_file}")
        typer.echo(f"Output:   {output_dir}")
        typer.echo("=" * 60)

    # Prepare output directory
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Read input text
    raw_text = Path(input_file).read_text(encoding="utf-8")
    if verbose:
        typer.echo(f"Input size: {len(raw_text)} characters")

    # Resolve bundle
    if verbose:
        typer.echo("Resolving bundle...")
    resolved = resolve_bundle(bundle)
    if verbose:
        typer.echo(f"Bundle resolved: {resolved.get_version_info()}")

    # Build request
    request = EvaluationRequest(
        raw_text=raw_text,
        bundle_ref=f"{resolved.artifact_bundle.bundle_id}@{resolved.artifact_bundle.bundle_version}",
    )

    # Configure provider(s)
    default_provider = None
    rater_providers: dict = {}
    stage_providers: dict = {}
    prompt_templates = {}

    if provider == "real":
        from src.providers.factory import build_provider, build_provider_map

        if resolved.provider_config is not None:
            # Path B: bundle-driven — build per-rater and per-stage providers from config
            try:
                default_provider, rater_providers, stage_providers = build_provider_map(
                    resolved.provider_config
                )
            except ValueError as exc:
                typer.echo(f"Error: {exc}", err=True)
                raise typer.Exit(code=1)
            if verbose:
                typer.echo(
                    f"Bundle provider_config loaded: "
                    f"{len(rater_providers)} rater(s), "
                    f"{len(stage_providers)} stage(s)"
                )
        else:
            # Legacy fallback: single provider from global LLM_* env vars
            from src.contracts.artifact_bundle import ProviderEntryConfig
            legacy_entry = ProviderEntryConfig(api_key_env="LLM_API_KEY")
            try:
                default_provider = build_provider(legacy_entry)
            except ValueError as exc:
                typer.echo(f"Error: {exc}", err=True)
                raise typer.Exit(code=1)
            if verbose:
                typer.echo("No provider_config in bundle — using global LLM_* env vars.")

        # Load prompt templates
        loader = PromptLoader()
        configs_prompts = _Path(__file__).parent.parent / "configs" / "prompts"
        for name, filename in [
            ("evidence_extraction", "evidence_extraction.yaml"),
            ("scoring", "scoring.yaml"),
            ("explanation", "explanation.yaml"),
        ]:
            tpl_path = configs_prompts / filename
            if tpl_path.exists():
                prompt_templates[name] = loader.load(tpl_path)
        if verbose:
            typer.echo(f"Loaded {len(prompt_templates)} prompt template(s)")

    # Run pipeline
    if verbose:
        typer.echo("Running pipeline...")
    runner = PipelineRunner(
        resolved,
        provider=default_provider,
        rater_providers=rater_providers,
        stage_providers=stage_providers,
        prompt_templates=prompt_templates,
    )
    run_trace, feedback = runner.run(request)

    if verbose:
        typer.echo(f"Pipeline status: {run_trace.status.value}")
        typer.echo(f"Terminal validation: {run_trace.terminal_validation_passed}")
        typer.echo(f"Node traces: {len(run_trace.node_traces)}")

    # Write outputs
    trace_path = output_dir / "run_trace.json"
    feedback_path = output_dir / "feedback.json"

    trace_path.write_text(
        json.dumps(run_trace.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    feedback_path.write_text(
        json.dumps(feedback, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    typer.echo(f"Status:            {run_trace.status.value}")
    typer.echo(f"Terminal valid:    {run_trace.terminal_validation_passed}")
    typer.echo(f"Run ID:            {run_trace.run_id}")
    typer.echo(f"Trace written to:  {trace_path}")
    typer.echo(f"Feedback written:  {feedback_path}")

    if run_trace.status.value != "completed":
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
