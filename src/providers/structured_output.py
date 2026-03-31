"""
结构化输出规范化模块，负责把原始模型文本解析为稳定 JSON 结构。

Structured output normalization.

Takes raw text content from a provider response and normalises it into a dict:
1. Strip whitespace.
2. Parse as JSON.  Non-JSON content raises ProviderParseError.
3. Ensure the result is a dict (wrap arrays/scalars in {"items"/"value": ...}).
4. If a JSON schema is provided, validate that all required fields are present.

This module has no knowledge of rubric dimensions, policies, or orchestration.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from src.providers.base import ProviderParseError


def normalize_structured_output(
    content: str,
    *,
    schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Parse and normalize raw provider text into a validated dict.

    Args:
        content : Raw text from the provider (expected to be JSON).
        schema  : Optional JSON Schema dict.  When provided, the presence of
                  all fields listed in ``schema["required"]`` is checked.

    Returns:
        Normalised dict.

    Raises:
        ProviderParseError: If content is empty, not valid JSON, or required
                            schema fields are missing.
    """
    stripped = content.strip()
    if not stripped:
        raise ProviderParseError(
            "Provider returned empty content; cannot parse structured output.",
            raw_content=content,
        )

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ProviderParseError(
            f"Failed to parse provider response as JSON: {exc}",
            raw_content=content,
        ) from exc

    normalised = _ensure_dict(parsed, content)

    if schema is not None:
        _validate_required_fields(normalised, schema, content)

    return normalised


# ── Internal helpers ──────────────────────────────────────────────────────────

def _ensure_dict(value: Any, raw: str) -> Dict[str, Any]:
    """Wrap non-dict JSON values into a dict so callers always get a dict."""
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"items": value}
    # Scalar (int, float, str, bool, None)
    return {"value": value}


def _validate_required_fields(
    data: Dict[str, Any],
    schema: Dict[str, Any],
    raw: str,
) -> None:
    """Check that all fields listed in schema['required'] are present in data."""
    required: List[str] = schema.get("required", [])
    missing = [field for field in required if field not in data]
    if missing:
        raise ProviderParseError(
            f"Structured output missing required fields: {missing}",
            raw_content=raw,
        )
