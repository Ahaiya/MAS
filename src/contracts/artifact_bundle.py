"""
配置工件契约，定义 bundle 编译完成后对外暴露的冻结快照结构。

Artifact Bundle Contracts

定义配置工件 bundle 的 schema。
这是零硬编码的入口点——所有运行时配置都必须通过这些契约流转。

核心契约：
- ArtifactBundle: 已加载、未冻结的配置工件集合
- ResolvedArtifactBundle: 已冻结、可运行、所有引用都已解析的 bundle
- RubricSnapshot: 提取出的评分标准核心数据，便于快速访问
- PolicySnapshot: 提取出的裁决/聚合/解释策略

所有 bundle 操作都必须具备版本感知、可哈希验证和可重放安全。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List, Any
from enum import Enum


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
class ProviderConfig:
    """从 bundle 中加载的、按评分者和按阶段区分的 provider 配置。
    
        Attributes:
            default:         当评分者/阶段没有特定条目时使用的回退 provider。
            rater_providers: 将 rater_id（例如 "rater_1"）映射到其 ProviderEntryConfig。
            stage_providers: 将阶段名称（例如 "evidence_extraction"）映射到其配置。"""
    default: ProviderEntryConfig
    rater_providers: Dict[str, ProviderEntryConfig] = field(default_factory=dict)
    stage_providers: Dict[str, ProviderEntryConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class OperationalParams:
    """由 bundle 声明并由 pipeline 使用的运行时操作参数。"""

    max_retries: int = 2


class SchemaVersion(Enum):
    """配置工件 bundle 支持的 schema 版本。"""
    V2_0 = "2.0"


@dataclass(frozen=True)
class ArtifactRef:
    """配置工件文件的引用。
    
        Attributes:
            ref_uri: URI 风格的引用（例如，"rubric://asap_set8_writing_traits/v1"）
            source_file: 相对于 configs/ 目录的相对路径
            loaded_data: 已解析的 YAML/JSON 数据（加载前为 None）
            content_hash: 已加载内容的 SHA-256 哈希"""
    ref_uri: str
    source_file: str
    loaded_data: Optional[Dict[str, Any]] = None
    content_hash: Optional[str] = None


@dataclass(frozen=True)
class ArtifactBundle:
    """解析前配置工件的集合。
    
        这表示已加载但尚未冻结的状态。ConfigResolver
        将加载所有被引用的工件，计算哈希，并冻结为
        ResolvedArtifactBundle。
    
        Attributes:
            bundle_id: 该 bundle 的唯一标识符
            bundle_version: 版本字符串（语义化版本或时间戳）
            bundle_name: 人类可读的名称
            description: 该 bundle 所评估内容的描述
            schema_version: bundle 结构的 schema 版本
            rubric_ref: 评分标准核心工件的引用
            adjudication_policy_ref: 裁决策略工件的引用
            aggregation_policy_ref: 聚合策略工件的引用
            explanation_policy_ref: 解释策略工件的引用
            chunking_policy_ref: 分块策略工件的可选引用
            scoring_context_ref: 评分上下文工件的可选引用
            prompt_refs: 提示模板引用列表
            source_documents: 源文档文件列表
            freeze_hash: 所有工件内容的计算哈希（在解析期间设置）
            freeze_timestamp: 该 bundle 被冻结的时间
            validation_rules: 用于验证 bundle 闭合性的规则
            metadata: 额外的元数据标签"""
    bundle_id: str
    bundle_version: str
    bundle_name: str
    description: str
    schema_version: SchemaVersion

    # 工件引用
    rubric_ref: ArtifactRef
    adjudication_policy_ref: ArtifactRef
    aggregation_policy_ref: ArtifactRef
    prompt_refs: List[ArtifactRef]

    # 源文档
    source_documents: List[str]

    # 冻结元数据（在解析期间设置）
    freeze_hash: Optional[str] = None
    freeze_timestamp: Optional[datetime] = None

    # 解释策略引用（可选：简化 bundle 可省略）
    explanation_policy_ref: Optional[ArtifactRef] = None

    # 验证
    validation_rules: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # 从 bundle YAML 中解析出的原始 provider_config 字典（由编译器结构化）
    provider_config_raw: Optional[Dict[str, Any]] = None
    # 从 bundle YAML 中解析出的原始 operational_params 字典（由编译器结构化）
    operational_params_raw: Optional[Dict[str, Any]] = None
    # 从 bundle YAML 中解析出的可选分块策略引用
    chunking_policy_ref: Optional[ArtifactRef] = None
    # 从 bundle YAML 中解析出的可选评分上下文引用
    scoring_context_ref: Optional[ArtifactRef] = None

    def is_frozen(self) -> bool:
        """检查该 bundle 是否已被解析并冻结。"""
        return self.freeze_hash is not None

    def get_all_refs(self) -> List[ArtifactRef]:
        """获取该 bundle 中的所有工件引用。"""
        refs = [
            self.rubric_ref,
            self.adjudication_policy_ref,
            self.aggregation_policy_ref,
            *self.prompt_refs,
        ]
        if self.explanation_policy_ref is not None:
            refs.append(self.explanation_policy_ref)
        if self.chunking_policy_ref is not None:
            refs.append(self.chunking_policy_ref)
        if self.scoring_context_ref is not None:
            refs.append(self.scoring_context_ref)
        return refs


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

    def get_dimension_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        """按 code 获取维度（例如，'I'、'O'、'V'）。"""
        return self.dimension_by_code.get(code)

    def get_scale(self, scale_id: str) -> Optional[Dict[str, Any]]:
        """按 ID 获取等级。"""
        return self.scale_by_id.get(scale_id)

    def validate_score(self, dimension_id: str, score: int) -> bool:
        """验证给定维度的分数是否在等级范围内。"""
        dimension = self.get_dimension(dimension_id)
        if not dimension:
            return False
        scale_ref = dimension.get("scale_ref")
        if not scale_ref:
            return False
        scale = self.get_scale(scale_ref)
        if not scale:
            return False
        min_score = scale.get("min", 1)
        max_score = scale.get("max", 6)
        return min_score <= score <= max_score


@dataclass(frozen=True)
class PolicySnapshot:
    """提取出的裁决、聚合和解释策略。
    
        该快照包含一致性检查器、裁决器和反馈组装器
        所需的策略数据。
    
        Attributes:
            adjudication_policy: 已解析的裁决策略规则
            aggregation_policy: 已解析的聚合策略公式
            explanation_policy: 已解析的解释策略要求
            chunking_policy: 可选的分块/覆盖范围收缩策略
            policy_version: 策略快照的合并版本字符串"""
    adjudication_policy: Dict[str, Any]
    aggregation_policy: Dict[str, Any]
    explanation_policy: Dict[str, Any]
    policy_version: str
    chunking_policy: Dict[str, Any] = field(default_factory=dict)
    scoring_context: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedArtifactBundle:
    """已冻结、可运行、所有引用都已解析的 bundle。
    
        这是 ConfigResolver 的输出，也是 orchestrator
        在每次评估开始时接收的内容。它包含已解析的快照，便于快速访问，
        并保证版本闭合性。
    
        Attributes:
            artifact_bundle: 原始的 ArtifactBundle（现已冻结）
            rubric_snapshot: 提取出的评分标准核心数据
            policy_snapshot: 提取出的裁决/聚合/解释策略
            prompt_templates: 已加载的提示模板
            resolved_at: 该 bundle 被解析的时间
            resolver_version: 创建该快照的解析器版本
            total_hash: 所有已解析工件的合并哈希"""
    artifact_bundle: ArtifactBundle
    rubric_snapshot: RubricSnapshot
    policy_snapshot: PolicySnapshot
    prompt_templates: Dict[str, str]

    # 解析元数据
    resolved_at: datetime
    resolver_version: str
    total_hash: str

    # 结构化 provider 配置（如果 bundle 未声明则为 None）
    provider_config: Optional[ProviderConfig] = None
    # 结构化 operational params（如果 bundle 未声明则为 None）
    operational_params: Optional[OperationalParams] = None

    def get_version_info(self) -> str:
        """获取用于日志记录的格式化版本信息。"""
        return (
            f"{self.artifact_bundle.bundle_id}@{self.artifact_bundle.bundle_version} "
            f"| rubric:{self.rubric_snapshot.rubric_version} "
            f"| policy:{self.policy_snapshot.policy_version}"
        )

    def get_frozen_config_summary(self) -> Dict[str, str]:
        """获取跟踪元数据冻结配置的摘要。"""
        return {
            "bundle_id": self.artifact_bundle.bundle_id,
            "bundle_version": self.artifact_bundle.bundle_version,
            "rubric_id": self.rubric_snapshot.rubric_id,
            "rubric_version": self.rubric_snapshot.rubric_version,
            "policy_version": self.policy_snapshot.policy_version,
            "resolved_at": self.resolved_at.isoformat(),
            "total_hash": self.total_hash,
        }


# 用于创建空 bundle 引用的工厂函数。
def create_artifact_ref(
    ref_uri: str,
    source_file: str
) -> ArtifactRef:
    """创建包含未加载数据的 ArtifactRef。"""
    return ArtifactRef(
        ref_uri=ref_uri,
        source_file=source_file,
        loaded_data=None,
        content_hash=None
    )
