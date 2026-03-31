#!/usr/bin/env python3
"""Replay a previous MAS run from a golden snapshot for regression tests.

Reads the snapshot metadata (bundle_id, bundle_version, provider, input essay_id)
and re-runs the pipeline with the same configuration, writing fresh artifacts to
the specified output directory.

This enables regression detection: run `replay_run.py` then `compare_snapshot.py`
to verify the pipeline produces the same results as the golden baseline.
"""

import json
import subprocess
import sys
from pathlib import Path

import typer

app = typer.Typer(name="replay-run", help="Replay a MAS run from a golden snapshot for tests.")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_TEST_SCRIPTS_DIR = Path(__file__).resolve().parent
_SAMPLES_DIR = _PROJECT_ROOT / "data" / "samples"
_BUNDLES_DIR = _PROJECT_ROOT / "configs" / "bundles"


@app.command()
def main(
    snapshot: Path = typer.Option(
        ...,
        "--snapshot",
        "-s",
        help="Path to golden snapshot JSON file.",
        exists=True,
    ),
    output_dir: Path = typer.Option(
        ...,
        "--output-dir",
        "-o",
        help="Directory to write replayed run artifacts.",
    ),
    provider_override: str = typer.Option(
        "",
        "--provider",
        "-p",
        help="Override provider from snapshot (empty = use snapshot provider).",
    ),
    input_override: Path = typer.Option(
        None,
        "--input-file",
        "-i",
        help="Override input file from snapshot (empty = derive from essay_id).",
    ),
) -> None:
    """Replay a MAS run using the configuration captured in a golden snapshot."""
    snap = json.loads(snapshot.read_text(encoding="utf-8"))

    essay_id = snap.get("essay_id", "unknown")
    provider = provider_override or snap.get("provider", "mock")
    run_trace = snap.get("run_trace", {})
    bundle_id = run_trace.get("bundle_id", "")

    typer.echo("=" * 60)
    typer.echo("MAS Run Replay")
    typer.echo("=" * 60)
    typer.echo(f"Snapshot:   {snapshot}")
    typer.echo(f"Essay ID:   {essay_id}")
    typer.echo(f"Provider:   {provider}")
    typer.echo(f"Bundle ID:  {bundle_id}")
    typer.echo("=" * 60)

    # Resolve bundle path from bundle_id
    bundle_path = _BUNDLES_DIR / f"{bundle_id}.bundle.yaml"
    if not bundle_path.exists():
        typer.echo(f"FAIL  Bundle not found: {bundle_path}", err=True)
        raise typer.Exit(code=1)

    # Resolve input file
    if input_override:
        input_file = input_override
    else:
        input_file = _SAMPLES_DIR / f"sample_{essay_id}.txt"
    if not input_file.exists():
        typer.echo(f"FAIL  Input file not found: {input_file}", err=True)
        raise typer.Exit(code=1)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Run pipeline
    result = subprocess.run(
        [
            sys.executable,
            str(_TEST_SCRIPTS_DIR / "run_baseline.py"),
            "--bundle", str(bundle_path),
            "--provider", provider,
            "--input-file", str(input_file),
            "--output-dir", str(output_dir),
        ],
        text=True,
    )

    if result.returncode != 0:
        typer.echo("FAIL  Pipeline replay failed.", err=True)
        raise typer.Exit(code=result.returncode)

    typer.echo(f"\nReplay artifacts written to: {output_dir}")
    typer.echo("DONE  Replay complete. Run compare_snapshot.py to check for regressions.")


if __name__ == "__main__":
    app()
