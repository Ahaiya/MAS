"""Configuration loading and resolution module.

This module provides infrastructure for loading, validating, and resolving
configuration bundles that drive the MAS evaluation system.
"""

from .loader import ConfigLoader, load_bundle

__all__ = ["ConfigLoader", "load_bundle"]
