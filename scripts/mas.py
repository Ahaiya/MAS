#!/usr/bin/env python3
"""Unified CLI entrypoint for MAS operations.

Recommended invocation:
  python -m scripts ...

Compatibility alias:
  python scripts/mas.py ...
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv

load_dotenv()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts import eval as eval_cli
from scripts import outer_loop as outer_loop_cli
from src.utils import compute_coverage_metrics as coverage_metrics_cli
from src.utils import compute_qwk as qwk_cli
from src.utils import validate_config as validate_config_cli

app = typer.Typer(
    name="mas",
    help="MAS 统一入口。官方推荐使用 `python -m scripts ...` 进入所有评估、外环、任务启动、指标与配置命令。",
)
outer_app = typer.Typer(name="outer-loop", help="外环优化入口。")
task_app = typer.Typer(name="task", help="任务启动入口。")
metrics_app = typer.Typer(name="metrics", help="指标与诊断工具。")
config_app = typer.Typer(name="config", help="配置校验工具。")

app.add_typer(outer_app, name="outer-loop")
app.add_typer(task_app, name="task")
app.add_typer(metrics_app, name="metrics")
app.add_typer(config_app, name="config")


@app.command("eval")
def eval_command(
    input_path: Annotated[
        Path | None,
        typer.Argument(metavar="INPUT_FILE", help="待评估的工程材料文件。"),
    ] = None,
    input_file: Annotated[
        Path | None,
        typer.Option("--input", "-i", help="兼容写法：待评估的工程材料文件。"),
    ] = None,
    bundle: Annotated[Path, typer.Option("--bundle", "-b")] = eval_cli._DEFAULT_BUNDLE,
    dim: Annotated[str, typer.Option("--dim")] = "",
    model_config: Annotated[Path, typer.Option("--model-config", "-m")] = eval_cli._DEFAULT_MODEL_CONFIG,
    output_dir: Annotated[Path | None, typer.Option("--output-dir", "-o")] = None,
    verbose: Annotated[bool, typer.Option("--verbose/--no-verbose", "-v")] = True,
    debug_bundle: Annotated[bool, typer.Option("--debug-bundle/--no-debug-bundle")] = True,
) -> None:
    """工程评价主入口。使用任务背景 bundle 和所选维度配置评估单个工程材料样本。"""
    eval_cli.main(
        input_path=input_path,
        input_file=input_file,
        bundle=bundle,
        dim=dim,
        model_config=model_config,
        output_dir=output_dir,
        verbose=verbose,
        debug_bundle=debug_bundle,
    )


@outer_app.command("run")
def outer_loop_run(
    max_iterations: Annotated[int, typer.Option("--max-iterations", min=1)] = 5,
    bundle: Annotated[Path, typer.Option("--bundle")] = outer_loop_cli._DEFAULT_BUNDLE,
    training_set: Annotated[Path, typer.Option("--training-set")] = outer_loop_cli._DEFAULT_TRAINING_SET,
) -> None:
    """运行外环优化循环。"""
    outer_loop_cli.run(
        max_iterations=max_iterations,
        bundle=bundle,
        training_set=training_set,
    )


@outer_app.command("status")
def outer_loop_status() -> None:
    """查看外环实验日志摘要。"""
    outer_loop_cli.status()


@outer_app.command("rollback")
def outer_loop_rollback(
    iter_id: Annotated[str, typer.Option("--iter-id")],
) -> None:
    """回滚 configs 到指定迭代快照。"""
    outer_loop_cli.rollback(iter_id=iter_id)


@outer_app.command("probe")
def outer_loop_probe(
    name: Annotated[str, typer.Option("--name")],
    artifacts_dir: Annotated[Path, typer.Option("--artifacts-dir")] = outer_loop_cli._DEFAULT_ARTIFACTS_BASE,
    training_set: Annotated[Path, typer.Option("--training-set")] = outer_loop_cli._DEFAULT_TRAINING_SET,
) -> None:
    """手动运行一个 outer-loop probe。"""
    outer_loop_cli.probe(
        name=name,
        artifacts_dir=artifacts_dir,
        training_set=training_set,
    )


@task_app.command("draft")
def task_draft(
    task_id: Annotated[str, typer.Option("--task-id")],
    task_brief: Annotated[str, typer.Option("--task-brief")] = "",
    task_brief_file: Annotated[Path | None, typer.Option("--task-brief-file")] = None,
    bundle: Annotated[Path, typer.Option("--bundle")] = outer_loop_cli._DEFAULT_BUNDLE,
) -> None:
    """生成任务草案。"""
    outer_loop_cli.task_draft(
        task_id=task_id,
        task_brief=task_brief,
        task_brief_file=task_brief_file,
        bundle=bundle,
    )


@task_app.command("show")
def task_show(
    task_id: Annotated[str, typer.Option("--task-id")],
    bundle: Annotated[Path, typer.Option("--bundle")] = outer_loop_cli._DEFAULT_BUNDLE,
) -> None:
    """查看当前任务草案。"""
    outer_loop_cli.task_show(task_id=task_id, bundle=bundle)


@task_app.command("revise")
def task_revise(
    task_id: Annotated[str, typer.Option("--task-id")],
    instruction: Annotated[str, typer.Option("--instruction")],
    bundle: Annotated[Path, typer.Option("--bundle")] = outer_loop_cli._DEFAULT_BUNDLE,
) -> None:
    """按一条教师指令修订任务草案。"""
    outer_loop_cli.task_revise(
        task_id=task_id,
        instruction=instruction,
        bundle=bundle,
    )


@task_app.command("confirm")
def task_confirm(
    task_id: Annotated[str, typer.Option("--task-id")],
    bundle: Annotated[Path, typer.Option("--bundle")] = outer_loop_cli._DEFAULT_BUNDLE,
) -> None:
    """冻结任务并把 bundle 绑定到当前 active task。"""
    outer_loop_cli.task_confirm(task_id=task_id, bundle=bundle)


@metrics_app.command("qwk")
def metrics_qwk(
    eval_dir: Annotated[Path, typer.Option("--eval-dir", "-e")] = qwk_cli._DEFAULT_EVAL_DIR,
    source: Annotated[Path, typer.Option("--source", "-s")] = qwk_cli._DEFAULT_SOURCE,
    rater: Annotated[str, typer.Option("--rater", "-r")] = "rater1",
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """计算 composite/per-dimension QWK 与评审员一致性。"""
    qwk_cli.main(
        eval_dir=eval_dir,
        source=source,
        rater=rater,
        output=output,
    )


@metrics_app.command("coverage")
def metrics_coverage(
    eval_dir: Annotated[Path, typer.Option("--eval-dir", "-e")] = coverage_metrics_cli._DEFAULT_EVAL_DIR,
    essay_id: Annotated[str, typer.Option("--essay-id")] = "",
    write: Annotated[bool, typer.Option("--write/--no-write")] = True,
) -> None:
    """计算 coverage recall / precision / chunk boundary 诊断。"""
    coverage_metrics_cli.main(
        eval_dir=eval_dir,
        essay_id=essay_id,
        write=write,
    )


@config_app.command("validate")
def config_validate(
    bundle: Annotated[Path, typer.Option("--bundle", "-b")],
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """校验 bundle 的引用闭包、schema 与 freeze hash。"""
    validate_config_cli.main(bundle=bundle, verbose=verbose)


if __name__ == "__main__":
    app()
