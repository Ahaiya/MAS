#!/usr/bin/env python3
"""Validate a MAS baseline run directory for tests and regression fixtures.

Checks:
  - Required files exist (run_trace.json, feedback.json)
  - run_trace schema: required fields present, status == 'completed'
  - terminal_validation_passed == True
  - All node_traces have status == 'success'
  - trace closure: input_ref / output_ref non-empty for each node
  - feedback schema: 'dimensions' key present with at least one entry
  - Optional: --require-adjudication asserts conflicts > 0 in trace refs
"""

import json
from pathlib import Path

import typer

app = typer.Typer(name="validate-run", help="Validate a MAS baseline run directory.")

# Required top-level fields in run_trace.json
_TRACE_REQUIRED_FIELDS = {
    "run_id", "bundle_version", "bundle_id", "status",
    "started_at", "finished_at", "node_traces",
    "terminal_validation_passed", "replay_metadata",
}

# Minimum expected nodes in the pipeline (normal path)
_EXPECTED_NODES = {
    "node_preprocess",
    "node_coverage",
    "node_extractor",
    "node_observer",
    "node_scorer",
    "node_consistency_checker",
    "node_adjudicator",
    "node_feedback",
}


def _fail(msg: str) -> None:
    typer.echo(f"FAIL  {msg}", err=True)
    raise typer.Exit(code=1)


def _ok(msg: str) -> None:
    typer.echo(f"OK    {msg}")


def validate_trace(trace: dict, require_adjudication: bool) -> None:
    # 1. Required fields
    missing = _TRACE_REQUIRED_FIELDS - set(trace.keys())
    if missing:
        _fail(f"run_trace missing required fields: {sorted(missing)}")
    _ok("run_trace required fields present")

    # 2. Status == completed
    if trace["status"] != "completed":
        _fail(f"run_trace status is '{trace['status']}', expected 'completed'")
    _ok(f"run_trace status = completed")

    # 3. Terminal validation
    if not trace.get("terminal_validation_passed"):
        _fail("terminal_validation_passed is not True")
    _ok("terminal_validation_passed = True")

    # 4. Node traces
    node_traces: list = trace.get("node_traces", [])
    if not node_traces:
        _fail("node_traces is empty")

    node_ids = {n["node_id"] for n in node_traces}
    missing_nodes = _EXPECTED_NODES - node_ids
    if missing_nodes:
        _fail(f"Missing expected nodes: {sorted(missing_nodes)}")
    _ok(f"All {len(_EXPECTED_NODES)} expected nodes present")

    # 5. All nodes succeeded; trace closure (input_ref / output_ref non-empty)
    for node in node_traces:
        nid = node.get("node_id", "?")
        if node.get("status") != "success":
            _fail(f"Node '{nid}' status = '{node.get('status')}', expected 'success'")
        if not node.get("input_ref"):
            _fail(f"Node '{nid}' has empty input_ref (trace closure violated)")
        if not node.get("output_ref"):
            _fail(f"Node '{nid}' has empty output_ref (trace closure violated)")
    _ok("All nodes succeeded with non-empty input_ref and output_ref")

    # 6. run_id and bundle_id non-empty
    if not trace.get("run_id"):
        _fail("run_id is empty")
    if not trace.get("bundle_id"):
        _fail("bundle_id is empty")
    _ok(f"run_id = {trace['run_id']}")

    # 7. Adjudication requirement
    if require_adjudication:
        # Consistency checker output_ref should indicate conflicts > 0
        checker_node = next(
            (n for n in node_traces if n["node_id"] == "node_consistency_checker"), None
        )
        if checker_node is None:
            _fail("--require-adjudication: node_consistency_checker not found in trace")
        output_ref = checker_node.get("output_ref", "")
        # output_ref format: "conflicts:N"
        if not output_ref.startswith("conflicts:"):
            _fail(
                f"--require-adjudication: consistency_checker output_ref unexpected format: '{output_ref}'"
            )
        conflict_count_str = output_ref.split(":", 1)[1]
        try:
            conflict_count = int(conflict_count_str)
        except ValueError:
            _fail(f"--require-adjudication: cannot parse conflict count from '{output_ref}'")
        if conflict_count < 1:
            _fail(
                f"--require-adjudication: expected >= 1 conflict, got {conflict_count}"
            )
        _ok(f"Adjudication triggered: {conflict_count} conflict(s) recorded")


def validate_feedback(feedback: dict) -> None:
    if not isinstance(feedback, dict):
        _fail(f"feedback.json root is not an object (got {type(feedback).__name__})")

    if "dimensions" not in feedback:
        _fail("feedback.json missing 'dimensions' key")

    dimensions = feedback["dimensions"]
    if not isinstance(dimensions, dict) or len(dimensions) == 0:
        _fail("feedback.json 'dimensions' is empty or not an object")

    # Each dimension should have final_score
    for dim_id, dim_data in dimensions.items():
        if not isinstance(dim_data, dict):
            _fail(f"dimensions['{dim_id}'] is not an object")
        if "final_score" not in dim_data:
            _fail(f"dimensions['{dim_id}'] missing 'final_score'")

    _ok(f"feedback.json dimensions: {len(dimensions)} dimension(s) with final_score")


@app.command()
def main(
    run_dir: Path = typer.Option(
        ...,
        "--run-dir",
        "-r",
        help="Path to run output directory containing run_trace.json and feedback.json.",
    ),
    require_adjudication: bool = typer.Option(
        False,
        "--require-adjudication",
        help="Assert that adjudication was triggered (conflict_count >= 1).",
    ),
) -> None:
    """Validate a MAS baseline run directory for schema, trace closure, and terminal validation."""
    run_dir = Path(run_dir)
    typer.echo(f"Validating run directory: {run_dir}")
    typer.echo("-" * 60)

    # Check required files
    trace_path = run_dir / "run_trace.json"
    feedback_path = run_dir / "feedback.json"

    if not trace_path.exists():
        _fail(f"run_trace.json not found in {run_dir}")
    _ok("run_trace.json exists")

    if not feedback_path.exists():
        _fail(f"feedback.json not found in {run_dir}")
    _ok("feedback.json exists")

    # Load and validate
    try:
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"run_trace.json is not valid JSON: {exc}")

    try:
        feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"feedback.json is not valid JSON: {exc}")

    validate_trace(trace, require_adjudication)
    validate_feedback(feedback)

    typer.echo("-" * 60)
    typer.echo("PASS  All validation checks passed.")


if __name__ == "__main__":
    app()
