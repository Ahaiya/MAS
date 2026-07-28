# 03 — 单 Rater 完整链（select→extract→score）+ FakeProvider 接缝

**What to build:** 一个 Rater 对一个二级指标独立完成「选段 → 取证 → 评分」完整链路，产出 `RaterChainResult`。通过新建的 `FakeProvider`（按调用顺序返回预设 `LLMResponse`）可确定性地跑完整条链并断言产出。这是整个测试策略的主接缝，建在 `BaseProvider` 最高点。合并旧 extractor+observer+scorer（旧文件本票不删，留到 09）。

**Blocked by:** 01（scoring 契约）、02（DataPackage 单元）

**Status:** ready-for-agent

- [ ] 新建 `FakeProvider`（实现 `BaseProvider`，按调用顺序吐脚本化 `LLMResponse`）——当前代码库无任何 fake/stub，这是唯一新增测试接缝
- [ ] 新增 `src/agents/rater.py`：`select`（看「单元号 + 每段前若干字节选」→ 选出该二级指标相关单元号，每 Rater 独立）→ `extract`（选中单元全文 → 证据，返回 `unit_ids`）→ `score`（证据 + 锚点 → `DimensionScore`）
- [ ] 取证与评分是两次独立 LLM 调用（证据先于分数生成）
- [ ] 模型引用证据只能返回已存在的单元编号；越界编号被校验拒绝（消除自由复述 + 模糊匹配）
- [ ] 一个 Rater 三趟共用同一个 provider（`raters.rater_N`），不拆分
- [ ] 新增 prompt 模板：`select.yaml`、`extraction.yaml`（按 unit_ids）、`scoring.yaml`
- [ ] 测试（注入 FakeProvider）：给定 DataPackage + 量规 + 脚本化响应，链产出正确的 `RaterChainResult`（选段号、证据 unit_ids 回指原文、分数）；越界 unit_id 被拒
