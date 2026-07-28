"""
Provider 工厂：按 ProviderEntryConfig 装配真实 provider 及其重试/超时包装层。

model / api_base 从配置条目读，读不到则退到全局环境变量，再退到安全默认值。
密钥值只从 env 读——配置里存的永远只是环境变量的**名字**（api_key_env）。

BOUNDARY RULE：本模块只做 ProviderEntryConfig → BaseProvider 的桥接，不得导入
rubric 或 policy。"""

from __future__ import annotations

import os

from src.contracts.artifact_bundle import ProviderEntryConfig
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
