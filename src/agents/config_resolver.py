"""
Config Resolver — wraps the real ConfigCompiler.

This worker never calls an LLM. It compiles a bundle YAML file into a frozen
ResolvedArtifactBundle using the real ConfigCompiler.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from src.config.compiler import ConfigCompiler
from src.contracts.artifact_bundle import ResolvedArtifactBundle


def run(bundle_path: Union[str, Path]) -> ResolvedArtifactBundle:
    """Compile a bundle file into a frozen ResolvedArtifactBundle.

    Args:
        bundle_path: Path to the bundle YAML file (relative to cwd or absolute).

    Returns:
        A fully frozen ResolvedArtifactBundle with rubric and policy snapshots.
    """
    compiler = ConfigCompiler()
    return compiler.compile(bundle_path)
