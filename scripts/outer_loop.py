#!/usr/bin/env python3
# ruff: noqa: E402
"""CLI entrypoint for the MAS outer-loop optimizer.

Recommended unified entry:
  python -m scripts outer-loop ...
  python -m scripts task ...
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from dotenv import load_dotenv

load_dotenv()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.contracts.artifact_bundle import ProviderEntryConfig
from src.outer_loop.agent import OuterLoopAgent
from src.outer_loop.experiments.experiment_log import ExperimentLog
from src.outer_loop.optimization.config_patcher import ConfigPatcher
from src.outer_loop.optimization.search_policy import SearchPolicy
from src.outer_loop.probes import run_probe
from src.outer_loop.task_setup import TaskSetupManager
from src.providers.factory import build_provider

app = typer.Typer(name="outer-loop", help="Outer-loop optimization controls.")
task_app = typer.Typer(name="task", help="Task setup workflow.")
app.add_typer(task_app, name="task")

_DEFAULT_BUNDLE = _PROJECT_ROOT / "configs" / "bundles" / "engineering_eval_baseline.bundle.yaml"
# Default training set: single .md file or directory of .md files; TSV also accepted for ASAP.
_DEFAULT_TRAINING_SET = _PROJECT_ROOT / "data" / "training" / "maker_hackathon" / "sample.md"
# Human-score annotations TSV for QWK probe (optional; falls back to training_set if not set).
_DEFAULT_ANNOTATIONS_PATH = (
    _PROJECT_ROOT / "data" / "training" / "maker_hackathon" / "annotations.tsv"
)
_DEFAULT_EXPERIMENT_LOG = _PROJECT_ROOT / "experiments" / "experiment_log.yaml"
_DEFAULT_EXPERIMENTS_DIR = _PROJECT_ROOT / "experiments"
_DEFAULT_ARTIFACTS_BASE = _PROJECT_ROOT / "artifacts" / "eval"
_DEFAULT_PROMPTS_DIR = _PROJECT_ROOT / "src" / "outer_loop" / "prompts"


def _maybe_float_env(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return float(raw)


def _maybe_int_env(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return int(raw)


def _build_outer_loop_provider():
    params: dict[str, Any] = {}

    temperature = _maybe_float_env("OUTER_LOOP_TEMPERATURE")
    if temperature is not None:
        params["temperature"] = temperature

    max_tokens = _maybe_int_env("OUTER_LOOP_MAX_TOKENS")
    if max_tokens is not None:
        params["max_tokens"] = max_tokens

    return build_provider(
        ProviderEntryConfig(
            api_key_env="OUTER_LOOP_API_KEY",
            model=os.environ.get("OUTER_LOOP_MODEL", "").strip(),
            api_base=os.environ.get("OUTER_LOOP_API_BASE", "").strip(),
            params=params,
        )
    )


def _extract_qwk_composite(probe_results: dict[str, Any]) -> float | None:
    qwk_payload = probe_results.get("qwk_probe")
    if isinstance(qwk_payload, dict):
        metrics = qwk_payload.get("metrics")
        if isinstance(metrics, dict):
            value = metrics.get("qwk_composite")
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                try:
                    return float(value.strip())
                except ValueError:
                    return None
    value = probe_results.get("qwk_composite")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _build_agent(
    *,
    bundle: Path,
    training_set: Path,
    annotations: Path | None = None,
) -> OuterLoopAgent:
    experiment_log = ExperimentLog.load(_DEFAULT_EXPERIMENT_LOG)
    patcher = ConfigPatcher(
        configs_root=_PROJECT_ROOT / "configs",
        snapshots_root=_PROJECT_ROOT / "experiments" / "snapshots",
    )
    policy = SearchPolicy()

    provider = _build_outer_loop_provider()

    effective_annotations = annotations if annotations is not None else (
        _DEFAULT_ANNOTATIONS_PATH if _DEFAULT_ANNOTATIONS_PATH.exists() else None
    )

    return OuterLoopAgent(
        experiment_log=experiment_log,
        config_patcher=patcher,
        search_policy=policy,
        provider=provider,
        bundle_path=bundle,
        training_set_path=training_set,
        annotations_path=effective_annotations,
        artifacts_output_base=_DEFAULT_ARTIFACTS_BASE,
        experiments_dir=_DEFAULT_EXPERIMENTS_DIR,
        prompts_dir=_DEFAULT_PROMPTS_DIR,
    )


def _build_task_setup_manager(
    *,
    bundle: Path = _DEFAULT_BUNDLE,
    require_provider: bool = False,
) -> TaskSetupManager:
    return TaskSetupManager(
        provider=_build_outer_loop_provider() if require_provider else None,
        configs_root=_PROJECT_ROOT / "configs",
        experiments_root=_PROJECT_ROOT / "experiments",
        bundle_path=bundle,
        aggregation_policy_path=(
            _PROJECT_ROOT
            / "configs"
            / "policies"
            / "aggregation"
            / "engineering_eval_aggregation.yaml"
        ),
    )


def _read_task_brief(
    *,
    task_brief: str,
    task_brief_file: Path | None,
) -> str:
    if task_brief_file is not None:
        if not task_brief_file.exists():
            raise FileNotFoundError(f"task brief file not found: {task_brief_file}")
        return task_brief_file.read_text(encoding="utf-8")
    if task_brief.strip():
        return task_brief.strip()
    raise ValueError("task brief is required: pass --task-brief or --task-brief-file")


@app.command()
def run(
    max_iterations: Annotated[int, typer.Option("--max-iterations", min=1)] = 5,
    bundle: Annotated[Path, typer.Option("--bundle")] = _DEFAULT_BUNDLE,
    training_set: Annotated[Path, typer.Option("--training-set")] = _DEFAULT_TRAINING_SET,
    annotations: Annotated[Path | None, typer.Option("--annotations")] = None,
) -> None:
    """Run the outer-loop optimization loop."""
    try:
        agent = _build_agent(
            bundle=bundle,
            training_set=training_set,
            annotations=annotations,
        )
        records = agent.run_loop(max_iterations=max_iterations, stop_on_target=True)
    except Exception as exc:
        typer.secho(f"outer-loop run failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    if not records:
        typer.echo("No iterations executed.")
        return

    for record in records:
        qwk = _extract_qwk_composite(record.probe_results)
        qwk_text = f"{qwk:.4f}" if qwk is not None else "N/A"
        typer.echo(
            f"iter={record.iteration:03d} "
            f"unit={record.changed_unit} "
            f"verdict={record.verdict} "
            f"qwk_composite={qwk_text}"
        )


@app.command()
def status() -> None:
    """Show experiment log summary and latest QWK."""
    log = ExperimentLog.load(_DEFAULT_EXPERIMENT_LOG)
    typer.echo(f"log_path={_DEFAULT_EXPERIMENT_LOG}")
    typer.echo(f"total_iterations={log.count()}")

    latest = log.latest()
    if latest is None:
        typer.echo("latest_iteration=None")
        typer.echo("latest_qwk_composite=None")
        return

    qwk = _extract_qwk_composite(latest.probe_results)
    qwk_text = f"{qwk:.4f}" if qwk is not None else "N/A"
    typer.echo(f"latest_iteration={latest.iteration}")
    typer.echo(f"latest_unit={latest.changed_unit}")
    typer.echo(f"latest_verdict={latest.verdict}")
    typer.echo(f"latest_qwk_composite={qwk_text}")


@app.command()
def rollback(iter_id: Annotated[str, typer.Option("--iter-id")]) -> None:
    """Rollback configs to the snapshot of a specific iteration."""
    patcher = ConfigPatcher(
        configs_root=_PROJECT_ROOT / "configs",
        snapshots_root=_PROJECT_ROOT / "experiments" / "snapshots",
    )
    ok = patcher.rollback(iter_id)
    if not ok:
        typer.secho(
            f"rollback failed: snapshot for iter_id={iter_id!r} not found",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    typer.echo(f"rollback completed: iter_id={iter_id}")


@app.command()
def probe(
    name: Annotated[str, typer.Option("--name")],
    artifacts_dir: Annotated[Path, typer.Option("--artifacts-dir")] = _DEFAULT_ARTIFACTS_BASE,
    training_set: Annotated[Path, typer.Option("--training-set")] = _DEFAULT_TRAINING_SET,
) -> None:
    """Run one probe manually and print JSON output."""
    try:
        result = run_probe(name, artifacts_dir, tsv_path=training_set)
    except Exception as exc:
        typer.secho(f"probe failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    payload = {
        "probe_name": result.probe_name,
        "sample_count": result.sample_count,
        "metrics": result.metrics,
        "per_sample": result.per_sample,
    }
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))


@task_app.command("draft")
def task_draft(
    task_id: Annotated[str, typer.Option("--task-id")] ,
    task_brief: Annotated[str, typer.Option("--task-brief")] = "",
    task_brief_file: Annotated[Path | None, typer.Option("--task-brief-file")] = None,
    bundle: Annotated[Path, typer.Option("--bundle")] = _DEFAULT_BUNDLE,
) -> None:
    """Generate the first task-setup draft from rubric source + task brief."""
    try:
        manager = _build_task_setup_manager(bundle=bundle, require_provider=True)
        brief = _read_task_brief(task_brief=task_brief, task_brief_file=task_brief_file)
        draft = manager.start(task_id=task_id, task_brief=brief)
    except Exception as exc:
        typer.secho(f"task draft failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    typer.echo(manager.format_draft(draft))


@task_app.command("show")
def task_show(
    task_id: Annotated[str, typer.Option("--task-id")],
    bundle: Annotated[Path, typer.Option("--bundle")] = _DEFAULT_BUNDLE,
) -> None:
    """Show the current task-setup draft."""
    try:
        manager = _build_task_setup_manager(bundle=bundle)
        draft = manager.show(task_id=task_id)
    except Exception as exc:
        typer.secho(f"task show failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    typer.echo(manager.format_draft(draft))


@task_app.command("revise")
def task_revise(
    task_id: Annotated[str, typer.Option("--task-id")],
    instruction: Annotated[str, typer.Option("--instruction")],
    bundle: Annotated[Path, typer.Option("--bundle")] = _DEFAULT_BUNDLE,
) -> None:
    """Revise the current task-setup draft with one teacher instruction."""
    try:
        manager = _build_task_setup_manager(bundle=bundle, require_provider=True)
        draft = manager.revise(task_id=task_id, instruction=instruction)
    except Exception as exc:
        typer.secho(f"task revise failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    typer.echo(manager.format_draft(draft))


@task_app.command("confirm")
def task_confirm(
    task_id: Annotated[str, typer.Option("--task-id")],
    bundle: Annotated[Path, typer.Option("--bundle")] = _DEFAULT_BUNDLE,
) -> None:
    """Freeze the draft into task files and rebind the active bundle."""
    try:
        manager = _build_task_setup_manager(bundle=bundle)
        result = manager.confirm(task_id=task_id)
    except Exception as exc:
        typer.secho(f"task confirm failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    typer.echo(f"task_id={result.task_id}")
    typer.echo(f"draft={result.draft_path}")
    typer.echo(f"rubric={result.rubric_path}")
    typer.echo(f"scoring_context={result.scoring_context_path}")
    typer.echo(f"bundle={result.bundle_path}")
    typer.echo(f"aggregation_policy={result.aggregation_policy_path}")


if __name__ == "__main__":
    app()
