#!/usr/bin/env python3
"""Validate configuration bundles.

Recommended unified entry:
  python -m scripts config validate --bundle ...

Compiles a bundle YAML file through the full resolver/compiler pipeline
and reports version closure, schema validation, and freeze hash.
"""

import sys
from pathlib import Path

import typer

# Ensure project root is on sys.path when run as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

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
    """Validate a configuration bundle.

    Loads the bundle, resolves all artifact references, validates each
    against its schema, computes freeze hashes, and reports the result.
    """
    if not bundle.exists():
        typer.echo(f"ERROR: Bundle file not found: {bundle}", err=True)
        raise typer.Exit(code=1)

    if verbose:
        typer.echo(f"Validating bundle: {bundle}")

    compiler = ConfigCompiler(configs_root=bundle.parent.parent)

    try:
        resolved = compiler.compile(bundle)
    except ConfigCompileError as exc:
        typer.echo(f"FAIL: Bundle validation failed.", err=True)
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
