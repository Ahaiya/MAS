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
from src.utils import validate_config as validate_config_cli

app = typer.Typer(
    name="mas",
    help="MAS 统一入口。官方推荐使用 `python -m scripts ...` 进入评估与配置校验命令。",
)
config_app = typer.Typer(name="config", help="配置校验工具。")

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


@config_app.command("validate")
def config_validate(
    bundle: Annotated[Path, typer.Option("--bundle", "-b")],
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """校验 bundle 的引用闭包、schema 与 freeze hash。"""
    validate_config_cli.main(bundle=bundle, verbose=verbose)


if __name__ == "__main__":
    app()
