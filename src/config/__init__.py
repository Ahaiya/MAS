"""配置子系统入口，负责装载、校验并解析驱动流水线运行的 bundle 配置。

Configuration loading and resolution module.

This module provides infrastructure for loading, validating, and resolving
configuration bundles that drive the MAS evaluation system.
"""

from .loader import ConfigLoader, load_bundle
from .compiler import ConfigCompiler, ConfigCompileError
from .resolver import ConfigResolver, ResolverError
from .freeze import compute_content_hash, compute_bundle_hash

__all__ = [
    "ConfigLoader",
    "load_bundle",
    "ConfigCompiler",
    "ConfigCompileError",
    "ConfigResolver",
    "ResolverError",
    "compute_content_hash",
    "compute_bundle_hash",
]
