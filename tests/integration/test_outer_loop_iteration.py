from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from src.outer_loop.agent import OuterLoopAgent, RuleBasedOuterLoopProvider
from src.outer_loop.experiments.batch_runner import RunResult
from src.outer_loop.experiments.experiment_log import ExperimentLog
from src.outer_loop.optimization.config_patcher import ConfigPatcher
from src.outer_loop.optimization.search_policy import SearchPolicy
from src.outer_loop.probes import ProbeResult


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _write_training_tsv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "essay_id\tessay\n"
        "1001\tThis is a minimal sample essay for outer-loop integration tests.\n",
        encoding="latin-1",
    )


def _batch_eval_stub(
    sample_ids: list[str],
    bundle_path: Path,
    tsv_path: Path,
    output_base: Path,
    iter_id: str | None = None,
    **kwargs: Any,
) -> list[RunResult]:
    _ = bundle_path
    _ = tsv_path
    _ = kwargs

    if iter_id is None:
        iter_dir = output_base
    else:
        text = str(iter_id)
        iter_dir = output_base / (text if text.startswith("iter_") else f"iter_{text}")

    results: list[RunResult] = []
    for essay_id in sample_ids:
        essay_dir = iter_dir / essay_id
        essay_dir.mkdir(parents=True, exist_ok=True)
        (essay_dir / "run_trace.json").write_text(
            json.dumps({"status": "completed"}, indent=2),
            encoding="utf-8",
        )
        (essay_dir / "feedback.json").write_text(
            json.dumps({"dimensions": {}}, indent=2),
            encoding="utf-8",
        )
        results.append(
            RunResult(
                essay_id=essay_id,
                success=True,
                output_dir=essay_dir,
                trace_dict={"status": "completed"},
                feedback_dict={"dimensions": {}},
            )
        )
    return results


def _run_probes_stub(
    probe_names: list[str],
    artifacts_dir: Path,
    **kwargs: Any,
) -> dict[str, ProbeResult]:
    _ = artifacts_dir
    _ = kwargs

    results: dict[str, ProbeResult] = {}
    for probe_name in probe_names:
        if probe_name == "qwk_probe":
            metrics = {
                "qwk_user_needs_analysis": 0.72,
                "qwk_solution_generation": 0.71,
                "qwk_engineering_ethics": 0.73,
                "qwk_composite": 0.72,
            }
        else:
            metrics = {"mock_metric": 1.0}
        results[probe_name] = ProbeResult(
            probe_name=probe_name,
            sample_count=1,
            metrics=metrics,
            per_sample=None,
        )
    return results


def _build_agent(tmp_path: Path) -> tuple[OuterLoopAgent, Path]:
    configs_root = tmp_path / "configs"
    experiments_dir = tmp_path / "experiments"
    artifacts_base = tmp_path / "artifacts" / "eval"
    bundle_path = configs_root / "bundles" / "engineering_eval_baseline.bundle.yaml"
    training_set_path = tmp_path / "data" / "engineering_training_set.tsv"

    _write_yaml(
        configs_root / "prompts" / "scoring_context.yaml",
        {
            "schema_version": "2.0",
            "scoring_context": {
                "context_id": "test_context",
                "calibration_notes": "initial calibration note",
            },
        },
    )
    _write_yaml(
        configs_root / "bundles" / "engineering_eval_baseline.bundle.yaml",
        {
            "artifact_bundle": {
                "provider_config": {
                    "default": {
                        "api_key_env": "LLM_API_KEY",
                        "model": "mock",
                        "params": {"temperature": 0.1},
                    }
                },
                "operational_params": {"max_retries": 1},
            }
        },
    )
    _write_training_tsv(training_set_path)

    log_path = experiments_dir / "experiment_log.yaml"
    log = ExperimentLog.load(log_path)
    patcher = ConfigPatcher(
        configs_root=configs_root,
        snapshots_root=experiments_dir / "snapshots",
    )
    policy = SearchPolicy()

    agent = OuterLoopAgent(
        experiment_log=log,
        config_patcher=patcher,
        search_policy=policy,
        provider=RuleBasedOuterLoopProvider(),
        bundle_path=bundle_path,
        training_set_path=training_set_path,
        artifacts_output_base=artifacts_base,
        experiments_dir=experiments_dir,
        prompts_dir=Path("src/outer_loop/prompts"),
        mock_eval_provider=True,
        batch_eval_fn=_batch_eval_stub,
        run_probes_fn=_run_probes_stub,
    )
    return agent, log_path


def test_outer_loop_one_iteration_appends_log_and_snapshot(tmp_path: Path) -> None:
    agent, log_path = _build_agent(tmp_path)

    record = agent.run_one_iteration("001")
    assert record.iteration == 1
    assert record.changed_unit == "scoring.calibration_notes.user_needs_analysis"
    assert "qwk_probe" in record.probe_used

    reloaded = ExperimentLog.load(log_path)
    assert reloaded.count() == 1
    latest = reloaded.latest()
    assert latest is not None
    assert latest.changed_unit == "scoring.calibration_notes.user_needs_analysis"

    snapshot_path = tmp_path / "experiments" / "snapshots" / "iter_001" / "configs"
    assert snapshot_path.exists()

    scoring_context_path = tmp_path / "configs" / "prompts" / "scoring_context.yaml"
    loaded = yaml.safe_load(scoring_context_path.read_text(encoding="utf-8"))
    assert "问题边界" in loaded["scoring_context"]["calibration_notes"]


def test_outer_loop_cold_start_writes_diagnostics_and_first_proposal(tmp_path: Path) -> None:
    agent, _ = _build_agent(tmp_path)

    proposal_path = agent.run_cold_start()
    assert proposal_path.exists()

    diagnostics_path = tmp_path / "experiments" / "probes" / "cold_start_diagnostics.json"
    assert diagnostics_path.exists()
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    probe_results = diagnostics.get("probe_results", {})
    assert len(probe_results) == 9
    assert "qwk_probe" in probe_results

    proposal = yaml.safe_load(proposal_path.read_text(encoding="utf-8"))
    assert isinstance(proposal, dict)
    assert "change_proposal" in proposal
    assert "selected_probes" in proposal
