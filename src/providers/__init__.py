"""
Provider 子系统入口，负责预注册内置 provider 并暴露统一装配入口。

src.providers package — pre-registers built-in providers in the default registry.

Importing this package ensures that "mock" is always available via get_registry().
Real providers (e.g. "openai_compatible") must be registered explicitly by the
caller after configuring their credentials.
"""

from src.providers.mock import MockProvider
from src.providers.registry import get_registry

# Pre-register the deterministic mock provider
get_registry().register("mock", MockProvider)
