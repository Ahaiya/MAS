from __future__ import annotations

from pathlib import Path

import yaml

from src.outer_loop.optimization.config_patcher import ChangeProposal, ConfigPatcher


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _read_yaml(path: Path) -> dict:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(loaded, dict):
        return loaded
    raise AssertionError(f"expected YAML dict at {path}")


def _build_patcher(tmp_path: Path) -> tuple[ConfigPatcher, Path, Path]:
    configs_root = tmp_path / "configs"
    snapshots_root = tmp_path / "experiments" / "snapshots"

    _write_yaml(
        configs_root / "prompts" / "tasks" / "task_bootstrap_scoring_context.yaml",
        {"scoring_context": {"calibration_notes": "old note"}},
    )
    _write_yaml(
        configs_root / "bundles" / "engineering_eval_baseline.bundle.yaml",
        {
            "artifact_bundle": {
                "provider_config": {
                    "model": "gpt-4.1-mini",
                    "temperature": 0.1,
                }
            }
        },
    )
    _write_yaml(
        configs_root / "prompts" / "scoring.yaml",
        {"prompts": {"system": "hello"}},
    )
    (configs_root / "rubrics" / "source").mkdir(parents=True, exist_ok=True)
    (configs_root / "rubrics" / "source" / "rubric.md").write_text(
        "readonly rubric",
        encoding="utf-8",
    )
    (configs_root / "archive" / "legacy_eval").mkdir(parents=True, exist_ok=True)
    (configs_root / "archive" / "legacy_eval" / "legacy.yaml").write_text(
        "legacy: true\n",
        encoding="utf-8",
    )

    patcher = ConfigPatcher(
        configs_root=configs_root,
        snapshots_root=snapshots_root,
    )
    return patcher, configs_root, snapshots_root


def test_rejects_non_whitelisted_file(tmp_path: Path) -> None:
    patcher, configs_root, snapshots_root = _build_patcher(tmp_path)
    original = (
        configs_root / "prompts" / "tasks" / "task_bootstrap_scoring_context.yaml"
    ).read_text(encoding="utf-8")

    proposal = ChangeProposal(
        change_unit="rubric.illegal",
        change_type="field_patch",
        target_file="configs/rubrics/source/rubric.md",
        target_path="x.y",
        new_value="new",
        rationale="should be blocked",
    )
    ok, message = patcher.apply(proposal, iter_id="001")
    assert ok is False
    assert "whitelist" in message
    assert (
        configs_root / "prompts" / "tasks" / "task_bootstrap_scoring_context.yaml"
    ).read_text(
        encoding="utf-8"
    ) == original
    assert not snapshots_root.exists()


def test_blocks_protected_bundle_field(tmp_path: Path) -> None:
    _, configs_root, snapshots_root = _build_patcher(tmp_path)
    patcher = ConfigPatcher(
        configs_root=configs_root,
        snapshots_root=snapshots_root,
        allowed_file_patterns=(
            *ConfigPatcher().allowed_file_patterns,
            "configs/bundles/engineering_eval_baseline.bundle.yaml",
        ),
    )
    bundle_path = configs_root / "bundles" / "engineering_eval_baseline.bundle.yaml"
    before = bundle_path.read_text(encoding="utf-8")

    proposal = ChangeProposal(
        change_unit="bundle.model",
        change_type="field_patch",
        target_file="configs/bundles/engineering_eval_baseline.bundle.yaml",
        target_path="artifact_bundle.provider_config.model",
        new_value="gpt-5",
        rationale="should be blocked",
    )
    ok, message = patcher.apply(proposal, iter_id="001")
    assert ok is False
    assert "protected" in message
    assert bundle_path.read_text(encoding="utf-8") == before


def test_applies_valid_patch_and_creates_snapshot(tmp_path: Path) -> None:
    patcher, configs_root, snapshots_root = _build_patcher(tmp_path)
    target_path = configs_root / "prompts" / "tasks" / "task_bootstrap_scoring_context.yaml"

    proposal = ChangeProposal(
        change_unit="scoring.calibration_notes.problem_analysis",
        change_type="field_patch",
        target_file="configs/prompts/tasks/task_bootstrap_scoring_context.yaml",
        target_path="scoring_context.calibration_notes",
        new_value="new note",
        rationale="improve alignment",
    )
    ok, message = patcher.apply(proposal, iter_id="001")
    assert ok is True
    assert "snapshot" in message

    current = _read_yaml(target_path)
    assert current["scoring_context"]["calibration_notes"] == "new note"

    snapshot_file = (
        snapshots_root
        / "iter_001"
        / "configs"
        / "prompts"
        / "tasks"
        / "task_bootstrap_scoring_context.yaml"
    )
    assert snapshot_file.exists()
    snap = _read_yaml(snapshot_file)
    assert snap["scoring_context"]["calibration_notes"] == "old note"
    assert not (snapshots_root / "iter_001" / "configs" / "archive").exists()
    assert not (snapshots_root / "iter_001" / "configs" / "rubrics" / "source").exists()


def test_invalid_yaml_overwrite_triggers_rollback(tmp_path: Path) -> None:
    patcher, configs_root, snapshots_root = _build_patcher(tmp_path)
    target_path = configs_root / "prompts" / "tasks" / "task_bootstrap_scoring_context.yaml"
    before = target_path.read_text(encoding="utf-8")

    proposal = ChangeProposal(
        change_unit="scoring.rewrite",
        change_type="file_overwrite",
        target_file="configs/prompts/tasks/task_bootstrap_scoring_context.yaml",
        target_path="",
        new_value="broken_yaml: [1, 2",
        rationale="force parser failure",
    )
    ok, message = patcher.apply(proposal, iter_id="002")
    assert ok is False
    assert "rolled back" in message

    after = target_path.read_text(encoding="utf-8")
    assert after == before
    assert (snapshots_root / "iter_002" / "configs").exists()


def test_rollback_restores_snapshot_state(tmp_path: Path) -> None:
    patcher, configs_root, _ = _build_patcher(tmp_path)
    target_path = configs_root / "prompts" / "tasks" / "task_bootstrap_scoring_context.yaml"

    proposal = ChangeProposal(
        change_unit="scoring.calibration_notes.problem_analysis",
        change_type="field_patch",
        target_file="configs/prompts/tasks/task_bootstrap_scoring_context.yaml",
        target_path="scoring_context.calibration_notes",
        new_value="first update",
        rationale="test rollback",
    )
    ok, _ = patcher.apply(proposal, iter_id="001")
    assert ok is True
    assert _read_yaml(target_path)["scoring_context"]["calibration_notes"] == "first update"

    _write_yaml(target_path, {"scoring_context": {"calibration_notes": "manual drift"}})
    restored = patcher.rollback("001")
    assert restored is True
    assert _read_yaml(target_path)["scoring_context"]["calibration_notes"] == "old note"
    assert (configs_root / "archive" / "legacy_eval" / "legacy.yaml").exists()
    assert (configs_root / "rubrics" / "source" / "rubric.md").exists()
