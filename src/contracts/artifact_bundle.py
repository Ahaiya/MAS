"""
配置快照契约：编译期确定、运行期只读的量规 / 策略 / provider 配置。

  RubricSnapshot      —— 一个一级指标的完整量规（二级指标 + 量表 + 锚点）
  PolicySnapshot      —— 仲裁触发阈值等策略配置
  ProviderEntryConfig —— 单个 provider 的 model / api_base / api_key_env / params

设计不变式：
- 全部为冻结（不可变）的 dataclass，编译后不再改动。
- 只存环境变量的**名称**（api_key_env），密钥值只从 .env 读，绝不进配置对象。

v1 的 ArtifactBundle / ResolvedArtifactBundle / ArtifactRef / OperationalParams
（工件引用 + 冻结哈希 + 回放元数据）已随 ConfigCompiler 一并删除。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ProviderEntryConfig:
    """单个 LLM provider 端点的配置。
    
        Attributes:
            api_key_env: 存放 API key 的环境变量名称。
            model:       模型标识符（例如 "deepseek-chat"）。空字符串表示
                         在构建时从环境变量读取 LLM_MODEL。
            api_base:    API 基础 URL。空字符串表示从环境变量读取 LLM_API_BASE。
            params:      可选的 provider 默认请求参数，会合并到每次调用中
                         （例如 temperature、max_tokens，或 provider 特定的
                         extra_body 设置，如 reasoning controls）。"""
    api_key_env: str
    model: str = ""
    api_base: str = ""
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RubricSnapshot:
    """提取出的评分标准核心数据，用于运行时快速访问。
    
        该快照仅包含 agents 所需的评分标准核心数据，
        避免在评估期间重复解析 YAML。
    
        Attributes:
            rubric_id: 该评分标准的唯一标识符
            rubric_version: 版本字符串
            rubric_name: 人类可读的名称
            dimensions: 维度定义列表
            scales: 可用分数等级列表
            dimension_by_id: dimension_id 到维度定义的映射
            dimension_by_code: dimension_code 到维度定义的映射
            scale_by_id: scale_id 到等级定义的映射"""
    rubric_id: str
    rubric_version: str
    rubric_name: str
    dimensions: List[Dict[str, Any]]
    scales: List[Dict[str, Any]]
    indicator_description: str = ""
    raw_task_rubric: Dict[str, Any] = field(default_factory=dict)

    # 为快速访问计算的查找映射
    dimension_by_id: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    dimension_by_code: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    scale_by_id: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def get_dimension(self, dimension_id: str) -> Optional[Dict[str, Any]]:
        """按 ID 获取维度。"""
        return self.dimension_by_id.get(dimension_id)

@dataclass(frozen=True)
class PolicySnapshot:
    """从 policy 配置提取出的、运行期只读的策略快照。

        Attributes:
            adjudication_policy: 已解析的仲裁触发规则（needs_adjudication 读它）
            policy_version: 策略快照的版本字符串"""
    adjudication_policy: Dict[str, Any]
    policy_version: str
