"""契约层入口，集中定义流水线阶段之间传递的 typed contract。

阶段之间传递的所有数据都必须用此处定义的类型。

各模块（一律从具体模块导入，本包只再导出配置快照三件套）：
- `package`: DataPackage 与带全局连续编号的 Unit（证据引用的锚点）
- `artifact_bundle`: RubricSnapshot / PolicySnapshot / ProviderEntryConfig
- `score_representation`: 规范化分数与显示注解分离
- `scoring`: DimensionScore / RaterChainResult / FinalDecision
- `trace`: StageTrace / RunTraceSummary（只记成本与性能）"""

__version__ = "0.1.0"

from .artifact_bundle import PolicySnapshot, ProviderEntryConfig, RubricSnapshot

__all__ = [
    "RubricSnapshot",
    "PolicySnapshot",
    "ProviderEntryConfig",
    "__version__",
]
