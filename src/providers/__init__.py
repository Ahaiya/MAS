"""
Provider 子系统入口。

src.providers package exports the shared ProviderRegistry accessor.
"""

from src.providers.registry import get_registry

__all__ = ["get_registry"]
