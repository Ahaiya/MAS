"""
Provider 注册表，负责维护 provider 名称到实现类的映射关系。

Provider adapter registry.

Provides a simple name → class mapping so the pipeline can select a provider
by name (e.g. "--provider mock", "--provider openai") without hard-coding any
specific provider in orchestrator or agent code.

BOUNDARY RULE (enforced by test_provider_interface.py):
  This module must remain free of policy, orchestrator, and rubric imports.
  It handles only provider class registration and instantiation.
"""

from __future__ import annotations

from typing import Dict, Type

from src.providers.base import BaseProvider


class ProviderRegistry:
    """
    Registry mapping provider names to their concrete classes.

    Usage::

        registry = ProviderRegistry()
        registry.register("mock", MockProvider)
        provider = registry.get("mock")
        response = provider.complete(request)
    """

    def __init__(self) -> None:
        self._store: Dict[str, Type[BaseProvider]] = {}

    def register(self, name: str, provider_class: type) -> None:
        """
        Register a provider class under the given name.

        Args:
            name           : Lookup key (e.g. "mock", "openai").
            provider_class : A class that is a subclass of BaseProvider.

        Raises:
            TypeError: If provider_class is not a subclass of BaseProvider.
        """
        if not (isinstance(provider_class, type) and issubclass(provider_class, BaseProvider)):
            raise TypeError(
                f"provider_class must be a subclass of BaseProvider, got {provider_class!r}"
            )
        self._store[name] = provider_class

    def get(self, name: str, **kwargs: object) -> BaseProvider:
        """
        Instantiate and return the provider registered under name.

        Args:
            name   : Registered provider name.
            **kwargs: Forwarded to the provider class constructor.

        Returns:
            A fresh BaseProvider instance.

        Raises:
            KeyError: If no provider is registered under name.
        """
        if name not in self._store:
            raise KeyError(f"No provider registered under '{name}'. "
                           f"Available: {sorted(self._store)}")
        return self._store[name](**kwargs)

    def list_names(self) -> list[str]:
        """Return sorted list of registered provider names."""
        return sorted(self._store)


# ── Module-level default registry ─────────────────────────────────────────────

_default_registry = ProviderRegistry()


def get_registry() -> ProviderRegistry:
    """Return the module-level default ProviderRegistry instance."""
    return _default_registry
