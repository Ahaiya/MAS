"""
Provider 工厂，负责按配置装配真实 provider 及其包装层。

Provider Factory — builds BaseProvider instances from ProviderEntryConfig.

Reads model, api_base, and API key from the config entry plus environment
variables.  All secret values stay in env; the bundle only stores the *name*
of the env var (api_key_env).

Fallback resolution order for model / api_base:
  1. Value set directly in ProviderEntryConfig (non-empty string)
  2. Corresponding global env var (LLM_MODEL / LLM_API_BASE)
  3. Hardcoded safe default ("gpt-4o-mini" / None)

BOUNDARY RULE: this module must not import rubric, policy, or orchestrator
modules.  It only bridges ProviderEntryConfig → BaseProvider.
"""

from __future__ import annotations

import os

from src.contracts.artifact_bundle import ProviderConfig, ProviderEntryConfig
from src.providers.base import BaseProvider
from src.providers.guards import GuardedProvider, RetryConfig
from src.providers.openai_compatible import OpenAICompatibleProvider


def build_provider(entry: ProviderEntryConfig) -> BaseProvider:
    """Instantiate a GuardedProvider from a ProviderEntryConfig + env vars.

    Args:
        entry: A ProviderEntryConfig (model, api_base, api_key_env).

    Returns:
        A ready-to-use BaseProvider (GuardedProvider wrapping OpenAICompatibleProvider).

    Raises:
        ValueError: If the resolved API key is empty.
    """
    api_key = os.environ.get(entry.api_key_env, "") or os.environ.get("LLM_API_KEY", "")
    if not api_key:
        raise ValueError(
            f"No API key found: '{entry.api_key_env}' is empty and LLM_API_KEY is also not set. "
            f"Set either '{entry.api_key_env}' or LLM_API_KEY in your .env file."
        )

    model = entry.model or os.environ.get("LLM_MODEL", "gpt-4o-mini")
    api_base_raw = entry.api_base or os.environ.get("LLM_API_BASE", "")
    api_base = api_base_raw or None  # OpenAICompatibleProvider expects None for default

    timeout = float(os.environ.get("LLM_TIMEOUT_SECONDS", "60"))
    max_retries = int(os.environ.get("LLM_MAX_RETRIES", "3"))
    retry_delay = float(os.environ.get("LLM_RETRY_DELAY_SECONDS", "1"))

    inner = OpenAICompatibleProvider(
        api_key=api_key,
        model_id=model,
        api_base=api_base,
        default_params=entry.params,
        timeout=timeout,
        max_retries=0,  # retries handled by GuardedProvider
    )
    return GuardedProvider(inner, RetryConfig(max_retries=max_retries, retry_delay_seconds=retry_delay))


def build_provider_map(provider_config: ProviderConfig) -> tuple[
    BaseProvider,
    dict[str, BaseProvider],
    dict[str, BaseProvider],
]:
    """Build all providers declared in a ProviderConfig.

    Returns:
        (default_provider, rater_providers, stage_providers)
        where rater_providers maps rater_id → BaseProvider
        and stage_providers maps stage_name → BaseProvider.
    """
    default = build_provider(provider_config.default)
    rater_providers = {
        rater_id: build_provider(entry)
        for rater_id, entry in provider_config.rater_providers.items()
    }
    stage_providers = {
        stage: build_provider(entry)
        for stage, entry in provider_config.stage_providers.items()
    }
    return default, rater_providers, stage_providers
