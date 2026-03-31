# 20717 内环 Code Review 报告

## Findings

### P1: `scoring` 阶段没有任何输出预算约束，真实运行中已出现明显的 completion 膨胀和慢尾

- 代码位置：
  - `src/agents/scorer.py:76-90`
  - `src/providers/openai_compatible.py:97-104`
- 运行证据：
  - 本次真实 run 共 31 次 LLM 调用，`85,323` tokens，LLM 总耗时 `846.1s`。
  - 其中 `rater_2(qwen3.5-plus)` 单独消耗 `40,820` tokens / `519.8s`。
  - 最慢的 5 次调用全部来自 `rater_2` 评分：
    - `voice`: `8666 tok / 136.7s`
    - `word_choice`: `8121 tok / 111.8s`
    - `conventions`: `6370 tok / 82.7s`
    - `sentence_fluency`: `7015 tok / 77.6s`
    - `ideas_content`: `6111 tok / 64.0s`
- 代码判断：
  - `scorer.run()` 构造 `LLMRequest` 时只传了 `prompt`、`output_schema` 和 `metadata`，没有传 `max_tokens`、`temperature` 或其他预算参数。
  - `OpenAICompatibleProvider.complete()` 仅在 `request.params` 非空时透传 provider 参数，因此当前 scoring 请求实际上没有任何 completion 上限。
  - 当前 prompt 只靠“Keep justification concise”这类软约束控制输出长度；对 `qwen3.5-plus` 这类更容易展开 justification 的模型，这个约束明显不够。
- 影响：
  - 评分阶段成为当前真实链路的绝对瓶颈，`node_scorer` 单节点耗时达到 `9分54秒`。
  - 外环一旦做多样本批跑，成本、时延和 rate-limit 风险都会被这一点放大。
  - 该问题还会污染调试信号，因为“模型推理质量”与“输出过长”混在一起，难以直接判断是否真的需要这么长的 justification。
- 建议：
  - 为 `scoring` 和 `feedback` 分 stage 设置硬性 `max_tokens` 上限。
  - 若要沿用 `LLM_MAX_TOKENS`，应明确把 env/bundle 配置映射到 `LLMRequest.params`，而不是只在 `.env` 中声明。
  - 外环首轮建议优先统计“每节点 completion tokens / elapsed_ms / parse success”三项，再决定是否保留 `qwen3.5-plus` 作为 `rater_2`。

### P2: `LoggingProvider` 在真实运行中丢失 call-level `model_id`，直接削弱中间日志和 debug bundle 的可观测性

- 代码位置：
  - `src/providers/logging_provider.py:110-133`
- 运行证据：
  - 本次终端中所有单次调用日志都显示模型名为 `?`。
  - `artifacts/inner_loop_real_eval_20260330_deepseek_all/20717/_debug/run-57e4d1af04bd/llm_calls/call-0001.json` 等 call 记录的 `model_id` 也是 `?`。
  - 但同一个 run 的 `manifest.json` 里 provider 绑定是正确的，说明问题不在 provider 构建，而在 call-level 记录路径。
- 代码判断：
  - `LoggingProvider.complete()` 用 `getattr(self._inner, "_model_id", "?")` 直接从一层 wrapper 上取模型名。
  - 当前真实 provider 是 `LoggingProvider -> GuardedProvider -> OpenAICompatibleProvider` 的嵌套结构，`GuardedProvider` 本身没有 `_model_id`，所以日志和 debug bundle 从这里开始就被写成了 `?`。
  - 同一个文件下 `model_id` property 已经实现了穿透查找，但 `complete()` 并没有复用这条逻辑。
- 影响：
  - 用户最想看的“当前到底是哪一个模型在慢、在抖、在输出超长 completion”无法直接从流式终端或 viewer 读出来。
  - 对外环来说，这会直接削弱 provider-routing 实验的可解释性。
- 建议：
  - `complete()` 内应复用现有 `self.model_id` 解析逻辑，避免一层 wrapper 导致信息丢失。
  - 修完后补一个针对 `LoggingProvider(GuardedProvider(OpenAICompatibleProvider))` 的回归测试。

### P3: 当前 adjudication policy 只处理“非相邻分差”与 cusp rule，对连续的边界漂移没有升级机制

- 代码位置：
  - `configs/policies/adjudication/asap_set8_default.yaml:21-46`
- 运行证据：
  - 本次双评审结果：
    - `organization`: `4 vs 5`
    - `word_choice`: `4 vs 5`
    - `conventions`: `2 vs 3`
  - `node_consistency_checker` 最终仍然输出 `conflicts:0`，没有进入 `rater_3` 路径。
  - 最终 composite 为 `35/60`，而人类均值为 `47/60`，差值达到 `-12`。
- 代码判断：
  - 当前 policy 只在 `|r1-r2| > 1` 时触发 resolution，或满足 Set 8 cusp rule 时触发。
  - 因此像本次这种“多个 trait 都是相邻分差、但整体方向一致地更保守”的情况，会被当作正常收敛直接放行。
  - 这不是实现 bug，而是当前 policy 对边界漂移的覆盖不足。
- 影响：
  - 内环真实主路径虽然通了，但外环如果以“更贴近人工评分”作为优化目标，这条 policy 会成为 recall 盲区。
  - 该盲区尤其容易出现在混合模型评审场景，因为不同模型对 4/5、2/3 这类边界的习惯并不一致。
- 建议：
  - 外环不要只看 `conflicts:0/1`，还应记录“adjacent disagreement count”和“same-direction drift”。
  - 可考虑在实验分支里增加一个非生产 trigger，例如“同篇出现 >=N 个相邻分差时升级 resolution”或“低 confidence + 相邻分差时升级 resolution”，先离线评估收益。

## Run Facts

- 运行日期：`2026-03-30`
- 样本：`essay_id=20717`
- 命令：
  - `python scripts/eval.py --essay-id 20717 --debug-bundle --output-dir artifacts/inner_loop_real_eval_20260330_deepseek_all --verbose`
- 运行 ID：`run-57e4d1af04bd`
- 本次 provider 路由：
  - `default`: `deepseek-chat`
  - `rater_1`: `deepseek-chat`
  - `rater_2`: `qwen3.5-plus`
  - `rater_3`: `deepseek-chat`
  - `chunking`: `deepseek-chat`
  - `coverage_planning`: `deepseek-chat`
  - `evidence_extraction`: `deepseek-chat`
  - `feedback`: `deepseek-chat`
- 为满足本轮要求，已将 `chunking` 与 `coverage_planning` 调整为回落到默认 provider：
  - `configs/bundles/asap_set8_baseline.bundle.yaml:86-94`

## Pipeline Outcome

- 总耗时：`13分49秒`
- 节点结果：
  - `node_preprocess`: `chunks:6 (llm_semantic)`，`31s`
  - `node_coverage`: `coverage:36->26`，`4s`
  - `node_extractor`: `69 spans`，`2分43秒`
  - `node_observer`: `6 observations`
  - `node_scorer`: `12 hypotheses`，`9分54秒`
  - `node_consistency_checker`: `conflicts:0`
  - `node_feedback`: `6 dims`，`36s`
- 最终 trait 分数：
  - `ideas_content=5`
  - `organization=4`
  - `voice=5`
  - `word_choice=4`
  - `sentence_fluency=3`
  - `conventions=2`
- Composite：
  - MAS: `35/60`
  - Human mean: `47/60`

## Validated Positives

### Stage M 的 explanation prompt 增强项已在真实链路中生效

- 代码位置：
  - `configs/prompts/explanation.yaml:24-76`
  - `src/agents/prompt_builders.py:293-365`
- 运行证据：
  - `call-0026` 的 feedback prompt 已包含：
    - `Facet Evidence`
    - `observation_confidence: HIGH`
    - `Scorer Seed Material`
    - `was_adjudicated: False`
    - `decision_note: no conflict, rater_1 score used (raters converged)`
  - `output_feedback.json` 的 6 个维度都带有 `scorer_rationale` 和 `was_adjudicated` 字段。
- 结论：
  - Stage M 这轮最核心的“解释阶段吃到 observation + scorer rationale + decision context”的目标已经在真实 provider 模式下被验证，不是只在 mock/单测里成立。

### Observation -> scoring 的 facet evidence 链路在真实 run 中是闭合的

- 运行证据：
  - `observations.json` 中 6 个 observation 全部是 `observation_confidence=high`。
  - 每个维度都有 `facet_findings`，且 `supporting_span_ids / counter_span_ids` 与对应 evidence spans 能对上。
  - 例如：
    - `ideas_content`: `16 supporting / 0 counter / 5 facet_findings`
    - `sentence_fluency`: `4 supporting / 8 counter / 3 facet_findings`
    - `conventions`: `1 supporting / 11 counter / 5 facet_findings`
- 结论：
  - 本轮并没有出现“抽证很多，但 observation 丢空”的结构性断链。

### Debug bundle 与 viewer 工件可用，已足够支撑外环前的局部排障

- 产物位置：
  - `artifacts/inner_loop_real_eval_20260330_deepseek_all/20717/_debug/run-57e4d1af04bd/manifest.json`
  - `artifacts/inner_loop_real_eval_20260330_deepseek_all/20717/_debug/run-57e4d1af04bd/summary.json`
  - `artifacts/inner_loop_real_eval_20260330_deepseek_all/20717/_debug/run-57e4d1af04bd/events.jsonl`
  - `artifacts/inner_loop_real_eval_20260330_deepseek_all/20717/_debug/run-57e4d1af04bd/viewer/index.html`
- 运行证据：
  - `event_counts` 完整闭环：`31 llm_call_started` / `31 llm_call_finished` / `7 node_started` / `7 node_finished`。
  - `manifest.json` 中 provider bindings 与本次实际路由一致。
- 结论：
  - 开发可视化工具本身是可用的，当前主要缺口不是“有没有”，而是“call-level model_id 还不够完整”。

## Outer-Loop Readiness

### 建议先完成的 4 个准备项

1. 给 `scoring` 和 `feedback` 增加显式 token budget，并把实际 budget 写进 debug metadata。
2. 修复 `LoggingProvider` 的 call-level `model_id` 记录，确保终端和 viewer 能直接看见真实模型名。
3. 在外环指标里新增：
   - `adjacent_disagreement_count`
   - `tokens_per_stage`
   - `p95 elapsed per provider`
   - `composite_gap_vs_human`
4. 外环第一轮先固定 provider routing，避免同时把“prompt/policy 变化”和“provider 风格差异”混在一起解释。

### 本轮结论

- 真实 LLM 主路径已经跑通，Stage M explanation 改造在真实链路中生效。
- 当前最需要优先处理的，不是功能正确性，而是：
  - `scoring` 的成本/时延失控
  - 可视化链路的模型标识缺失
  - adjudication policy 对边界漂移的观测盲区
- 在这三点不先收敛前，直接上外环批量实验会让结论噪声偏大，且成本不可控。
