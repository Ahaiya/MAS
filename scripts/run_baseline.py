#!/usr/bin/env python3
"""Run MAS evaluation baseline.

This script executes the full multi-agent evaluation pipeline on a given text input.
Supports --provider mock (deterministic, no LLM calls) for baseline validation.
"""

import json
import sys
from pathlib import Path

# Ensure project root is on sys.path when run as `python scripts/run_baseline.py`
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.agents.mock_config_resolver import run as resolve_bundle
from src.contracts.request_models import EvaluationRequest
from src.pipeline.runner import PipelineRunner

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
    """Run the MAS baseline evaluation pipeline in mock mode."""
    if provider != "mock":
        typer.echo(
            f"Error: provider '{provider}' is not supported yet. Use --provider mock.",
            err=True,
        )
        raise typer.Exit(code=1)

    if verbose:
        typer.echo("=" * 60)
        typer.echo("MAS Baseline Evaluation Pipeline (mock mode)")
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

    # Run pipeline
    if verbose:
        typer.echo("Running pipeline...")
    runner = PipelineRunner(resolved)
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
