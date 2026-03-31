"""
配置冻结工具，负责计算配置内容哈希并形成可追踪的版本闭包。

Config Freeze: Content hashing utilities for version closure.

Provides deterministic, order-independent hash computation for:
- Individual artifact file content (SHA-256)
- Combined bundle hash (sorted, then SHA-256)

These hashes enable replay safety: given identical config files,
the system always produces the same frozen bundle hash.
"""

import hashlib
from typing import Sequence


def compute_content_hash(content: str) -> str:
    """Compute SHA-256 hash of a file content string.

    Args:
        content: The raw text content to hash.

    Returns:
        64-character lowercase hex string (SHA-256 digest).
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_bundle_hash(content_hashes: Sequence[str]) -> str:
    """Compute a deterministic, order-independent combined hash.

    Sorts input hashes before combining so that the bundle hash
    is stable regardless of artifact loading order.

    Args:
        content_hashes: Sequence of individual artifact content hashes.

    Returns:
        64-character lowercase hex string (SHA-256 digest).
    """
    sorted_hashes = sorted(content_hashes)
    combined = "\n".join(sorted_hashes)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
