#!/usr/bin/env python3
"""Unified baseline acceptance entry point for test workflows.

Runs the full baseline pipeline for all samples in the manifest,
validates each run directory, and optionally checks golden snapshots.

Exit 0 → all samples passed all checks.
Exit 1 → at least one check failed.
"""

import json
import subprocess
import sys
from pathlib import Path

import typer
import yaml

app = typer.Typer(name="accept-baseline", help="Run and validate the test baseline flow.")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_TEST_SCRIPTS_DIR = Path(__file__).resolve().parent
_GOLDEN_DIR = _PROJECT_ROOT / "tests" / "golden"


def _run_cmd(args: list[str], label: str) -> bool:
    """Run a subprocess, stream output, return success flag."""
    typer.echo(f"\n>>> {label}")
    result = subprocess.run(args, text=True, capture_output=False)
    if result.returncode != 0:
        typer.echo(f"FAIL  [{label}]", err=True)
        return False
    return True


def _compare_snapshot(essay_id: str, run_dir: Path) -> bool:
    """Compare live run output against golden snapshot. Return True if matching."""
    golden_path = _GOLDEN_DIR / f"sample_{essay_id}.mock.json"
    if not golden_path.exists():
        typer.echo(f"FAIL  Golden snapshot not found: {golden_path}", err=True)
        return False

    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    trace = json.loads((run_dir / "run_trace.json").read_text(encoding="utf-8"))
    feedback = json.loads((run_dir / "feedback.json").read_text(encoding="utf-8"))

    ok = True

    # Status
    if trace["status"] != golden["run_trace"]["status"]:
        typer.echo(
            f"FAIL  [{essay_id}] status mismatch: {trace['status']} != {golden['run_trace']['status']}",
            err=True,
        )
        ok = False

    # terminal_validation_passed
    if trace["terminal_validation_passed"] != golden["run_trace"]["terminal_validation_passed"]:
        typer.echo(f"FAIL  [{essay_id}] terminal_validation_passed mismatch", err=True)
        ok = False

    # Nodes
    cur_nodes = [n["node_id"] for n in trace["node_traces"]]
    gld_nodes = [n["node_id"] for n in golden["run_trace"]["node_traces"]]
    if cur_nodes != gld_nodes:
        typer.echo(f"FAIL  [{essay_id}] node sequence mismatch", err=True)
        typer.echo(f"      current: {cur_nodes}", err=True)
        typer.echo(f"      golden:  {gld_nodes}", err=True)
        ok = False

    # Dimension IDs and scores
    cur_dims = feedback.get("dimensions", {})
    gld_dims = golden["feedback"].get("dimensions", {})
    if set(cur_dims.keys()) != set(gld_dims.keys()):
        typer.echo(f"FAIL  [{essay_id}] dimension ID mismatch", err=True)
        ok = False
    else:
        for dim_id in gld_dims:
            if cur_dims[dim_id]["final_score"] != gld_dims[dim_id]["final_score"]:
                typer.echo(
                    f"FAIL  [{essay_id}] score mismatch for '{dim_id}': "
                    f"{cur_dims[dim_id]['final_score']} != {gld_dims[dim_id]['final_score']}",
                    err=True,
                )
                ok = False

    if ok:
        typer.echo(f"OK    [{essay_id}] snapshot matches golden")
    return ok


@app.command()
def main(
    bundle: Path = typer.Option(
        ...,
        "--bundle",
        "-b",
        help="Path to configuration bundle YAML file.",
        exists=True,
    ),
    manifest: Path = typer.Option(
        ...,
        "--manifest",
        "-m",
        help="Path to baseline manifest YAML file.",
        exists=True,
    ),
    provider: str = typer.Option(
        "mock",
        "--provider",
        "-p",
        help="LLM provider: 'mock' or 'real'.",
    ),
    output_base: Path = typer.Option(
        _PROJECT_ROOT / "artifacts" / "baseline",
        "--output-base",
        help="Base directory for run artifacts.",
    ),
    check_snapshots: bool = typer.Option(
        False,
        "--check-snapshots",
        help="Compare live run against golden snapshots.",
    ),
) -> None:
    """Run and validate the full MAS baseline for all manifest samples."""
    typer.echo("=" * 60)
    typer.echo("MAS Baseline Acceptance")
    typer.echo("=" * 60)
    typer.echo(f"Bundle:          {bundle}")
    typer.echo(f"Manifest:        {manifest}")
    typer.echo(f"Provider:        {provider}")
    typer.echo(f"Check snapshots: {check_snapshots}")
    typer.echo("=" * 60)

    manifest_data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    samples = manifest_data.get("samples", [])
    if not samples:
        typer.echo("FAIL  No samples found in manifest.", err=True)
        raise typer.Exit(code=1)

    all_ok = True

    for sample in samples:
        essay_id = str(sample["id"])
        expected_path = sample.get("expected_path", "normal")
        input_file = _PROJECT_ROOT / sample["path"]
        run_dir = output_base / essay_id / provider

        typer.echo(f"\n{'='*60}")
        typer.echo(f"Sample: {essay_id}  (expected_path={expected_path})")
        typer.echo(f"{'='*60}")

        # Step 1: Run pipeline
        pipeline_ok = _run_cmd(
            [
                sys.executable,
                str(_TEST_SCRIPTS_DIR / "run_baseline.py"),
                "--bundle", str(bundle),
                "--provider", provider,
                "--input-file", str(input_file),
                "--output-dir", str(run_dir),
            ],
            f"run_baseline [{essay_id}]",
        )
        if not pipeline_ok:
            all_ok = False
            continue

        # Step 2: Validate run directory
        require_adj = expected_path == "adjudication"
        validate_args = [
            sys.executable,
            str(_TEST_SCRIPTS_DIR / "validate_run.py"),
            "--run-dir", str(run_dir),
        ]
        if require_adj:
            validate_args.append("--require-adjudication")

        validate_ok = _run_cmd(validate_args, f"validate_run [{essay_id}]")
        if not validate_ok:
            all_ok = False

        # Step 3: Snapshot comparison (only for mock provider)
        if check_snapshots and provider == "mock":
            snap_ok = _compare_snapshot(essay_id, run_dir)
            if not snap_ok:
                all_ok = False

    typer.echo(f"\n{'='*60}")
    if all_ok:
        typer.echo("PASS  All baseline acceptance checks passed.")
    else:
        typer.echo("FAIL  One or more baseline acceptance checks failed.", err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
