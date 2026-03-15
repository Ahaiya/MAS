"""
Provider switch — factory functions for selecting providers by name.

`create_provider(name)` instantiates a provider from the default registry.
`create_provider_from_env()` reads the LLM_PROVIDER environment variable
(defaulting to "mock") and delegates to `create_provider`.

The default registry is pre-populated with "mock" in src/providers/__init__.py.
"""

from __future__ import annotations

import os

from src.providers.base import BaseProvider
from src.providers.registry import get_registry


def create_provider(name: str, **kwargs: object) -> BaseProvider:
    """
    Instantiate the provider registered under name in the default registry.

    Args:
        name  : Registered provider name (e.g. "mock", "openai_compatible").
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

    Defaults to "mock" when LLM_PROVIDER is not set.

    Args:
        kwargs: Forwarded to the provider class constructor.

    Returns:
        A fresh BaseProvider instance.

    Raises:
        KeyError: If LLM_PROVIDER names an unregistered provider.
    """
    name = os.environ.get("LLM_PROVIDER", "mock")
    return create_provider(name, **kwargs)
