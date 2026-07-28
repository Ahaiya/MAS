#!/usr/bin/env python3
"""MAS 命令行入口——单文件 CLI

用法：
  python scripts/cli.py eval <file> --task experiment --dim a4   # 评单个一级指标
  python scripts/cli.py eval <file> --task experiment            # 评该任务下全部一级指标
  python scripts/cli.py config validate --task experiment        # 校验配置能否加载


命令体只做「建 Engine → evaluate → 打印」：
模型/参数固定从 `configs/model_config.yaml`读，
密钥值只从 `.env` 读，
评价逻辑全在 `src/engine.py`。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Dict, List, Optional

import typer

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import yaml
from dotenv import load_dotenv

from src.config.compiler import (
    PROMPT_STAGES,
    ConfigCompileError,
    list_task_dimension_ids,
    load_adjudication_policy,
    load_dimension_rubric,
    prompt_path,
)
from src.contracts.package import DataPackage
from src.engine import DimensionEvaluation, Engine
from src.engine_config import EngineConfigError, load_runtime_config, validate_model_config
from src.providers.prompt_loader import PromptLoader
from src.segment import read_text_file

load_dotenv()

_DEFAULT_CONFIGS_ROOT = _PROJECT_ROOT / "configs"
_DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "artifacts"

app = typer.Typer(name="mas", help="MAS 评价引擎命令行入口。", add_completion=False)
config_app = typer.Typer(name="config", help="配置校验工具。", add_completion=False)
app.add_typer(config_app, name="config")


# 用户能自己修的配置类错误（改 yaml、补 .env、纠正 --dim 拼写）统一按"印一行人话"
# 处理，不甩 traceback；不在这张网里的异常照常向上抛，别把 bug 藏起来。
# 注意：新增的校验必须抛这些类型之一，否则一个 yaml 笔误就会变成一屏 traceback。
_USER_FIXABLE_ERRORS = (
    EngineConfigError,
    ConfigCompileError,
    FileNotFoundError,
    yaml.YAMLError,
    KeyError,
)


def _exit_with_error(message: str) -> typer.Exit:
    """打印错误到 stderr，返回给调用方 raise 的 Exit(1)。"""
    typer.echo(f"ERROR: {message}", err=True)
    return typer.Exit(code=1)


def _available_tasks(configs_root: Path) -> List[str]:
    """列出 `{configs_root}/tasks/` 下的任务 id（有 dimension 子目录的才算）。"""
    tasks_dir = configs_root / "tasks"
    if not tasks_dir.is_dir():
        return []
    return sorted(p.name for p in tasks_dir.iterdir() if (p / "dimension").is_dir())


def _require_task(configs_root: Path, task: Optional[str]) -> str:
    """任务必须显式指定——没有默认值，也不沿用任何配置文件里的值。

    旧的 `active_task_id` 写在 bundle 里，切任务要改一个 tracked 文件；漏改就会
    静默评错任务，而产物目录名和分数都"看起来正常"。宁可在这里报错。"""
    if not configs_root.is_dir():
        raise _exit_with_error(f"配置目录不存在：{configs_root}")

    available = _available_tasks(configs_root)
    if task is None:
        hint = "、".join(available) if available else "（该目录下没有任何任务）"
        raise _exit_with_error(f"必须用 --task 指定任务。可选：{hint}")
    if task not in available:
        hint = "、".join(available) if available else "（该目录下没有任何任务）"
        raise _exit_with_error(f"任务 '{task}' 不存在于 {configs_root / 'tasks'}。可选：{hint}")
    return task


# ── eval ─────────────────────────────────────────────────────────────────────


def _render_summary(results: Dict[str, DimensionEvaluation]) -> str:
    """把 evaluate() 的返回渲染成给人看的摘要。

    失败隔离（07）下被跳过的二级指标必须一并打印——否则用户会以为所有维度都评过，
    而实际上 feedback.json 里少了几个维度。"""
    lines: List[str] = []
    for dim_id in sorted(results):
        evaluation = results[dim_id]
        report = evaluation.feedback_report
        trace = evaluation.run_trace

        # primary_score 为 None = 该一级指标下全部二级指标都失败，没评出分；
        # 与"评了、得低分"是两回事，不能印成 0.00。
        primary_score = report["primary_score"]
        headline = "全部二级指标评价失败" if primary_score is None else f"{primary_score:.2f}"
        lines.append(f"[{dim_id}] 一级指标分：{headline}")
        for sub_dim_id, entry in sorted(report["dimensions"].items()):
            lines.append(f"  {sub_dim_id}: {entry['final_score']}  ({entry['source']})")
        for failed in trace.failed_dims:
            lines.append(f"  {failed['dimension_id']}: 评价失败 — {failed['error']}")
        lines.append(f"  tokens={trace.total_tokens}  耗时={trace.total_ms / 1000:.1f}s")
    return "\n".join(lines)


def _load_package(input_file: Path) -> DataPackage:
    """IO 边界：读文件 → 切分 → DataPackage。sample 名取文件名（不含后缀）。

    不存在/后缀不支持在这里就报错退出，不让它烂到 engine 里才炸。"""
    if not input_file.exists():
        raise _exit_with_error(f"输入文件不存在：{input_file}")

    try:
        package, dropped_unit_ids = read_text_file(input_file, package_id=input_file.stem)
    except ValueError as exc:  # read_text_file 对不支持的后缀抛 ValueError
        raise _exit_with_error(str(exc)) from exc

    if dropped_unit_ids:
        typer.echo(
            f"WARNING: 超出上下文预算，已丢弃 {len(dropped_unit_ids)} 个单元"
            f"（编号 {dropped_unit_ids[0]}–{dropped_unit_ids[-1]}）。",
            err=True,
        )
    return package


@app.command("eval")
def eval_command(
    input_file: Annotated[
        Path,
        typer.Argument(metavar="INPUT_FILE", help="待评价的材料文件（.md / .txt）。"),
    ],
    task: Annotated[
        Optional[str],
        typer.Option("--task", help="任务 id，对应 configs/tasks/<task>/。"),
    ] = None,
    configs: Annotated[
        Path,
        typer.Option("--configs", help="配置根目录。"),
    ] = _DEFAULT_CONFIGS_ROOT,
    dim: Annotated[
        Optional[str],
        typer.Option("--dim", help="一级指标（如 a4）；缺省评该任务下全部一级指标。"),
    ] = None,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="产物落盘根目录。"),
    ] = _DEFAULT_OUTPUT_DIR,
) -> None:
    """评价一份材料：切分 → 双链评价 → 仲裁 → 反馈，产物按 {task}/{sample}/{dim}/ 落盘。"""
    task_id = _require_task(configs, task)
    package = _load_package(input_file)

    try:
        engine = Engine.from_configs(configs, task_id, output_dir=output_dir)
        results = engine.evaluate(package, dim=dim)
    except _USER_FIXABLE_ERRORS as exc:
        raise _exit_with_error(f"{type(exc).__name__}: {exc}") from exc

    typer.echo(_render_summary(results))
    typer.echo(f"产物已写入：{output_dir}")

    # 一个分都没评出来时退非零：脚本/CI 才能发现这次"跑完了"其实什么都没产出。
    if all(not r.feedback_report["dimensions"] for r in results.values()):
        raise _exit_with_error("本次评价没有产出任何二级指标分数——见上方各维度失败原因。")


# ── config validate ──────────────────────────────────────────────────────────


def _validate_configs(configs_root: Path, task_id: str) -> List[str]:
    """按约定路径走一遍全部配置，返回给人看的 OK 行；任何一处解析不了就抛错。

    刻意不建 provider：配置是否自洽与密钥是否就位是两件事，`config validate` 只答
    前一件（含 model_config 的结构），因此没有 .env 也能在 CI 里跑。路径解析与
    仲裁策略加载都与 Engine 共用同一批函数，避免"校验通过但引擎跑不起来"。"""
    lines = [f"OK  task           : {task_id}"]

    policy = load_adjudication_policy(configs_root)
    lines.append(
        f"OK  adjudication   : score_gap>{policy.score_gap_threshold} "
        f"drift>={policy.drift_min_dimensions}"
    )

    loader = PromptLoader()
    for stage in PROMPT_STAGES:
        path = prompt_path(configs_root, stage)
        if not path.exists():
            raise ConfigCompileError(f"提示词文件不存在：{path}")
        loader.load(path)  # 顺带校验 prompt yaml 的结构
        lines.append(f"OK  prompt         : {stage}")

    dim_ids = list_task_dimension_ids(configs_root, task_id)
    if not dim_ids:
        raise ConfigCompileError(f"任务 '{task_id}' 下没有任何 *_rubric.yaml——无可评的一级指标。")
    for dim_id in dim_ids:
        rubric = load_dimension_rubric(configs_root, task_id, dim_id)
        sub_dim_ids = [d["dimension_id"] for d in rubric.dimensions]
        lines.append(
            f"OK  一级指标       : {dim_id}"
            f"（{len(sub_dim_ids)} 个二级指标：{', '.join(sub_dim_ids)}）"
        )

    # model_config 是模型/参数的唯一来源且必填项不少，一并校验结构——否则漏填
    # model 要等到真跑评价、建 provider 时才炸。只看字段在不在，不读密钥、不建
    # provider，因此 CI 里没有 .env 也能跑。
    entries = validate_model_config(configs_root / "model_config.yaml")
    for name, entry in sorted(entries.items()):
        lines.append(f"OK  provider       : {name} → {entry.model} @ {entry.api_base}")
    max_workers, retry = load_runtime_config(configs_root / "model_config.yaml")
    lines.append(
        f"OK  runtime        : max_workers={max_workers} timeout={retry.timeout_seconds:g}s "
        f"retries={retry.max_retries}"
    )

    return lines


@config_app.command("validate")
def config_validate(
    task: Annotated[
        Optional[str],
        typer.Option("--task", help="任务 id，对应 configs/tasks/<task>/。"),
    ] = None,
    configs: Annotated[
        Path,
        typer.Option("--configs", help="配置根目录。"),
    ] = _DEFAULT_CONFIGS_ROOT,
) -> None:
    """校验配置：仲裁策略 / 提示词 / 任务下各一级指标量规 / model_config 都能加载。"""
    task_id = _require_task(configs, task)

    try:
        lines = _validate_configs(configs, task_id)
    except (ConfigCompileError, EngineConfigError, ValueError, FileNotFoundError, yaml.YAMLError) as exc:
        raise _exit_with_error(str(exc)) from exc

    for line in lines:
        typer.echo(line)
    typer.echo("PASS: 配置校验通过。")


if __name__ == "__main__":
    app()
