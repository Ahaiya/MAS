"""契约层入口，集中定义流水线阶段之间传递的 typed contract。

阶段之间传递的所有数据都必须用此处定义的类型。

各模块（一律从具体模块导入，本包不做再导出）：
- `package`: DataPackage 与带全局连续编号的 Unit（证据引用的锚点）
- `configuration`: RubricSnapshot / PolicySnapshot / ProviderEntryConfig
- `scoring`: DimensionScore / RaterChainResult / FinalDecision
- `trace`: StageTrace / RunTraceSummary（只记成本与性能）
"""
