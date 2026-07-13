"""契约层入口，集中定义流水线阶段之间传递的 typed contract。

MAS 评估引擎 - 契约包

本包包含多智能体系统使用的所有数据契约。
在 agents/nodes 之间传递的所有数据必须使用此处定义的类型。

核心契约：
- artifact_bundle: 用于配置解析的 ArtifactBundle 和 ResolvedArtifactBundle
- score_representation: 规范化分数与显示注解分离
- request_models: 请求规范化与文本分段
- evidence: 证据跨度与维度观测
- scoring: 分数假设、冲突、裁决与最终决策
- trace: 运行追踪与重放元数据"""

__version__ = "0.1.0"

from .artifact_bundle import (
    SchemaVersion,
    ArtifactRef,
    ArtifactBundle,
    RubricSnapshot,
    PolicySnapshot,
    ResolvedArtifactBundle,
    create_artifact_ref,
)

__all__ = [
    "SchemaVersion",
    "ArtifactRef",
    "ArtifactBundle",
    "RubricSnapshot",
    "PolicySnapshot",
    "ResolvedArtifactBundle",
    "create_artifact_ref",
    "__version__",
]
