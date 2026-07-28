"""
Provider 工厂：按 ProviderEntryConfig 装配真实 provider 及其重试/超时包装层。

职责边界是死的：**model / api_base / params 只来自 yaml，密钥值只来自 env**。
没有任何兜底——yaml 里 model 或 api_base 缺失即报错，密钥变量没值即报错。
这些兜底曾经存在（LLM_MODEL / LLM_API_BASE / LLM_API_KEY 以及硬编码的
"gpt-4o-mini"），删掉的原因是它们会静默生效：
- 拿 A 厂商的 key 去连 B 厂商，换来一句难以归因的 401；
- yaml 漏填 model 时静默跑一个本项目根本不用的模型，产物上看不出异常。

超时与重试由调用方以 RetryConfig 传入（来自 model_config.yaml 的 runtime 段），
本模块不再自己读环境变量。

BOUNDARY RULE：本模块只做 ProviderEntryConfig → BaseProvider 的桥接，不得导入
rubric 或 policy。"""

from __future__ import annotations

import os
from typing import Any, Dict

from src.contracts.artifact_bundle import ProviderEntryConfig
from src.providers.base import BaseProvider
from src.providers.guards import GuardedProvider, RetryConfig
from src.providers.openai_compatible import OpenAICompatibleProvider


def build_provider(entry: ProviderEntryConfig, retry_config: RetryConfig) -> GuardedProvider:
    """从 ProviderEntryConfig 与密钥环境变量实例化一个 GuardedProvider。

        Args:
            entry: provider 条目（model / api_base / api_key_env / params）。
            retry_config: 重试与超时配置，来自 model_config.yaml 的 runtime 段。

        Returns:
            包装 OpenAICompatibleProvider 的 GuardedProvider。

        Raises:
            ValueError: model / api_base 未在 yaml 中给出，或密钥环境变量没有值。"""
    if not entry.model:
        raise ValueError(
            f"provider 缺少 model：请在 model_config.yaml 里显式写明模型名"
            f"（该条目的 api_key_env 是 '{entry.api_key_env}'）。"
        )
    if not entry.api_base:
        raise ValueError(
            f"provider 缺少 api_base：请在 model_config.yaml 里显式写明接口地址"
            f"（该条目的 api_key_env 是 '{entry.api_key_env}'）。"
        )

    api_key = os.environ.get(entry.api_key_env, "")
    if not api_key:
        raise ValueError(
            f"环境变量 '{entry.api_key_env}' 没有值。请在 .env 里设置它——"
            f"密钥不再回退到 LLM_API_KEY，以免把某个厂商的 key 发给另一个厂商。"
        )

    # RetryConfig.timeout_seconds 为 None 表示"不做挂钟超时强制"（GuardedProvider
    # 那一层的语义）。HTTP 客户端自身仍需要一个数字，此时交给 provider 用它的默认值。
    inner_kwargs: Dict[str, Any] = {}
    if retry_config.timeout_seconds is not None:
        inner_kwargs["timeout"] = retry_config.timeout_seconds

    inner = OpenAICompatibleProvider(
        api_key=api_key,
        model_id=entry.model,
        api_base=entry.api_base,
        default_params=entry.params,
        **inner_kwargs,
    )
    return GuardedProvider(inner, retry_config)
