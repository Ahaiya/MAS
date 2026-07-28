# 01 — v2 数据契约基础层（DataPackage / Unit / scoring v2）

**What to build:** 定型本轮重构所有新数据契约，作为 segment 与评价链共同依赖的基础层。纯 dataclass，无逻辑、无 LLM、无 IO——建完即可被后续所有票 import。新契约与旧契约**并存**（旧契约留到 09 才删），保证中途编译不断。

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [ ] 新增 `DataPackage` 与 `Unit`（frozen dataclass）。Unit 字段：`id:int`（全局连续编号）、`kind:str`（prose|code|table_row|heading|image）、`text:str`、`source_file:str`、`char_range:tuple[int,int]`、`speaker:str|None`。DataPackage 字段：`package_id:str`、`units:list[Unit]`、`metadata:dict`（前端透传，引擎不解释）
- [ ] `scoring.py` 新增 `DimensionScore`（`dimension_id/score/supporting_unit_ids/rationale/confidence`）与 `RaterChainResult`（单 Rater 完整链：选段 + 证据 + 分数 + rationale 绑定同一结构，证据引用为 `unit_ids` 编号）
- [ ] 新增/调整最终决策契约以承载 `source: consensus|adjudicated` 标记与被引用的 `unit_ids`
- [ ] `trace.py` 新增轻量 trace 契约：运行级（run_id/bundle_ref/dim/total_tokens/total_ms/adjudicated_dims）+ 阶段级（stage/rater/llm_calls/tokens/ms）
- [ ] 旧契约（CoveragePlan/DimensionObservation/FacetFinding/ObservationConfidence 及旧 scoring 类型）保持不动，与新契约并存
- [ ] 新契约有构造/不可变性的最小单元测试
