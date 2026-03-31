# MAS Pipeline Stages

本文档整理当前仓库主流程中的 9 个阶段。

说明：

- 这里统计的是从 `Preprocess` 到 `Validation` 的 9 个运行阶段。
- 运行前的 `Config Resolved / Bundle Freeze` 很重要，但不计入这 9 个阶段。
- 文档描述的是仓库当前实现状态，尤其以 `src/pipeline/runner.py` 的主路径为准。
- 除非特别说明，后续示例和说明默认站在“真实 LLM / provider 路径”上理解系统。

## 真实 LLM 中文示例

下面用一篇自拟中文短文，串起一次真实 LLM 调用下的数据流。

示例文章：

> 去年秋天，学校后院的老槐树被台风吹断了一根大枝。很多同学觉得它太危险，应该砍掉。我和几位同学查了资料，发现只要及时修剪和支撑，这棵树还可以继续活下去。后来我们把建议写成倡议书交给学校，老师也请来了园林工人。春天再来的时候，树上长出了新的嫩叶，我第一次觉得，保护一棵树也是在保护校园里的记忆。

为便于说明，假设本次运行使用：

- `bundle = asap_set8_baseline`
- `PipelineRunner(..., provider=<真实 provider>, prompt_templates=...)`
- rubric 仍是 ASAP Set 8 的 6 个维度：
  `ideas_content`、`organization`、`voice`、`word_choice`、`sentence_fluency`、`conventions`

先记住 4 个非常重要的现实约束：

- 当前 `preprocess` 只按 `(?<=[.!?]) +` 切句，不按中文 `。！？` 切句。
- 因此这篇中文文章在当前实现里，大概率只会得到 **1 个 `TextUnit`**，而不是 5 个中文句子单元。
- 当前 `word_count = len(normalized_text.split())`，对这类中文无空格文本通常会得到 **1**，只能当占位统计，不能当真实词数。
- 当前 provider 路径里，真正会调用外部 LLM 的阶段主要是：
  `Evidence Extraction`、`Scoring`、`Feedback`，以及发生冲突时为 `rater_3` 追加的那次 `Scoring`。

### 示例流 1：进入系统

- 原始入口是一个 `EvaluationRequest`
- 关键字段大致是：
  - `raw_text = 上面的整篇中文短文`
  - `bundle_ref = "asap_set8_baseline@v1"`
  - `request_id = 可选；不传则系统自动生成`

### 示例流 2：Preprocess 之后

- `normalized_text` 基本等于原文去首尾空白后的结果
- 系统会生成：
  - `request_id = req-...`
  - `document_id = doc-...`
  - `text_units = [TextUnit(...)]`
- 由于当前中文切句规则有限，这里很可能只有 1 个 `TextUnit`
- 这意味着后面很多阶段看到的文档，实际上是“整篇中文文章作为一个句子单元”

### 示例流 3：Coverage Planning 之后

- 系统会生成 6 条 `CoveragePlan`，每个维度 1 条
- 例如 `ideas_content` 的 plan 会包含：
  - `dimension_id = "ideas_content"`
  - `target_unit_ids = [那唯一一个 unit_id]`
  - `required_facets = ["clarity_focus", "main_idea_salience", ...]`
- `organization` 的 plan 也会指向同一个 `target_unit_ids`
- 所以当前中文文章在实现上更接近：
  “每个维度都对整篇文章做 full scan”

### 示例流 4：Evidence Extraction 之后

- 对每条 `CoveragePlan`，`extractor.run(...)` 都会调用一次 provider
- prompt 中会注入：
  - 当前维度名与 code
  - 当前维度要求覆盖的 `required_facets`
  - `essay_text = 整篇中文文章`
- 如果 provider 表现正常，可能会返回类似证据：
  - `ideas_content`：
    - “查了资料”
    - “把建议写成倡议书交给学校”
    - “保护一棵树也是在保护校园里的记忆”
  - `organization`：
    - “去年秋天 … 后来 … 春天再来的时候 …”
- 这些内容会被转成 `EvidenceSpan`
- 如果 provider 没覆盖某个 `required_facet`，系统还会补一个 `fallback GLOBAL span`

### 示例流 5：Observation Build 之后

- 同一维度下的 `EvidenceSpan` 会被整理成 1 个 `DimensionObservation`
- 例如 `ideas_content` 会得到：
  - `supporting_span_ids = [span1, span2, span3, ...]`
  - `facet_findings = 每个 facet 一个 finding`
  - `observation_confidence = HIGH / MEDIUM / LOW`
- 当前这个阶段不会再调用 LLM
- 它做的是“把零散证据整理成可评分视图”

### 示例流 6：Scoring 之后

- 对每个维度、每个评分员，都会生成一条 `ScoreHypothesis`
- 在真实路径下，默认先有：
  - `rater_1`
  - `rater_2`
- 所以 6 个维度通常先得到 12 条 `ScoreHypothesis`
- 每次 `scorer.run(...)` 的 prompt 会带上：
  - 当前维度 levels
  - 该维度 observation 引用到的 evidence spans
  - `essay_text = 整篇中文文章`
- 例如：
  - `rater_1` 可能给 `ideas_content = 5`
  - `rater_2` 可能给 `ideas_content = 4`
  - `rater_1/rater_2` 对 `organization` 都给 `4`

### 示例流 7：Consistency Check 之后

- 系统会按 policy 检查 12 条 `ScoreHypothesis` 是否冲突
- 如果 `ideas_content` 出现：
  - `rater_1 = 5`
  - `rater_2 = 3`
  - 且分差超过阈值
  那就会产生一条 `ConflictRecord`
- 如果没有冲突，系统会直接进入最终决定生成
- 如果有冲突，系统会路由到 `ADJUDICATED`

### 示例流 8：Adjudication 之后

- 在真实路径里，一旦进入裁决，runner 会先补跑一轮 `rater_3` 的 `Scoring`
- 这个补评分不是只评冲突维度，而是对全部维度再打一轮
- 然后 `adjudicator.run(...)` 会：
  - 对冲突维度优先采用 `rater_3`
  - 对无冲突维度仍按当前实现取 `rater_id` 字典序最小者
- 最终得到每个维度唯一的 `FinalDimensionDecision`

### 示例流 9：Feedback 之后

- `feedback.run(...)` 会把每个 `FinalDimensionDecision` 包装成统一输出
- 在真实路径里，这里会再次调用 provider 生成每个维度的 `feedback_text`
- 例如 `ideas_content` 维度的最终输出条目会包含：
  - `canonical_score`
  - `final_score`
  - `descriptor_refs`
  - `evidence_span_ids`
  - `feedback_text`
  - `decision_confidence`
- 根级还会包含：
  - `dimensions`
  - `violations`
  - `generated_at`
  - `summary`
  - `provider`
  - `composite`

### 示例流 10：Validation 之后

- `terminal_validation()` 只做两件事：
  - 每个 plan 维度都有最终 decision
  - 最终分数在合法量表范围内
- 通过后，`RunTrace.terminal_validation_passed = true`
- 但当前实现即使不通过，也只是记录布尔结果，不会自动把整次运行改成失败

### 这个示例最值得长期记住的点

- 讨论真实运行时，默认应该把系统理解为：
  `typed pipeline + provider-backed extraction/scoring/feedback`
- 当前中文文本在 `Preprocess` 阶段的切句能力很弱，会直接影响：
  - `text_units`
  - `target_unit_ids`
  - `essay_text` 如何被后续 prompt 使用
- 当前 provider 路径虽然叫“按阶段处理”，但 `Extraction` 和 `Scoring` prompt 里实际仍然是全文文本，而不是严格局部窗口
- `Adjudication` 阶段本身不直接调用 LLM；真实路径里的额外 LLM 调用发生在进入裁决前补跑的 `rater_3` 评分
- `Feedback` 已经是统一出口；讨论输出结构时，应优先看 `src/agents/feedback.py`

## 1. Preprocess

### 输入输出
输入：

- `EvaluationRequest`

输出：

- `NormalizedRequest`
- `NormalizedDocument`

### 涉及代码文件

- `src/pipeline/runner.py`
- `src/agents/preprocess.py`
- `src/contracts/request_models.py`
- `tests/unit/agents/test_mock_workers.py`

### 内部发生了什么

- `runner` 在 `node_preprocess` 中调用 `preprocess.run(request)`。
- 对输入文本做最小归一化，当前主要是 `strip()`。
- 生成稳定的 `request_id`、`document_id`、`unit_id`。
- 将文本按句号/问号/感叹号后的空格做简单切句。
- 构造 `TextUnit[]`，保留字符偏移。
- 构造 `NormalizedRequest` 和 `NormalizedDocument`。
- 写 trace、checkpoint，并推进状态到 `PREPROCESSED`。

### 当前没做什么

- 不调用 LLM。
- 不做真正 NLP 分析。
- 不做段落级切分。
- 不做匿名化、拼写纠正、复杂清洗。

### 精炼总结

把原始输入文本整理成后续阶段可引用、可定位、可追踪的规范化文档。

## 2. Coverage Planning

### 输入输出
输入：

- `NormalizedDocument`
- `RubricSnapshot`

输出：

- `List[CoveragePlan]`

### 涉及代码文件

- `src/pipeline/runner.py`
- `src/agents/coverage.py`
- `src/policies/rubric_core.py`
- `src/contracts/request_models.py`
- `src/pipeline/validators.py`
- `configs/rubrics/asap_set8_baseline.yaml`

### 内部发生了什么

- `runner` 在 `node_coverage` 中调用 `coverage.run(document, rubric)`。
- `coverage` 先收集全文所有 `TextUnit.unit_id`。
- 通过 `rubric_core.build_dimension_traversal(rubric)` 遍历所有维度。
- 从每个维度的 `observation_schema.required_facets` 读取 `required_facets`。
- 为每个维度生成一条 `CoveragePlan`。
- 当前 plan 默认使用：
  - `target_unit_ids = 全部 text units`
  - `minimum_evidence_units = max(1, len(required_facets))`
  - `allowed_evidence_scopes = ["span", "global"]`
  - `coverage_strategy = "full_scan"`
- `validate_coverage_plans()` 检查每个 rubric 维度都有对应 plan。
- 写 trace、checkpoint，并推进状态到 `COVERAGE_PLANNED`。

### 当前没做什么

- 不调用 LLM。
- 不做语义检索或优先级排序。
- 不按维度缩小搜索窗口。
- 不真正根据 `evidence_requirements` 生成不同策略。
- 当前更像计划展开器，不是智能 planner。

### 精炼总结

把量规中的每个维度翻译成一份结构化的证据搜索任务单。

## 3. Evidence Extraction

### 输入输出
输入：

- `CoveragePlan`
- `NormalizedDocument`

real 路径额外输入：

- `RubricSnapshot`
- `BaseProvider`
- `PromptTemplate`

输出：

- 原子输出：`List[EvidenceSpan]`
- pipeline 内部聚合输出：`Dict[dimension_id, List[EvidenceSpan]]`

### 涉及代码文件

- `src/pipeline/runner.py`
- `src/agents/deterministic_extractor.py`
- `src/agents/extractor.py`
- `src/agents/prompt_builders.py`
- `src/contracts/evidence.py`
- `tests/unit/agents/test_mock_workers.py`
- `tests/e2e/test_real_provider_smoke.py`

### 内部发生了什么

- `runner` 在 `node_extractor` 中对每个 `CoveragePlan` 分别执行抽取。
- deterministic 路径：
  - 调用 `deterministic_extractor.run(plan, document)`。
  - 为每个 `required_facet` 生成一个 `GLOBAL` scope 的 `EvidenceSpan`。
  - `span_id` 由 `plan_id + facet_id` 的哈希稳定生成。
- provider 路径：
  - 通过 `build_extraction_prompt()` 构造抽取 prompt。
  - 调用 provider。
  - 解析结构化输出或回退解析文本输出。
  - 根据返回内容构造 `EvidenceSpan`。
  - 有 offset 时标为 `SPAN`，否则标为 `GLOBAL`。
  - 若某些 `required_facets` 未覆盖，则补 `fallback` spans。
- 统计总 spans 数量，写 trace、checkpoint，并推进状态到 `EVIDENCE_EXTRACTED`。

### 当前没做什么

- deterministic 路径不真正读文本内容。
- provider 路径没有真正按 `target_unit_ids` 裁切上下文。
- `minimum_evidence_units`、`allowed_evidence_scopes`、`coverage_strategy` 未被严格执行。
- 当前不回填 `unit_id`。
- 没有专门的 `validate_evidence_spans()`。
- 不校验 `text_quote` 与 offset 是否严格对应。

### 精炼总结

按照阶段二定义的任务单，真正从文本中产出原子证据。

## 4. Observation Build

### 输入输出
输入：

- `List[EvidenceSpan]`
- `CoveragePlan`

输出：

- `List[DimensionObservation]`

### 涉及代码文件

- `src/pipeline/runner.py`
- `src/agents/observer.py`
- `src/contracts/evidence.py`
- `src/pipeline/validators.py`
- `tests/unit/agents/test_mock_workers.py`

### 内部发生了什么

- `runner` 在 `node_observer` 中对每个维度分别调用 observer。
- 当前无论 mock 还是 real 路径，都统一调用 `observer.run(spans, plan)`。
- `observer` 先按 `facet_id -> span_ids` 做归类。
- 依据 `plan.required_facets` 逐个构造 `FacetFinding`。
- 构造 `DimensionObservation`：
  - `supporting_span_ids`
  - `counter_span_ids`
  - `facet_findings`
  - `observation_confidence`
  - `uncertainty_notes`
- `observation_confidence` 当前按 facet 覆盖比例确定：
  - 全覆盖 `HIGH`
  - 部分覆盖 `MEDIUM`
  - 无覆盖 `LOW`
- `validate_observations()` 检查每个 plan 都有 observation。
- 写 trace、checkpoint，并推进状态到 `OBSERVATION_BUILT`。

### 当前没做什么

- 没有 `real_observer`。
- 不做新的 LLM 推理。
- `counter_span_ids` 当前为空。
- 不做 facet 级摘要或冲突分析。
- `uncertainty_notes` 当前基本为空。
- validator 只检查“有没有 observation”，不检查质量。

### 精炼总结

把分散的证据整理成面向评分阶段可直接消费的结构化观察对象。

## 5. Scoring

### 输入输出
输入：

- `List[DimensionObservation]`
- `RubricSnapshot`
- `rater_ids`

real 路径额外输入：

- `List[EvidenceSpan]`
- `NormalizedDocument`
- `BaseProvider`
- `PromptTemplate`

输出：

- `List[ScoreHypothesis]`

### 涉及代码文件

- `src/pipeline/runner.py`
- `src/agents/deterministic_scorer.py`
- `src/agents/scorer.py`
- `src/agents/prompt_builders.py`
- `src/policies/rubric_core.py`
- `src/contracts/scoring.py`
- `src/pipeline/validators.py`
- `tests/unit/agents/test_mock_workers.py`

### 内部发生了什么

- `runner` 在 `node_scorer` 中对每个 `(observation, rater_id)` 组合产出一条 hypothesis。
- deterministic 路径：
  - 用 `observation_id + rater_id + dimension_id` 做哈希。
  - 将结果映射进该维度的合法分值区间。
  - 通过 rubric 读取 `scale_ref` 和 descriptor refs。
  - 组装成 `ScoreHypothesis`。
- provider 路径：
  - 用 `build_scoring_prompt()` 构造 prompt。
  - prompt 包含维度信息、levels、相关 evidence spans、全文文本。
  - 调用 provider。
  - 解析 `proposed_score`、`descriptor_refs`、`confidence`、`justification`。
  - 将分数 clamp 到 rubric 合法区间。
  - 组装成 `ScoreHypothesis`。
- `validate_hypotheses()` 检查每个 `(dimension, rater)` 组合都有 hypothesis。
- 写 trace、checkpoint，并推进状态到 `SCORED`。

### 当前没做什么

- deterministic scorer 不真正理解证据，只生成可复现分数。
- scorer 当前不真正使用 LLM 返回的 `evidence_ids`。
- 当前不利用 `observation_confidence` 调整评分。
- 不在这里处理评分冲突。
- provider 路径的 hypothesis ID 不是确定性的。

### 精炼总结

让每个评分员基于每个维度的 observation 产出各自的评分提案。

## 6. Consistency Check

### 输入输出
输入：

- `List[ScoreHypothesis]`
- `PolicySnapshot`

输出：

- `List[ConflictRecord]`

### 涉及代码文件

- `src/pipeline/runner.py`
- `src/agents/deterministic_consistency_checker.py`
- `src/agents/consistency_checker.py`
- `src/policies/adjudication.py`
- `src/orchestrator/router.py`
- `src/contracts/scoring.py`
- `tests/unit/agents/test_mock_workers.py`

### 内部发生了什么

- `runner` 在 `node_consistency_checker` 中检查各评分员结果是否冲突。
- deterministic 路径：
  - 读取 policy 中第一个 `score_distance` trigger。
  - 对同维度 hypotheses 两两比较。
  - 若分差超过阈值，生成 `ConflictRecord`。
- policy-aware 路径：
  - 调用 policy-aware `consistency_checker.run()`。
  - 通过 `evaluate_all_triggers()` 评估全部已配置 trigger。
  - 当前支持：
    - `score_distance`
    - `pattern_match`
  - 可表达如 non-adjacent、cusp 等规则。
- 生成 `ConflictRecord` 后，`route_after_consistency_check()` 决定下一步：
  - 无冲突：`FEEDBACK_RENDERED`
  - 需裁决：`ADJUDICATED`
  - 需回抽：`RE_EXTRACT`
  - 需重评：`RE_SCORE`
  - 需人工：`HUMAN_REVIEW`
- 如果没有冲突，runner 会直接根据 hypotheses 生成最终 decisions，跳过正式 adjudication 节点。

### 当前没做什么

- 不重新看原文或证据。
- 不解决冲突，只发现冲突并建议路由。
- mock checker 只看第一个 `score_distance` trigger。
- 当前没有专门的 `validate_conflicts()`。

### 精炼总结

检查多个评分员的打分提案是否触发冲突规则，并决定状态机下一步往哪走。

## 7. Adjudication

### 输入输出
输入：

- `List[ConflictRecord]`
- `List[ScoreHypothesis]`
- `PolicySnapshot`

输出：

- `List[AdjudicationRecord]`
- `List[FinalDimensionDecision]`

### 涉及代码文件

- `src/pipeline/runner.py`
- `src/agents/adjudicator.py`
- `src/agents/deterministic_adjudicator.py`
- `src/contracts/scoring.py`
- `src/orchestrator/router.py`
- `tests/unit/agents/test_mock_workers.py`
- `tests/unit/agents/test_fixes_verification.py`

### 内部发生了什么

- 当上一阶段将流程路由到 `ADJUDICATED` 时，runner 才真正进入本阶段。
- provider 路径下，如果是第三评分员裁决路径，runner 会先补跑一轮 `rater_3` 评分，再进入 adjudicator。
- deterministic adjudicator：
  - 对冲突维度，从冲突 hypotheses 中按 `hypothesis_id` 字典序选 winner。
  - 对无冲突维度，从 `rater_id` 字典序最小者取分。
  - 生成 `AdjudicationRecord` 和 `FinalDimensionDecision`。
- adjudicator：
  - 读取 policy 中的 `resolution_rater_label`。
  - 对冲突维度，优先使用 `rater_3` 的 hypothesis 作为权威结果。
  - 若缺失 `rater_3` hypothesis，则标记未解决并建议 `HUMAN_REVIEW`。
  - 对无冲突维度，取 `rater_id` 字典序最小者的分数。
- 两条路径都会聚合该维度的：
  - `evidence_span_ids`
  - `descriptor_refs`
- `route_after_adjudication()` 决定后续去：
  - `FEEDBACK_RENDERED`
  - `RE_EXTRACT`
  - `RE_SCORE`
  - `HUMAN_REVIEW`

### 当前没做什么

- adjudicator 本身不直接调用 LLM。
- mock adjudicator 只是确定性选 winner，不是真实仲裁逻辑。
- real adjudicator 当前主要实现的是 `use_rater_3_as_authoritative`。
- 不支持更复杂的 weighted merge、reasoning-based merge、policy average 全面策略。
- `decision_note` 当前基本为空。

### 精炼总结

把有分歧的多个评分提案收敛成每个维度唯一的权威最终决定。

## 8. Feedback

### 输入输出
输入：

- `List[FinalDimensionDecision]`
- `List[DimensionObservation]`
- `RubricSnapshot`

real 路径额外输入：

- `List[EvidenceSpan]`
- `BaseProvider`
- `PromptTemplate`

输出：

- `Dict[str, Any]` 形式的 feedback 结果

runner 还会在输出中补：

- `feedback["composite"]`

### 涉及代码文件

- `src/pipeline/runner.py`
- `src/agents/feedback.py`
- `src/agents/prompt_builders.py`
- `src/policies/explanation.py`
- `tests/unit/agents/test_mock_workers.py`
- `tests/unit/policies/test_explanation_policy.py`

### 内部发生了什么

- `runner` 在 feedback 之前先计算 composite 总分。
- 两种模式现在统一调用 `feedback.run(...)`。
- `feedback.run(...)` 先基于 `FinalDimensionDecision`、`EvidenceSpan`、`RubricSnapshot` 和 `PolicySnapshot` 生成结构化 explanation，并执行 citation-chain policy 检查。
- 如果是 mock / deterministic 路径：
  - 直接使用 explanation policy 生成的 commentary 作为 `feedback_text`。
- 如果是真实 LLM 路径：
  - 对每个维度筛出相关证据。
  - 用 `build_explanation_prompt()` 构造 explanation prompt。
  - 调用 provider。
  - 将返回文本裁剪到 policy 允许的长度后写入 `feedback_text`。
- 最终对每个维度统一输出同一套字段，包括：
  - `canonical_score`
  - `final_score`（兼容别名）
  - `display_score`
  - `descriptor_refs`
  - `evidence_span_ids`
  - `feedback_text`
  - `commentary`
  - `decision_confidence`
  - `confidence`（兼容别名）
- `runner` 最后统一补上 `composite`。

### 当前没做什么

- 当前仍然没有单独的强类型 feedback contract，返回值还是 `Dict[str, Any]`。
- 当前 terminal validation 仍然不校验 feedback 结构。
- 真实路径的 policy enforcement 主要仍然校验 citation chain，不会对 LLM 生成文本做更深层语义审核。

### 精炼总结

把最终维度决定统一包装成同一结构的反馈结果，并附上 composite 总分。

## 9. Validation

### 输入输出
输入：

- `List[FinalDimensionDecision]`
- `List[CoveragePlan]`
- `RubricSnapshot`

输出：

- `terminal_validation_passed: bool`
- 写入最终 `RunTrace`

### 涉及代码文件

- `src/pipeline/runner.py`
- `src/pipeline/validators.py`
- `src/contracts/trace.py`
- `src/orchestrator/trace_store.py`
- `tests/e2e/test_mock_baseline_normal_path.py`
- `tests/integration/test_mock_pipeline.py`

### 内部发生了什么

- `runner` 在 feedback 完成后调用 `terminal_validation(decisions, plans, rubric)`。
- `terminal_validation()` 检查：
  - 每个 `CoveragePlan.dimension_id` 都有对应 `FinalDimensionDecision`
  - 每个最终分数都在该维度合法分值区间内
- 返回布尔值 `terminal_passed`。
- `runner` 推进状态到 `VALIDATED`。
- `TraceStore.build_run_trace()` 将 `terminal_validation_passed` 写入 `RunTrace`。

### 当前没做什么

- 没有单独的 `node_validation`。
- 不校验 feedback 输出结构。
- 不校验 composite。
- 不校验 explanation policy / citation chain。
- 当前即使 `terminal_validation_passed=False`，runner 仍会返回 `RunStatus.COMPLETED`，只是把布尔结果记录下来。

### 精炼总结

对最终结果做最后一轮完整性和分值合法性检查，并把结果写入审计追踪。

## 总结

这 9 个阶段可以概括为：

1. `Preprocess`：把原始文本变成规范化文档
2. `Coverage Planning`：定义每个维度要找什么证据
3. `Evidence Extraction`：真正抽取证据
4. `Observation Build`：把证据整理成可评分观察
5. `Scoring`：每个评分员产出评分提案
6. `Consistency Check`：判断评分提案是否冲突
7. `Adjudication`：将冲突收敛为最终权威决定
8. `Feedback`：把最终决定包装成可交付输出
9. `Validation`：对最终结果做终态校验并写入 `RunTrace`

如果把运行前准备也算上，还应在最前面补一个：

- `Config Resolved / Bundle Freeze`

但它属于运行前置阶段，不在本文档的 9 个正式阶段之内。
