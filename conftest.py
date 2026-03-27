"""
Root conftest.py — opt-in handling for real-provider tests.

Default test runs should stay hermetic and avoid outbound LLM calls. Real
provider smoke tests are therefore skipped unless the caller explicitly opts in
with `pytest -m real` or `RUN_REAL_TESTS=1`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from dotenv import load_dotenv

_ENV_FILE = Path(__file__).parent / ".env"


def _real_tests_enabled(config: pytest.Config) -> bool:
    """Return True when the current pytest invocation explicitly enables real tests."""
    if os.environ.get("RUN_REAL_TESTS") == "1":
        return True

    markexpr = (getattr(config.option, "markexpr", "") or "").strip()
    if not markexpr:
        return False

    normalized = " ".join(markexpr.split())
    if normalized == "real":
        return True

    return bool(re.search(r"\breal\b", normalized) and "not real" not in normalized)


def pytest_configure(config: pytest.Config) -> None:
    if _real_tests_enabled(config):
        load_dotenv(_ENV_FILE, override=False)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _real_tests_enabled(config):
        return

    skip_real = pytest.mark.skip(
        reason="real LLM tests are opt-in; run with `pytest -m real` or set RUN_REAL_TESTS=1"
    )
    for item in items:
        if "real" in item.keywords:
            item.add_marker(skip_real)
