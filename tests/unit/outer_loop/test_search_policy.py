from __future__ import annotations

from pathlib import Path

from src.outer_loop.experiments.experiment_log import ExperimentLog, IterationRecord
from src.outer_loop.optimization.search_policy import PRIORITY_LAYERS, SearchPolicy


def _record(iteration: int, changed_unit: str, verdict: str) -> IterationRecord:
    return IterationRecord(
        iteration=iteration,
        timestamp=f"2026-04-01T10:{iteration:02d}:00",
        changed_unit=changed_unit,
        change_description="test",
        target_file="configs/prompts/scoring_context.yaml",
        target_path="calibration_notes.ideas_content",
        probe_used=["coverage_probe"],
        probe_results={},
        verdict=verdict,
        next_hypothesis="next",
        config_snapshot_path=f"experiments/snapshots/iter_{iteration:03d}/",
    )


def _log(tmp_path: Path) -> ExperimentLog:
    return ExperimentLog.load(tmp_path / "experiments" / "experiment_log.yaml")


def test_should_force_transfer_after_two_consecutive_failures(tmp_path: Path) -> None:
    log = _log(tmp_path)
    policy = SearchPolicy()
    unit = "scoring.calibration_notes.ideas_content"

    log.append(_record(1, unit, "no-improvement"))
    assert policy.should_force_transfer(log, unit) is False

    log.append(_record(2, unit, "no-improvement"))
    assert policy.should_force_transfer(log, unit) is True


def test_should_rollback_only_when_qwk_drop_exceeds_threshold() -> None:
    policy = SearchPolicy()
    assert policy.should_rollback(prev_qwk=0.80, new_qwk=0.76) is True
    assert policy.should_rollback(prev_qwk=0.80, new_qwk=0.77) is False


def test_is_exploration_mode_after_five_global_no_improvement(tmp_path: Path) -> None:
    log = _log(tmp_path)
    policy = SearchPolicy()
    for idx in range(1, 6):
        log.append(_record(idx, "coverage.chunking", "no-improvement"))
    assert policy.is_exploration_mode(log) is True


def test_is_forbidden_unit_delegates_to_log(tmp_path: Path) -> None:
    log = _log(tmp_path)
    policy = SearchPolicy()
    unit = "feedback.template"

    assert policy.is_forbidden_unit(log, unit) is False
    log.mark_forbidden(unit, "qwk drop")
    assert policy.is_forbidden_unit(log, unit) is True


def test_get_priority_layer_uses_mapping_and_fallback() -> None:
    policy = SearchPolicy(priority_layers=PRIORITY_LAYERS)
    assert policy.get_priority_layer("scoring.calibration") == 1
    assert policy.get_priority_layer("coverage.chunking") == 2
    assert policy.get_priority_layer("feedback.style") == 4
    assert policy.get_priority_layer("unknown.unit") == 5


def test_should_escalate_layer_on_upstream_probe_regression(tmp_path: Path) -> None:
    log = _log(tmp_path)
    policy = SearchPolicy()
    log.append(_record(1, "feedback.template", "effective"))

    should_escalate = policy.should_escalate_layer(
        log,
        probes={"coverage_probe": {"metrics": {"coverage_recall_rate": 0.62}}},
    )
    assert should_escalate is True


def test_should_not_escalate_in_exploration_mode(tmp_path: Path) -> None:
    log = _log(tmp_path)
    policy = SearchPolicy()
    for idx in range(1, 6):
        log.append(_record(idx, "coverage.chunking", "no-improvement"))

    should_escalate = policy.should_escalate_layer(
        log,
        probes={"coverage_probe": {"metrics": {"coverage_recall_rate": 0.50}}},
    )
    assert should_escalate is False
