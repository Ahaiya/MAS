# !/usr/bin/env python3
"""验证配置 bundles。

推荐入口：
  python -m scripts config validate --bundle ...

通过完整的 resolver/compiler pipeline 编译 bundle YAML 文件
并报告 version closure、schema validation 和 freeze hash。"""

import sys
from pathlib import Path

import typer

# 当作为脚本运行时，确保项目根目录在 sys.path 上
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.compiler import ConfigCompiler, ConfigCompileError

app = typer.Typer(
    name="validate-config",
    help="Validate configuration bundles for MAS evaluation system.",
)


@app.command()
def main(
    bundle: Path = typer.Option(
        ...,
        "--bundle",
        "-b",
        help="Path to the bundle YAML file to validate.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed validation results.",
    ),
) -> None:
    """验证配置 bundle。
    
        加载 bundle，解析所有 artifact 引用，根据各自的
        schema 验证每一项，计算 freeze hashes，并报告结果。"""
    if not bundle.exists():
        typer.echo(f"ERROR: Bundle file not found: {bundle}", err=True)
        raise typer.Exit(code=1)

    if verbose:
        typer.echo(f"Validating bundle: {bundle}")

    compiler = ConfigCompiler(configs_root=bundle.parent.parent)

    try:
        resolved = compiler.compile(bundle)
    except ConfigCompileError as exc:
        typer.echo("FAIL: Bundle validation failed.", err=True)
        typer.echo(f"      {exc}", err=True)
        raise typer.Exit(code=1)

    bundle_info = resolved.get_frozen_config_summary()

    typer.echo(f"OK  bundle_id       : {bundle_info['bundle_id']}")
    typer.echo(f"OK  bundle_version  : {bundle_info['bundle_version']}")
    typer.echo(f"OK  rubric_id       : {bundle_info['rubric_id']}")
    typer.echo(f"OK  rubric_version  : {bundle_info['rubric_version']}")
    typer.echo(f"OK  policy_version  : {bundle_info['policy_version']}")
    typer.echo(f"OK  total_hash      : {bundle_info['total_hash'][:16]}...")
    typer.echo(f"OK  resolved_at     : {bundle_info['resolved_at']}")

    if verbose:
        typer.echo("")
        typer.echo(f"Dimensions : {len(resolved.rubric_snapshot.dimensions)}")
        typer.echo(f"Scales     : {len(resolved.rubric_snapshot.scales)}")
        typer.echo(f"Prompts    : {len(resolved.prompt_templates)}")
        typer.echo(f"Version    : {resolved.get_version_info()}")

    typer.echo("PASS: Bundle validation complete.")


if __name__ == "__main__":
    app()
