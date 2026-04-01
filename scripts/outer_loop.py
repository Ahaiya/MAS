#!/usr/bin/env python3
# ruff: noqa: E402
"""CLI entrypoint for the MAS outer-loop optimizer."""

from __future__ import annotations

import json
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
from src.outer_loop.agent import OuterLoopAgent, RuleBasedOuterLoopProvider
from src.outer_loop.experiments.experiment_log import ExperimentLog
from src.outer_loop.optimization.config_patcher import ConfigPatcher
from src.outer_loop.optimization.search_policy import SearchPolicy
from src.outer_loop.probes import run_probe
from src.providers.factory import build_provider

app = typer.Typer(name="outer-loop", help="Outer-loop optimization controls.")

_DEFAULT_BUNDLE = _PROJECT_ROOT / "configs" / "bundles" / "asap_set8_baseline.bundle.yaml"
_DEFAULT_TRAINING_SET = _PROJECT_ROOT / "data" / "training_set_8.tsv"
_DEFAULT_EXPERIMENT_LOG = _PROJECT_ROOT / "experiments" / "experiment_log.yaml"
_DEFAULT_EXPERIMENTS_DIR = _PROJECT_ROOT / "experiments"
_DEFAULT_ARTIFACTS_BASE = _PROJECT_ROOT / "artifacts" / "eval"
_DEFAULT_PROMPTS_DIR = _PROJECT_ROOT / "src" / "outer_loop" / "prompts"


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
    mock_agent: bool,
) -> OuterLoopAgent:
    experiment_log = ExperimentLog.load(_DEFAULT_EXPERIMENT_LOG)
    patcher = ConfigPatcher(
        configs_root=_PROJECT_ROOT / "configs",
        snapshots_root=_PROJECT_ROOT / "experiments" / "snapshots",
    )
    policy = SearchPolicy()

    if mock_agent:
        provider = RuleBasedOuterLoopProvider()
    else:
        provider = build_provider(ProviderEntryConfig(api_key_env="LLM_API_KEY"))

    return OuterLoopAgent(
        experiment_log=experiment_log,
        config_patcher=patcher,
        search_policy=policy,
        provider=provider,
        bundle_path=bundle,
        training_set_path=training_set,
        artifacts_output_base=_DEFAULT_ARTIFACTS_BASE,
        experiments_dir=_DEFAULT_EXPERIMENTS_DIR,
        prompts_dir=_DEFAULT_PROMPTS_DIR,
        mock_eval_provider=mock_agent,
    )


@app.command()
def run(
    max_iterations: Annotated[int, typer.Option("--max-iterations", min=1)] = 5,
    bundle: Annotated[Path, typer.Option("--bundle")] = _DEFAULT_BUNDLE,
    training_set: Annotated[Path, typer.Option("--training-set")] = _DEFAULT_TRAINING_SET,
    mock_agent: Annotated[bool, typer.Option("--mock-agent")] = False,
) -> None:
    """Run the outer-loop optimization loop."""
    try:
        agent = _build_agent(
            bundle=bundle,
            training_set=training_set,
            mock_agent=mock_agent,
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
        "essay_count": result.essay_count,
        "metrics": result.metrics,
        "per_essay": result.per_essay,
    }
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    app()
