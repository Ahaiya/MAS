"""
Provider 抽象层，定义模型调用请求、响应与错误边界。

Provider 接口 — 能力边界定义。

本模块仅定义传输级别的抽象：
- LLMRequest  : provider 调用的结构化输入
- LLMResponse : provider 调用的结构化输出
- TokenUsage  : token 统计
- ProviderCapability : provider 声明的能力
- ProviderError hierarchy : 调用级别和解析级别的错误
- BaseProvider : 所有具体 provider 必须实现的抽象基类

BOUNDARY RULE (由 test_provider_interface.py 强制执行)：
  本模块必须保持不包含 policy、orchestrator 和 rubric 的导入。
  它只处理传输级别的问题：调用格式化、执行、解析。"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Optional


# ── 值对象 ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TokenUsage:
    """Provider 调用返回的 token 统计。"""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class LLMRequest:
    """
    与 Provider 无关的请求封装。
    
    Fields:
        prompt        : 主要用户/任务 prompt 文本（必填，非空）。
        system        : 单独传递的可选 system 指令。
        model_id      : 可选的 model 标识符覆盖。
        params        : Provider 特定的调用参数（temperature, max_tokens, …）。
        output_schema : 请求结构化输出的可选 JSON schema 字典。
        metadata      : 用于追踪/调试工具的可选非 provider 元数据。"""

    prompt: str
    system: Optional[str] = None
    model_id: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    output_schema: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.prompt:
            raise ValueError("LLMRequest.prompt must be a non-empty string")


@dataclass(frozen=True)
class LLMResponse:
    """
    与 Provider 无关的响应封装。
    
    Fields:
        content         : 来自模型的原始文本内容。
        structured_data : 解析后的结构化输出（当提供了 output_schema 且解析成功时设置；否则为 None）。
        usage           : Token 使用统计。
        provider_name   : 生成此响应的 provider 名称。
        model_id        : 此调用使用的 model 标识符。"""

    content: str
    structured_data: Optional[Dict[str, Any]]
    usage: TokenUsage
    provider_name: str
    model_id: str


# ── 能力枚举 ────────────────────────────────────────────────────────────────

class ProviderCapability(Enum):
    """Provider 可能声明的能力。"""

    TEXT_COMPLETION = auto()
    STRUCTURED_OUTPUT = auto()


# ── 错误层级 ────────────────────────────────────────────────────────────────

class ProviderError(Exception):
    """所有 provider 级别错误的基类。"""


class ProviderCallError(ProviderError):
    """当底层 API 调用失败时引发（network, auth, rate-limit, …）。"""
    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProviderParseError(ProviderError):
    """当 provider 响应无法解析为预期形状时引发。"""

    def __init__(self, message: str, *, raw_content: str = "") -> None:
        super().__init__(message)
        self.raw_content = raw_content


# ── 抽象基类 Provider ───────────────────────────────────────────────────────

class BaseProvider(abc.ABC):
    """
    所有 LLM provider 适配器的抽象基类。
    
    具体 provider 负责：
    - 将 LLMRequest 格式化为特定于 API 的 payload。
    - 执行 API 调用（内部管理重试/超时）。
    - 将 API 响应解析为 LLMResponse。
    - 失败时引发 ProviderCallError 或 ProviderParseError。
    
    具体 provider 绝不能：
    - 包含 rubric 语义（维度名称、分数范围、描述符逻辑）。
    - 包含 policy 逻辑（冲突解决、分数组合、引用规则）。
    - 执行状态机路由。"""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """此 provider 的唯一标识符（例如 "openai_compatible"）。"""

    @property
    @abc.abstractmethod
    def capabilities(self) -> frozenset[ProviderCapability]:
        """此 provider 支持的能力集合。"""

    @abc.abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse:
        """
        执行单次补全调用。
        
        Args:
            request: 包含 prompt、可选 system 消息、可选 model 覆盖、可选输出 schema 和 params 的 LLMRequest。
        
        Returns:
            包含 content、可选 structured_data 和 usage 信息的 LLMResponse。
        
        Raises:
            ProviderCallError: 如果 API 调用本身失败。
            ProviderParseError: 如果响应无法解析。"""
