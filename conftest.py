"""
Root conftest.py — loads .env before any test runs.

This ensures that @pytest.mark.real tests pick up credentials from a local
.env file without requiring the caller to export them manually.
"""

from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (same directory as this file).
_ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(_ENV_FILE, override=False)
