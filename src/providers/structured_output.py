"""
结构化输出规范化模块，负责把原始模型文本解析为稳定 JSON 结构。

结构化输出规范化。

从 provider 响应中提取原始文本内容，并将其规范化为 dict：
1. 去除空白字符。
2. 解析为 JSON。非 JSON 内容会引发 ProviderParseError。
3. 确保结果是 dict（将 arrays/scalars 包装在 {"items"/"value": ...} 中）。
4. 如果提供了 JSON schema，则验证是否包含所有必填字段。

此模块不了解 rubric dimensions、策略或编排逻辑。"""

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
    将原始 provider 文本解析并规范化为已验证的 dict。
    
    Args:
        content : 来自 provider 的原始文本（预期为 JSON）。
        schema  : 可选的 JSON Schema dict。提供时，会检查
                  ``schema["required"]`` 中列出的所有字段是否存在。
    
    Returns:
        规范化的 dict。
    
    Raises:
        ProviderParseError: 如果 content 为空、不是有效的 JSON，或者缺少
                            必填的 schema 字段。"""
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


# ── 内部辅助函数 ──────────────────────────────────────────────────────────

def _ensure_dict(value: Any, raw: str) -> Dict[str, Any]:
    """将非 dict JSON 值包装成 dict，以便调用者始终获得 dict。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"items": value}
    # 标量 (int, float, str, bool, None)
    return {"value": value}


def _validate_required_fields(
    data: Dict[str, Any],
    schema: Dict[str, Any],
    raw: str,
) -> None:
    """检查 schema['required'] 中列出的所有字段是否都存在于 data 中。"""
    required: List[str] = schema.get("required", [])
    missing = [field for field in required if field not in data]
    if missing:
        raise ProviderParseError(
            f"Structured output missing required fields: {missing}",
            raw_content=raw,
        )
