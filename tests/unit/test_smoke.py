"""Smoke tests for basic project setup.

These tests verify that the project structure and basic imports work correctly.
They should pass after Phase 0: Repository & Environment Setup.
"""

from pathlib import Path

import pytest


class TestDirectoryStructure:
    """Verify that all required directories exist."""

    @pytest.fixture
    def project_root(self) -> Path:
        """Get the project root directory."""
        return Path(__file__).parent.parent.parent

    def test_configs_directory_exists(self, project_root: Path) -> None:
        """Configs directory should exist."""
        assert (project_root / "configs").is_dir()

    def test_src_directory_exists(self, project_root: Path) -> None:
        """Src directory should exist."""
        assert (project_root / "src").is_dir()

    def test_tests_directory_exists(self, project_root: Path) -> None:
        """Tests directory should exist."""
        assert (project_root / "tests").is_dir()

    def test_scripts_directory_exists(self, project_root: Path) -> None:
        """Scripts directory should exist."""
        assert (project_root / "scripts").is_dir()

    def test_data_directory_exists(self, project_root: Path) -> None:
        """Data directory should exist."""
        assert (project_root / "data").is_dir()

    def test_data_samples_directory_exists(self, project_root: Path) -> None:
        """Data/samples directory should exist."""
        assert (project_root / "data" / "samples").is_dir()


class TestPackageStructure:
    """Verify that all Python packages have __init__.py files."""

    @pytest.fixture
    def project_root(self) -> Path:
        """Get the project root directory."""
        return Path(__file__).parent.parent.parent

    def test_src_init_exists(self, project_root: Path) -> None:
        """src/__init__.py should exist."""
        assert (project_root / "src" / "__init__.py").is_file()

    def test_contracts_package_exists(self, project_root: Path) -> None:
        """src/contracts/__init__.py should exist."""
        assert (project_root / "src" / "contracts" / "__init__.py").is_file()

    def test_config_package_exists(self, project_root: Path) -> None:
        """src/config/__init__.py should exist."""
        assert (project_root / "src" / "config" / "__init__.py").is_file()

    def test_orchestrator_package_exists(self, project_root: Path) -> None:
        """src/orchestrator/__init__.py should exist."""
        assert (project_root / "src" / "orchestrator" / "__init__.py").is_file()

    def test_agents_package_exists(self, project_root: Path) -> None:
        """src/agents/__init__.py should exist."""
        assert (project_root / "src" / "agents" / "__init__.py").is_file()

    def test_policies_package_exists(self, project_root: Path) -> None:
        """src/policies/__init__.py should exist."""
        assert (project_root / "src" / "policies" / "__init__.py").is_file()

    def test_providers_package_exists(self, project_root: Path) -> None:
        """src/providers/__init__.py should exist."""
        assert (project_root / "src" / "providers" / "__init__.py").is_file()

    def test_pipeline_package_exists(self, project_root: Path) -> None:
        """src/pipeline/__init__.py should exist."""
        assert (project_root / "src" / "pipeline" / "__init__.py").is_file()

    def test_evaluation_package_exists(self, project_root: Path) -> None:
        """src/evaluation/__init__.py should exist."""
        assert (project_root / "src" / "evaluation" / "__init__.py").is_file()


class TestPackageImports:
    """Verify that all packages can be imported."""

    def test_import_config_package(self) -> None:
        """config package should be importable."""
        import src.config  # noqa: F401

    def test_import_config_loader(self) -> None:
        """ConfigLoader should be importable."""
        from src.config.loader import ConfigLoader, load_bundle  # noqa: F401


class TestProjectMetadata:
    """Verify that project metadata files exist and are valid."""

    @pytest.fixture
    def project_root(self) -> Path:
        """Get the project root directory."""
        return Path(__file__).parent.parent.parent

    def test_pyproject_toml_exists(self, project_root: Path) -> None:
        """pyproject.toml should exist."""
        assert (project_root / "pyproject.toml").is_file()

    def test_pyproject_toml_is_valid(self, project_root: Path) -> None:
        """pyproject.toml should be valid TOML."""
        import tomllib

        with open(project_root / "pyproject.toml", "rb") as f:
            tomllib.load(f)

    def test_pytest_ini_exists(self, project_root: Path) -> None:
        """pytest.ini should exist."""
        assert (project_root / "pytest.ini").is_file()

    def test_python_version_file_exists(self, project_root: Path) -> None:
        """.python-version should exist."""
        assert (project_root / ".python-version").is_file()

    def test_env_example_exists(self, project_root: Path) -> None:
        """.env.example should exist."""
        assert (project_root / ".env.example").is_file()


class TestScriptEntryPoints:
    """Verify that command entry points exist."""

    @pytest.fixture
    def project_root(self) -> Path:
        """Get the project root directory."""
        return Path(__file__).parent.parent.parent

    def test_validate_config_module_exists(self, project_root: Path) -> None:
        """validate_config utility module should exist."""
        assert (project_root / "src" / "utils" / "validate_config.py").is_file()

    def test_validate_config_help_works(self, project_root: Path) -> None:
        """validate-config help should work via the unified CLI."""
        import subprocess

        result = subprocess.run(
            ["python", "-m", "scripts", "config", "validate", "--help"],
            capture_output=True,
            text=True,
            cwd=project_root,
        )
        assert result.returncode == 0
        assert "校验 bundle" in result.stdout or "validate" in result.stdout.lower()
