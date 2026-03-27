# MAS 流水线 Review 文档

> 本文档记录系统完整流程梳理、Review 发现、以及后续调整与迭代记录。
> 所有讨论均在**接入真实 LLM** 的视角下进行。

---

## 一、完整评价流程梳理

以一篇作文文本为例，执行 `python scripts/eval.py --essay-id 20757`。

### 第零步：启动与初始化

`eval.py` 做三件准备工作：

1. **读取 TSV**：从 `data/training_set_8.tsv` 找到 essay_id=20757 的这行记录，取出原始文本和人工评分（用于事后对比）
2. **编译配置束（Bundle）**：调用 `config_resolver` → `ConfigCompiler`，将 `configs/bundles/asap_set8_baseline.bundle.yaml` 编译为一个冻结的 `ResolvedArtifactBundle`，包含：
   - `rubric_snapshot`：量规（6 个维度 I/O/V/W/S/C，每维 1-6 分级描述符）
   - `policy_snapshot`：策略（裁决阈值、Cusp Rule、聚合公式、解释要求）
   - bundle 整体 SHA-256 hash（保障回放一致性）
3. **构造 Provider**：从 `.env` 读取模型名和 API Key，构造：
   - `rater_1_provider`（如 DeepSeek）
   - `rater_2_provider`（如 Qwen）
   - 每个都被 `LoggingProvider` 包一层（打印调用详情）+ `GuardedProvider`（自动重试 + 超时保护）
4. **加载 Prompt 模板**：从 `configs/` 读取 `evidence_extraction.yaml`、`scoring.yaml`、`explanation.yaml` 三套 Jinja2 模板
5. **构造 `PipelineRunner`**，传入 bundle + providers + 模板，创建 `EvaluationRequest`（含原始文本）

---

### 第一步：预处理 `INIT → PREPROCESSED`

**执行者**：`preprocess.run(request)` — 纯确定性，不调用 LLM

- 清洗原始文本（去除多余空白等）
- 按句子边界切分为 `TextUnit[]`（每个 TextUnit 有内容 hash 作为 ID）
- 产出 `NormalizedDocument`（含所有 TextUnit）

---

### 第二步：覆盖规划 `PREPROCESSED → COVERAGE_PLANNED`

**执行者**：`coverage.run(document, rubric)` — 纯确定性，不调用 LLM

- 调用 `rubric_core` 遍历量规中的 6 个维度
- 为每个维度生成一个 `CoveragePlan`（记录：维度 ID、需要收集的 facet 列表、目标文本单元范围）
- 产出 6 个 `CoveragePlan[]`

---

### 第三步：证据抽取 `COVERAGE_PLANNED → EVIDENCE_EXTRACTED`

**执行者**：`extractor.run(plan, document, rubric, provider, template)` — **调用 LLM**

- 对每个维度的 CoveragePlan，用 `prompt_builders` 渲染 `evidence_extraction` 模板，生成 Prompt
- 调用 `evidence_extraction_provider`（实际是 `rater_1` 或默认 provider）
- 解析 LLM 返回的 JSON，转换为 `EvidenceSpan[]`（每条证据含：文本片段、facet_id、置信度）
- **6 个维度各调用一次 LLM**，产出 `all_spans_by_dim: Dict[dim_id → EvidenceSpan[]]`

---

### 第四步：观察构建 `EVIDENCE_EXTRACTED → OBSERVATION_BUILT`

**执行者**：`observer.run(spans, plan)` — 纯确定性，不调用 LLM

- 将每个维度的 `EvidenceSpan[]` 按 `facet_id` 分组
- 每个必需 facet 生成一个 `FacetFinding`（有 / 无 supporting spans）
- 根据 facet 覆盖率计算 confidence（全覆盖=HIGH，部分=MEDIUM，无=LOW）
- 产出 6 个 `DimensionObservation[]`

---

### 第五步：双评审打分 `OBSERVATION_BUILT → SCORED`

**执行者**：`scorer.run(obs, spans, rubric, doc, provider, template, rater_id)` — **调用 LLM**

- 对每个维度 × 每个 rater（rater_1、rater_2），渲染 `scoring` 模板
  - 模板包含：维度描述符、证据内容、评分标准、要求输出结构化 JSON
- 分别调用 `rater_1_provider` 和 `rater_2_provider`
- 解析返回 JSON 为 `ScoreHypothesis`（含 `canonical_score` 整数、`rationale` 文字理由）
- **6 维度 × 2 rater = 12 次 LLM 调用**，产出 12 个 `ScoreHypothesis[]`

---

### 第六步：一致性检验 `SCORED → CONSISTENCY_CHECKED`

**执行者**：`consistency_checker.run(hypotheses, policy)` — 纯计算，不调用 LLM

从 `policy_snapshot` 读取触发器规则，对每个维度评估：

- **score_distance 触发器**：`|rater_1分 - rater_2分| > 阈值`（如差值 > 1）
- **pattern_match / Cusp Rule**：跨维度模式匹配（如某维度均在临界分值 3/4 附近时特别处理）

产出 `ConflictRecord[]`，每条记录哪个维度冲突、建议路由（ADJUDICATED / RE_SCORE 等）

---

### 分叉：有无冲突

#### 路径 A：无冲突 → 直接进入反馈

用 `deterministic_adjudicator` 取 rater_1 结果生成 `FinalDimensionDecision[]`，直接跳到第八步。

#### 路径 B：有冲突 → 触发 rater_3 裁决

---

### 第七步（仅冲突时）：rater_3 全文重评 + 裁决 `CONSISTENCY_CHECKED → ADJUDICATED`

**执行者**：`scorer.run(..., rater_3_provider, ...)` — **调用 LLM**

- 按 ASAP Set 8 "resolution read" 规则：rater_3 对**全部 6 个维度**重新评分
- **6 次额外 LLM 调用**，产出 6 个 `rater_3_hypothesis`，追加进 `hypotheses`

**裁决**：`adjudicator.run(conflicts, hypotheses, policy)`

- 有冲突的维度：直接采用 rater_3 的分数（权威裁决）
- 无冲突的维度：保持 rater_1/rater_2 均值
- 产出 6 个 `FinalDimensionDecision[]` 和 `AdjudicationRecord[]`

若 rater_3 缺失（API 不可用等），路由到 `HUMAN_REVIEW`，流水线提前终止，返回空 feedback。

---

### 第八步：聚合总分

**执行者**：`compute_composite(decisions, hypotheses, adjudications, policy)` — 纯计算

从 `policy_snapshot` 读取 `asap_set8_composite.yaml` 中的加权公式：

```
无裁决：avg(I)*2 + avg(O)*2 + avg(S)*2 + avg(C)*4  →  量程 10–60
有裁决：I_R3*2  + O_R3*2  + S_R3*2  + C_R3*4      →  量程 10–60
```

（V/W 权重为 0，不计入总分）

产出 `CompositeDecision`（含 `canonical_score` 和 `aggregation_detail`）

---

### 第九步：反馈生成 `FEEDBACK_RENDERED`

**执行者**：`feedback_agent.run(...)` — **调用 LLM**

- 对每个维度，用 `explanation` 模板渲染 Prompt（含：最终分数、证据、量规描述符）
- 调用 `feedback_provider`（默认与 rater_1 同一个 provider）
- 生成自然语言解释文本，验证引用链（descriptor_ref → evidence_span → score）
- 产出 `feedback` dict：

```json
{
  "dimensions": {
    "ideas_content": { "canonical_score": 4, "summary": "...", "evidence": [...] },
    "organization":  { "canonical_score": 3, "summary": "..." },
    "..."
  },
  "summary": "综合评价...",
  "composite": {
    "composite_score": { "canonical_score": 38 },
    "aggregation_detail": { "variant_id": "without_resolution", "weights": {...} }
  }
}
```

---

### 第十步：终止验证 `VALIDATED`

`terminal_validation(decisions, plans, rubric)` 检查：

- 所有 6 个维度都有 FinalDimensionDecision
- 每个分数在量规合法范围内（1–6）

通过后状态机进入 `VALIDATED`，流水线正常完成。

---

### 最终输出

`eval.py` 收到 `(RunTrace, feedback)` 后：

1. **终端打印**：各维度分数对比（MAS vs 人工 rater_1/rater_2）、加权总分 X/60、LLM token 用量和耗时
2. **写入 `artifacts/eval/{essay_id}/`**：
   - `feedback.json`：完整评分结果（含 composite）
   - `hypotheses.json`：rater_1 / rater_2 各自的原始分数
   - `run_trace.json`：每个节点的执行时间、input/output ref、状态
   - `report.md`：人类可读的 Markdown 报告

---

### LLM 调用统计

| 阶段 | 调用次数 | Provider |
|------|---------|---------|
| 证据抽取 | 6次（每维度1次） | 默认 provider |
| rater_1 评分 | 6次 | rater_1_provider |
| rater_2 评分 | 6次 | rater_2_provider |
| 反馈生成 | 6次（每维度1次） | 默认 provider |
| **合计（无冲突）** | **24次** | — |
| rater_3 评分（有冲突时） | +6次 | rater_3_provider |
| **合计（有冲突）** | **30次** | — |

---

## 二、Review 记录

> 每次 Review 在此追加，格式：日期 + 发现 + 结论/决策

---

## 三、调整与迭代记录

> 每次对系统做出的调整在此追加，格式：日期 + 变更内容 + 涉及文件

---
