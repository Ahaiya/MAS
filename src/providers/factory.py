"""
Provider 工厂，负责按配置装配真实 provider 及其包装层。

Provider Factory — 根据 ProviderEntryConfig 构建 BaseProvider 实例。

从配置条目及环境变量中读取 model、api_base 和 API key。所有密钥值保留在 env 中；bundle 仅存储环境变量的 *名称* (api_key_env)。

model / api_base 的回退解析顺序：
  1. 直接在 ProviderEntryConfig 中设置的值（非空字符串）
  2. 对应的全局环境变量 (LLM_MODEL / LLM_API_BASE)
  3. 硬编码的安全默认值 ("gpt-4o-mini" / None)

BOUNDARY RULE：此模块不得导入 rubric、policy 或 orchestrator 模块。它仅桥接 ProviderEntryConfig → BaseProvider。"""

from __future__ import annotations

import os

from src.contracts.artifact_bundle import ProviderConfig, ProviderEntryConfig
from src.providers.base import BaseProvider
from src.providers.guards import GuardedProvider, RetryConfig
from src.providers.openai_compatible import OpenAICompatibleProvider


def build_provider(entry: ProviderEntryConfig) -> BaseProvider:
    """从 ProviderEntryConfig 和环境变量实例化一个 GuardedProvider。
    
        Args:
            entry: 一个 ProviderEntryConfig (model, api_base, api_key_env)。
    
        Returns:
            一个可直接使用的 BaseProvider (包装 OpenAICompatibleProvider 的 GuardedProvider)。
    
        Raises:
            ValueError: 如果解析出的 API key 为空。"""
    api_key = os.environ.get(entry.api_key_env, "") or os.environ.get("LLM_API_KEY", "")
    if not api_key:
        raise ValueError(
            f"No API key found: '{entry.api_key_env}' is empty and LLM_API_KEY is also not set. "
            f"Set either '{entry.api_key_env}' or LLM_API_KEY in your .env file."
        )

    model = entry.model or os.environ.get("LLM_MODEL", "gpt-4o-mini")
    api_base_raw = entry.api_base or os.environ.get("LLM_API_BASE", "")
    api_base = api_base_raw or None  # OpenAICompatibleProvider 默认期望 None

    timeout = float(os.environ.get("LLM_TIMEOUT_SECONDS", "60"))
    max_retries = int(os.environ.get("LLM_MAX_RETRIES", "3"))
    retry_delay = float(os.environ.get("LLM_RETRY_DELAY_SECONDS", "1"))

    inner = OpenAICompatibleProvider(
        api_key=api_key,
        model_id=model,
        api_base=api_base,
        default_params=entry.params,
        timeout=timeout,
    )
    return GuardedProvider(inner, RetryConfig(max_retries=max_retries, retry_delay_seconds=retry_delay))


def build_provider_map(provider_config: ProviderConfig) -> tuple[
    BaseProvider,
    dict[str, BaseProvider],
    dict[str, BaseProvider],
]:
    """构建在 ProviderConfig 中声明的所有 providers。
    
        Returns:
            (default_provider, rater_providers, stage_providers)
            其中 rater_providers 映射 rater_id → BaseProvider
            而 stage_providers 映射 stage_name → BaseProvider。"""
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
