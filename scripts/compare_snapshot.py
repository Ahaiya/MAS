#!/usr/bin/env python3
"""Compare a live run directory against a golden snapshot.

Exit 0 → no regressions detected.
Exit 1 → at least one structural or score regression found.

Comparison dimensions:
  - run status and terminal_validation_passed
  - node sequence (node_id order)
  - dimension IDs and final_score values
  - adjudication: conflict_count present and >= 1 when golden recorded conflicts
"""

import json
from pathlib import Path

import typer

app = typer.Typer(name="compare-snapshot", help="Compare a run directory against a golden snapshot.")


def _ok(msg: str) -> None:
    typer.echo(f"OK    {msg}")


def _fail(msg: str) -> None:
    typer.echo(f"FAIL  {msg}", err=True)


def _compare(golden: dict, trace: dict, feedback: dict) -> bool:
    all_ok = True

    golden_trace = golden.get("run_trace", {})
    golden_feedback = golden.get("feedback", {})

    # 1. Status
    if trace.get("status") != golden_trace.get("status"):
        _fail(f"status: {trace.get('status')} != {golden_trace.get('status')}")
        all_ok = False
    else:
        _ok(f"status = {trace.get('status')}")

    # 2. Terminal validation
    if trace.get("terminal_validation_passed") != golden_trace.get("terminal_validation_passed"):
        _fail("terminal_validation_passed mismatch")
        all_ok = False
    else:
        _ok("terminal_validation_passed matches")

    # 3. Node sequence
    cur_nodes = [n["node_id"] for n in trace.get("node_traces", [])]
    gld_nodes = [n["node_id"] for n in golden_trace.get("node_traces", [])]
    if cur_nodes != gld_nodes:
        _fail(f"node sequence differs:\n  current: {cur_nodes}\n  golden:  {gld_nodes}")
        all_ok = False
    else:
        _ok(f"node sequence matches ({len(cur_nodes)} nodes)")

    # 4. Dimension IDs
    cur_dims = feedback.get("dimensions", {})
    gld_dims = golden_feedback.get("dimensions", {})
    if set(cur_dims.keys()) != set(gld_dims.keys()):
        _fail(
            f"dimension IDs differ:\n  current: {sorted(cur_dims.keys())}\n  golden:  {sorted(gld_dims.keys())}"
        )
        all_ok = False
    else:
        _ok(f"dimension IDs match ({len(cur_dims)} dimensions)")

        # 5. final_score per dimension
        for dim_id in gld_dims:
            if dim_id not in cur_dims:
                continue
            cur_score = cur_dims[dim_id].get("final_score")
            gld_score = gld_dims[dim_id].get("final_score")
            if cur_score != gld_score:
                _fail(f"score regression in '{dim_id}': current={cur_score}, golden={gld_score}")
                all_ok = False
            else:
                _ok(f"score '{dim_id}' = {cur_score}")

    # 6. Adjudication consistency
    gld_checker = next(
        (n for n in golden_trace.get("node_traces", []) if n["node_id"] == "node_consistency_checker"),
        None,
    )
    cur_checker = next(
        (n for n in trace.get("node_traces", []) if n["node_id"] == "node_consistency_checker"),
        None,
    )
    if gld_checker and cur_checker:
        gld_ref = gld_checker.get("output_ref", "")
        cur_ref = cur_checker.get("output_ref", "")
        # Both should have same conflict polarity (0 vs nonzero)
        gld_conflicts = int(gld_ref.split(":")[1]) if ":" in gld_ref else 0
        cur_conflicts = int(cur_ref.split(":")[1]) if ":" in cur_ref else 0
        if (gld_conflicts > 0) != (cur_conflicts > 0):
            _fail(
                f"adjudication polarity changed: golden conflicts={gld_conflicts}, current={cur_conflicts}"
            )
            all_ok = False
        else:
            _ok(f"adjudication polarity consistent (conflicts: golden={gld_conflicts}, current={cur_conflicts})")

    return all_ok


@app.command()
def main(
    snapshot: Path = typer.Option(
        ...,
        "--snapshot",
        "-s",
        help="Path to golden snapshot JSON file.",
        exists=True,
    ),
    run_dir: Path = typer.Option(
        ...,
        "--run-dir",
        "-r",
        help="Path to live run directory (must contain run_trace.json and feedback.json).",
    ),
) -> None:
    """Compare a live run directory against a golden snapshot for regressions."""
    run_dir = Path(run_dir)
    typer.echo(f"Snapshot: {snapshot}")
    typer.echo(f"Run dir:  {run_dir}")
    typer.echo("-" * 60)

    trace_path = run_dir / "run_trace.json"
    feedback_path = run_dir / "feedback.json"

    for p in (trace_path, feedback_path):
        if not p.exists():
            _fail(f"Missing file: {p}")
            raise typer.Exit(code=1)

    golden = json.loads(snapshot.read_text(encoding="utf-8"))
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    feedback = json.loads(feedback_path.read_text(encoding="utf-8"))

    all_ok = _compare(golden, trace, feedback)

    typer.echo("-" * 60)
    if all_ok:
        typer.echo("PASS  No regressions detected.")
    else:
        typer.echo("FAIL  Regressions detected.", err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
