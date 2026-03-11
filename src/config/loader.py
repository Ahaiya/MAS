"""Configuration loader for artifact bundles."""

from pathlib import Path
from typing import Any

# TODO: Define ArtifactBundle schema in Phase 1
ArtifactBundle = dict[str, Any]


class ConfigLoadError(Exception):
    """Raised when configuration fails to load or validate."""


class ConfigLoader:
    """Loads and validates configuration bundles from YAML files."""

    def __init__(self, config_root: Path | str | None = None):
        """Initialize the config loader.

        Args:
            config_root: Root directory for config files. Defaults to 'configs/'.
        """
        self.config_root = Path(config_root or "configs/")

    def load_bundle(self, bundle_path: Path | str) -> ArtifactBundle:
        """Load an artifact bundle from a YAML file.

        Args:
            bundle_path: Path to the bundle YAML file.

        Returns:
            The loaded artifact bundle.

        Raises:
            ConfigLoadError: If the file doesn't exist or fails to parse.
        """
        bundle_file = self.config_root / bundle_path

        if not bundle_file.exists():
            raise ConfigLoadError(f"Bundle file not found: {bundle_file}")

        # TODO: Implement actual YAML loading in Phase 1
        # For now, return empty dict as placeholder
        return {}


def load_bundle(bundle_path: Path | str, config_root: Path | str | None = None) -> ArtifactBundle:
    """Convenience function to load a bundle.

    Args:
        bundle_path: Path to the bundle YAML file.
        config_root: Root directory for config files.

    Returns:
        The loaded artifact bundle.
    """
    loader = ConfigLoader(config_root)
    return loader.load_bundle(bundle_path)
