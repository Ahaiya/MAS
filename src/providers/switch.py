"""
Provider 选择器，负责按名称或环境变量创建可用的 provider 实例。

Provider switch — factory functions for selecting providers by name.

`create_provider(name)` instantiates a provider from the default registry.
`create_provider_from_env()` reads the LLM_PROVIDER environment variable
(defaulting to "openai_compatible") and resolves a real provider.
"""

from __future__ import annotations

import os

from src.contracts.artifact_bundle import ProviderEntryConfig
from src.providers.base import BaseProvider
from src.providers.factory import build_provider
from src.providers.registry import get_registry


_OPENAI_COMPATIBLE_ENV_ALIASES = frozenset({
    "openai",
    "openai_compatible",
    "deepseek",
    "qwen",
})


def create_provider(name: str, **kwargs: object) -> BaseProvider:
    """
    Instantiate the provider registered under name in the default registry.

    Args:
        name  : Registered provider name (e.g. "openai_compatible").
        kwargs: Forwarded to the provider class constructor.

    Returns:
        A fresh BaseProvider instance.

    Raises:
        KeyError: If no provider is registered under name.
    """
    return get_registry().get(name, **kwargs)


def create_provider_from_env(**kwargs: object) -> BaseProvider:
    """
    Instantiate a provider using the LLM_PROVIDER environment variable.

    Defaults to "openai_compatible" when LLM_PROVIDER is not set.

    Args:
        kwargs: Forwarded to the provider class constructor.

    Returns:
        A fresh BaseProvider instance.

    For common OpenAI-compatible provider names declared in env
    (for example `deepseek` or `openai`), this function builds a provider
    directly from the global `LLM_*` environment variables.

    Raises:
        KeyError: If LLM_PROVIDER names an unsupported provider.
    """
    name = os.environ.get("LLM_PROVIDER", "openai_compatible").strip() or "openai_compatible"

    if name in _OPENAI_COMPATIBLE_ENV_ALIASES:
        entry = ProviderEntryConfig(
            api_key_env="LLM_API_KEY",
            model=os.environ.get("LLM_MODEL", ""),
            api_base=os.environ.get("LLM_API_BASE", ""),
        )
        return build_provider(entry)

    registry = get_registry()
    if name in registry.list_names():
        return registry.get(name, **kwargs)

    raise KeyError(
        f"No provider registered under '{name}'. "
        f"Available: {sorted(registry.list_names())}"
    )
