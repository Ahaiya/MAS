from __future__ import annotations

from pathlib import Path

from src.outer_loop.experiments.experiment_log import ExperimentLog, IterationRecord


def _record(
    *,
    iteration: int,
    changed_unit: str,
    verdict: str,
) -> IterationRecord:
    return IterationRecord(
        iteration=iteration,
        timestamp=f"2026-04-01T10:0{iteration}:00",
        changed_unit=changed_unit,
        change_description="test change",
        target_file="configs/prompts/scoring_context.yaml",
        target_path="calibration_notes.problem_analysis",
        probe_used=["qwk_probe"],
        probe_results={"qwk_composite": "0.70 -> 0.71"},
        verdict=verdict,
        next_hypothesis="next",
        config_snapshot_path=f"experiments/snapshots/iter_{iteration:03d}/",
    )


def test_load_append_reload_latest(tmp_path: Path) -> None:
    log_path = tmp_path / "experiments" / "experiment_log.yaml"

    log = ExperimentLog.load(log_path)
    assert log.latest() is None
    assert log.count() == 0

    first = _record(
        iteration=1,
        changed_unit="scoring.calibration_notes.problem_analysis",
        verdict="effective",
    )
    log.append(first)

    reloaded = ExperimentLog.load(log_path)
    latest = reloaded.latest()
    assert latest is not None
    assert latest.iteration == 1
    assert latest.changed_unit == "scoring.calibration_notes.problem_analysis"
    assert reloaded.count() == 1
    assert reloaded.next_iteration_id() == 2


def test_count_consecutive_no_improvement_for_same_unit(tmp_path: Path) -> None:
    log_path = tmp_path / "experiments" / "experiment_log.yaml"
    log = ExperimentLog.load(log_path)

    log.append(
        _record(
            iteration=1,
            changed_unit="scoring.calibration_notes.problem_analysis",
            verdict="no-improvement",
        )
    )
    log.append(
        _record(
            iteration=2,
            changed_unit="scoring.calibration_notes.problem_analysis",
            verdict="no-improvement",
        )
    )

    assert log.count_consecutive_no_improvement("scoring.calibration_notes.problem_analysis") == 2
    assert log.consecutive_no_improvement_global() == 2


def test_mark_forbidden_unit_persists(tmp_path: Path) -> None:
    log_path = tmp_path / "experiments" / "experiment_log.yaml"
    log = ExperimentLog.load(log_path)

    log.mark_forbidden("scoring.calibration_notes.project_execution", "qwk dropped > 0.03")
    assert log.is_forbidden_unit("scoring.calibration_notes.project_execution")

    reloaded = ExperimentLog.load(log_path)
    assert reloaded.is_forbidden_unit("scoring.calibration_notes.project_execution")
    meta = reloaded.forbidden_units["scoring.calibration_notes.project_execution"]
    assert meta["reason"] == "qwk dropped > 0.03"
    assert "marked_at" in meta
