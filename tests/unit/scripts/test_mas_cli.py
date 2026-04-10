from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import scripts
from scripts import __main__ as scripts_main
from scripts import mas

runner = CliRunner()


def test_package_reexports_unified_app() -> None:
    assert scripts.app is mas.app


def test_root_help_lists_major_command_groups() -> None:
    result = runner.invoke(mas.app, ["--help"])
    assert result.exit_code == 0
    for command_name in ("eval", "outer-loop", "task", "metrics", "config"):
        assert command_name in result.stdout


def test_eval_command_dispatches_to_underlying_script(monkeypatch, tmp_path: Path) -> None:
    called: dict[str, object] = {}

    def fake_main(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr(mas.eval_cli, "main", fake_main)

    input_path = tmp_path / "sample.md"
    result = runner.invoke(
        mas.app,
        ["eval", "--input", str(input_path), "--dim", "A4"],
    )

    assert result.exit_code == 0
    assert called["input_file"] == input_path
    assert called["dim"] == "A4"
    assert called["model_config"] == mas.eval_cli._DEFAULT_MODEL_CONFIG


def test_eval_command_accepts_positional_input(monkeypatch, tmp_path: Path) -> None:
    called: dict[str, object] = {}

    def fake_main(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr(mas.eval_cli, "main", fake_main)

    input_path = tmp_path / "4组—AI助手.md"
    result = runner.invoke(mas.app, ["eval", str(input_path)])

    assert result.exit_code == 0
    assert called["input_path"] == input_path
    assert called["input_file"] is None
    assert called["debug_bundle"] is True
    assert called["model_config"] == mas.eval_cli._DEFAULT_MODEL_CONFIG


def test_eval_command_passes_model_config_override(monkeypatch, tmp_path: Path) -> None:
    called: dict[str, object] = {}

    def fake_main(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr(mas.eval_cli, "main", fake_main)

    input_path = tmp_path / "sample.md"
    model_config = tmp_path / "models.yaml"
    result = runner.invoke(
        mas.app,
        ["eval", str(input_path), "--dim", "B1", "--model-config", str(model_config)],
    )

    assert result.exit_code == 0
    assert called["input_path"] == input_path
    assert called["dim"] == "B1"
    assert called["model_config"] == model_config


def test_task_confirm_dispatches_to_underlying_script(monkeypatch) -> None:
    called: dict[str, object] = {}

    def fake_task_confirm(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr(mas.outer_loop_cli, "task_confirm", fake_task_confirm)

    result = runner.invoke(mas.app, ["task", "confirm", "--task-id", "demo"])
    assert result.exit_code == 0
    assert called["task_id"] == "demo"


def test_metrics_qwk_dispatches_to_underlying_script(monkeypatch, tmp_path: Path) -> None:
    called: dict[str, object] = {}

    def fake_main(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr(mas.qwk_cli, "main", fake_main)

    eval_dir = tmp_path / "eval"
    source = tmp_path / "scores.tsv"
    result = runner.invoke(
        mas.app,
        [
            "metrics",
            "qwk",
            "--eval-dir",
            str(eval_dir),
            "--source",
            str(source),
            "--rater",
            "average",
        ],
    )

    assert result.exit_code == 0
    assert called["eval_dir"] == eval_dir
    assert called["source"] == source
    assert called["rater"] == "average"


def test_package_main_delegates_to_unified_app(monkeypatch) -> None:
    called: dict[str, bool] = {}

    def fake_app() -> None:
        called["invoked"] = True

    monkeypatch.setattr(scripts_main, "app", fake_app)
    scripts_main.main()
    assert called["invoked"] is True
