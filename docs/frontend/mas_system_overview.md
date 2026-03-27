# MAS 系统概览 — 前端展示页实现参考

> 本文档基于代码库实际代码与样本输出文件编写，供前端"项目汇报展示页"实现使用。
> 文档中所有字段名、示例值均来自真实文件，不做推测。

---

## 1. 系统完整处理流程

```
输入文章 (raw text)
    │
    ▼
[Stage 1] Preprocess         — 文本规范化，生成 NormalizedDocument（含 doc_id、TextUnit 切分）
    │
    ▼
[Stage 2] Coverage Planning  — 按量规生成 CoveragePlan[]，每个维度一个计划
    │
    ▼
[Stage 3] Evidence Extraction — LLM 从原文中按维度提取 EvidenceSpan[]
                               每个 span 携带: text_quote + start_offset + end_offset
    │
    ▼
[Stage 4] Observation Build  — 将 span 按 facet 分组，生成 DimensionObservation[]
    │
    ▼
[Stage 5] Scoring            — 两位评审员(rater_1, rater_2)各自为每个维度生成 ScoreHypothesis
                               共 12 个假设（6 维度 × 2 评审员）
    │
    ▼
[Stage 6] Consistency Check  — 检测评审员之间的分歧，无分歧则跳过裁决
    │          │
    │     有分歧时
    │          ▼
    │    Adjudication        — 按策略裁决，可能触发 re_score / re_extract / human_review
    │
    ▼
[Stage 7] Feedback           — LLM 为每个维度生成 feedback_text，组装 feedback.json
    │
    ▼
[Output] feedback.json + run_trace.json  — 写入 artifacts/ 目录
```

**正常路径节点序列**（见 `data/samples/baseline_manifest.yaml`）：
`config_resolved → preprocessed → coverage_planned → evidence_extracted →
observation_built → scored → consistency_checked → feedback_rendered → validated`

**实际耗时参考**（样本 20716，真实 LLM，见 `artifacts/eval/20716/report.md`）：
- 证据抽取（Stage 3）：约 3 分 50 秒
- 双评审打分（Stage 5）：约 2 分 55 秒
- 反馈生成（Stage 7）：约 52 秒
- 全程总计：约 7 分 38 秒

---

## 2. `feedback.json` 字段说明

### 2.1 整体结构

```json
{
  "dimensions": {
    "<dimension_id>": { ... },
    "<dimension_id>": { ... }
  },
  "summary": "Real provider feedback for 6 dimension(s).",
  "provider": "openai_compatible"
}
```

### 2.2 每个维度的字段（统一输出，来自 `src/agents/feedback.py`）

```json
"ideas_content": {
  "dimension_name": "Ideas and Content",
  "canonical_score": 4,
  "display_score": "4",
  "display_annotation": null,
  "descriptor_refs": [
    "clear main ideas",
    "easily identifiable purpose",
    "supporting details are relevant but may be overly general or limited"
  ],
  "evidence_span_ids": [
    "span-real-00798a57318d",
    "span-real-088a1408b267",
    ...
  ],
  "feedback_text": "Your essay received a score of 4 for Ideas and Content...",
  "confidence": 0.85
}
```

| 字段 | 类型 | 是否稳定 | 说明 |
|------|------|----------|------|
| `dimension_name` | string | **稳定** | 维度英文全名，来自量规配置 |
| `canonical_score` | int | **稳定** | 最终分数（整数，1-6） |
| `display_score` | string | **稳定** | 展示用分数（当前与 canonical_score 相同，如 "4"） |
| `display_annotation` | string\|null | 稳定 | 额外注释，通常为 null |
| `descriptor_refs` | string[] | **稳定** | 与该分数对应的量规描述语片段，直接来自量规文本 |
| `evidence_span_ids` | string[] | **稳定** | 支撑该分数的 span ID 列表 |
| `feedback_text` | string | **稳定** | LLM 生成的完整反馈文本，含 Markdown 加粗 |
| `confidence` | float | **稳定** | 置信度，范围 [0.0, 1.0]，当前固定为 0.85 |

> **注意**：现在 mock 与真实 provider 已统一为同一结构。
> 其中 `final_score` 仍然保留，但只是 `canonical_score` 的兼容别名。

### 2.3 `summary` 和 `provider`

| 字段 | 说明 |
|------|------|
| `summary` | 整体摘要文本，如 "Real provider feedback for 6 dimension(s)." |
| `provider` | 使用的 provider 名称，如 "openai_compatible" |

---

## 3. `run_trace.json` 字段说明

### 3.1 顶层字段（来自 `src/contracts/trace.py` `RunTrace`）

```json
{
  "run_id": "run-cb058861ea90",
  "bundle_version": "2026-03-12",
  "bundle_id": "asap_set8_baseline",
  "request_id": "req-8bcf9cb8eb67",
  "status": "completed",
  "started_at": "2026-03-16T12:10:31.028584+00:00",
  "finished_at": "2026-03-16T12:18:09.850256+00:00",
  "node_traces": [ ... ],
  "terminal_validation_passed": true,
  "replay_metadata": {
    "provider": "openai_compatible"
  }
}
```

| 字段 | 前端展示价值 | 说明 |
|------|-------------|------|
| `run_id` | **展示** | 唯一运行 ID，用于溯源 |
| `bundle_version` | **展示** | 量规版本，如 "2026-03-12" |
| `bundle_id` | **展示** | 量规包 ID，如 "asap_set8_baseline" |
| `status` | **展示** | 运行状态：completed / failed / human_review |
| `started_at` / `finished_at` | **展示** | 计算总耗时 |
| `terminal_validation_passed` | **展示** | 最终验证是否通过（true/false） |
| `replay_metadata.provider` | **展示** | 使用的 LLM provider |
| `request_id` | 内部 | 对应原始请求，前端通常无需展示 |

### 3.2 每个 `node_traces` 条目

```json
{
  "node_id": "node_extractor",
  "run_id": "run-cb058861ea90",
  "node_type": "extract",
  "status": "success",
  "started_at": "2026-03-16T12:10:31.028821+00:00",
  "finished_at": "2026-03-16T12:14:21.877681+00:00",
  "input_ref": "plans:6",
  "output_ref": "spans:52",
  "checkpoint": { ... },
  "fallback_history": [],
  "error_message": null,
  "metadata": {}
}
```

**前端适合展示的节点字段**：

| 字段 | 展示建议 |
|------|---------|
| `node_type` | 阶段名称（preprocess/coverage/extract/observe/score/check_consistency/feedback） |
| `status` | success / failed / skipped 状态图标 |
| `started_at` - `finished_at` | 计算并展示每阶段耗时（秒） |
| `output_ref` | 阶段产出摘要，如 "spans:52"、"hyps:12"、"dims:6" |
| `fallback_history` | 如非空，说明发生过重试 |
| `error_message` | 如非 null，展示错误信息 |

**节点类型与含义对照**：

| node_type | 中文含义 | output_ref 示例 |
|-----------|---------|----------------|
| preprocess | 文档预处理 | `doc-f34ce0393252` |
| coverage | 维度覆盖确认 | `plans:6` |
| extract | 证据抽取 | `spans:52` |
| observe | 证据整理 | `obs:6` |
| score | 双评审打分 | `hyps:12` |
| check_consistency | 一致性检验 | `conflicts:0` |
| feedback | 反馈生成 | `dims:6` |

---

## 4. 原文证据的产生机制与 `evidence_span_ids` 的映射

### 4.1 证据如何产生

1. LLM 被要求在原文中定位支撑评分的文本片段（`src/agents/extractor.py`）
2. LLM 返回结构化 JSON，每个 span 包含：
   - `quote`：原文引用（verbatim）
   - `start_offset`：在规范化文本中的字符起始位置（含）
   - `end_offset`：字符终止位置（不含）
3. 系统分配唯一 `span_id`（格式如 `span-real-00798a57318d`），封装为 `EvidenceSpan` 对象

### 4.2 `EvidenceSpan` 完整字段（来自 `src/contracts/evidence.py`）

```python
span_id:          str          # 唯一 ID
document_id:      str          # 父文档 ID
unit_id:          str | None   # 父 TextUnit ID（SPAN scope 时有值）
text_quote:       str | None   # 原文引用文本
start_offset:     int | None   # 字符偏移起始（SPAN scope）
end_offset:       int | None   # 字符偏移终止（SPAN scope）
scope:            "span"|"global"
dimension_id:     str          # 所属维度
facet_ids:        list[str]    # 所属 facet
extraction_note:  str | None   # 提取备注
```

### 4.3 `evidence_span_ids` 如何映射到原文

`feedback.json` 中每个维度的 `evidence_span_ids` 是 span ID 列表，例如：
```json
"evidence_span_ids": ["span-real-00798a57318d", "span-real-088a1408b267", ...]
```

这些 ID 在运行时存于内存中，**当前版本不持久化 span 对象到磁盘**。
已保存的 artifacts 目录中只有 `feedback.json` 和 `run_trace.json`，没有独立的 spans 文件。

**前端实现含义**：
- 如需展示具体引用文本（`text_quote`），需要系统在写出 artifacts 时额外输出 spans 文件
- 当前 `feedback_text` 中已内嵌了关键引用（如 `"I said hi I'm @CAPS4 do you want to hear a joke"`），
  可以从 `feedback_text` 中直接提取 Markdown 加粗的引用内容用于展示
- `evidence_span_ids` 的数量（`len(evidence_span_ids)`）可作为"证据丰富度"的量化指标

---

## 5. 六个维度的固定顺序、名称与分值范围

六个维度在量规配置（`configs/rubrics/asap_set8_baseline.yaml`）中按固定顺序定义，
前端应按此顺序展示：

| 序号 | dimension_id | 英文全名 | 简称/代码 | 分值范围 | 复合公式权重 |
|------|-------------|---------|----------|---------|------------|
| 1 | `ideas_content` | Ideas and Content | I | 1–6 | ×2 |
| 2 | `organization` | Organization | O | 1–6 | ×2 |
| 3 | `voice` | Voice | V | 1–6 | ×0（不计入合计） |
| 4 | `word_choice` | Word Choice | W | 1–6 | ×0（不计入合计） |
| 5 | `sentence_fluency` | Sentence Fluency | S | 1–6 | ×2 |
| 6 | `conventions` | Conventions | C | 1–6 | ×4 |

**复合得分公式**（来自 `configs/policies/aggregation/asap_set8_composite.yaml`）：

无裁决时（使用两位评审员平均）：
```
composite = (I_R1+I_R2)/2×2 + (O_R1+O_R2)/2×2 + (S_R1+S_R2)/2×2 + 2×(C_R1+C_R2)/2
```

有裁决时（使用第三评审员）：
```
composite = 2×I_R3 + 2×O_R3 + 2×S_R3 + 4×C_R3
```

**理论满分**：`2×6 + 2×6 + 2×6 + 4×6 = 60`

> Voice 和 Word Choice 不计入复合得分。

**各分值的等级描述参考**（基于量规标准）：

| 分值 | 等级建议 |
|------|---------|
| 1 | 极差 |
| 2 | 较差 |
| 3 | 中等 |
| 4 | 良好 |
| 5 | 优秀 |
| 6 | 卓越 |

---

## 6. 人工评分数据的来源与字段含义

### 6.1 数据来源

人工评分来自 ASAP 数据集，存储于 `data/training_set_8.tsv`，格式为制表符分隔。

**TSV 列头**：
```
essay_id  essay_set  essay
rater1_domain1  rater2_domain1  rater3_domain1  domain1_score
rater1_domain2  rater2_domain2  domain2_score
rater1_trait1 .. rater1_trait6
rater2_trait1 .. rater2_trait6
rater3_trait1 .. rater3_trait6
```

### 6.2 Trait 编号到维度的映射

| TSV 列名 | 对应 dimension_id |
|---------|-----------------|
| `rater1_trait1` / `rater2_trait1` | `ideas_content` |
| `rater1_trait2` / `rater2_trait2` | `organization` |
| `rater1_trait3` / `rater2_trait3` | `voice` |
| `rater1_trait4` / `rater2_trait4` | `word_choice` |
| `rater1_trait5` / `rater2_trait5` | `sentence_fluency` |
| `rater1_trait6` / `rater2_trait6` | `conventions` |

### 6.3 具体示例（essay_id = 20716）

| dimension_id | rater1 | rater2 | MAS | 偏差 |
|-------------|--------|--------|-----|------|
| ideas_content | 4 | 3 | 4 | +0.5 |
| organization | 4 | 4 | 3 | -1.0 |
| voice | 4 | 4 | 4 | 0.0 |
| word_choice | 4 | 4 | 3 | -1.0 |
| sentence_fluency | 4 | 3 | 3 | -0.5 |
| conventions | 3 | 3 | 3 | 0.0 |
| **合计（composite）** | 23 | 21 | 20 | -2.0 |

"偏差" = MAS 得分 - 人类均值

### 6.4 如何与 MAS 结果对齐

1. 以 `essay_id` 为主键（TSV 第 1 列 = 目录名，如 `20716`）
2. 从 TSV 中取 `rater1_trait1..6` 和 `rater2_trait1..6`
3. 从 `feedback.json` 的 `dimensions` 中取各维度的 `canonical_score`
4. 按维度 ID 对齐（使用上表的 trait 编号映射）
5. 人类均值 = `(rater1_traitN + rater2_traitN) / 2`
6. 偏差 = MAS 得分 - 人类均值
7. 评估指标使用 QWK（二次加权 Kappa），由 `src/evaluation/qwk.py` 实现

---

## 7. 哪些数据适合前端展示，哪些只是内部中间结果

### 7.1 适合前端直接展示

| 数据来源 | 字段 | 展示建议 |
|---------|------|---------|
| `feedback.json` | `dimensions[id].canonical_score` | 维度得分，雷达图/条形图 |
| `feedback.json` | `dimensions[id].dimension_name` | 维度标签 |
| `feedback.json` | `dimensions[id].feedback_text` | 逐维度反馈文本（Markdown） |
| `feedback.json` | `dimensions[id].descriptor_refs` | 评分依据描述语（列表） |
| `feedback.json` | `dimensions[id].confidence` | 置信度徽章/进度条 |
| `feedback.json` | `dimensions[id].evidence_span_ids` 数量 | 证据数量（可视化） |
| `feedback.json` | `provider` | 使用的模型标识 |
| `run_trace.json` | `run_id` | 运行溯源 ID |
| `run_trace.json` | `bundle_version` | 量规版本 |
| `run_trace.json` | `status` | 运行状态（成功/失败） |
| `run_trace.json` | `started_at` + `finished_at` | 总耗时 |
| `run_trace.json` | `terminal_validation_passed` | 校验通过标志 |
| `run_trace.json` | `node_traces[].node_type` + `status` + 耗时 | 执行流水线时间轴 |
| TSV | `rater1_trait1..6` / `rater2_trait1..6` | 人类评分对比 |
| 计算 | MAS - 人类均值 | 偏差表格 |

### 7.2 仅用于内部，前端不需要展示

| 数据 | 说明 |
|------|------|
| `EvidenceSpan` 对象 | 当前未持久化，仅内存中间结果 |
| `DimensionObservation` | 内部中间结构，打分前的证据汇总 |
| `ScoreHypothesis` | 每位评审员的原始评分假设，前端不需要 |
| `ConflictRecord` | 内部评审冲突记录 |
| `AdjudicationRecord` | 内部裁决记录 |
| `CoveragePlan` | 维度覆盖计划，纯内部 |
| `checkpoint` in node_traces | 断点续跑用，前端无需 |
| `input_ref` / `output_ref` in node_traces | 内部 storage key，前端无需 |
| `request_id` | 内部关联 ID |
| `replay_metadata` 完整对象 | 仅 `provider` 有展示价值 |

---

## 8. 单样本汇报页推荐最小数据模型

以下是前端实现一个完整"单样本汇报页"的最小数据模型，
数据全部来自 `feedback.json` + `run_trace.json` + TSV 人工评分：

```typescript
interface DimensionResult {
  id: string;               // dimension_id: "ideas_content" | "organization" | ...
  name: string;             // 英文全名: "Ideas and Content"
  score: number;            // 1-6，来自 canonical_score
  maxScore: number;         // 6
  confidence: number;       // 0-1，来自 confidence
  descriptors: string[];    // 评分依据描述语，来自 descriptor_refs
  feedbackText: string;     // LLM 生成反馈，来自 feedback_text（含 Markdown）
  evidenceCount: number;    // evidence_span_ids.length
  humanScore1: number | null;  // rater1_traitN，来自 TSV（可能缺失）
  humanScore2: number | null;  // rater2_traitN，来自 TSV（可能缺失）
}

interface RunMeta {
  runId: string;            // run_id
  bundleVersion: string;    // bundle_version
  status: string;           // "completed" | "failed" | "human_review"
  startedAt: string;        // ISO 8601
  finishedAt: string;       // ISO 8601
  durationSeconds: number;  // 计算得出
  validationPassed: boolean;// terminal_validation_passed
  provider: string;         // replay_metadata.provider
}

interface PipelineStep {
  nodeType: string;         // preprocess / extract / score / ...
  status: string;           // success / failed
  durationSeconds: number;  // 计算得出
  outputRef: string;        // 如 "spans:52"
}

interface SampleReport {
  essayId: string;          // 如 "20716"
  essayText: string;        // 原文全文（从 samples/ 读取）
  runMeta: RunMeta;
  pipeline: PipelineStep[];
  dimensions: DimensionResult[];  // 按固定顺序 I/O/V/W/S/C
  compositeScore: number | null;  // 可选，从 dimensions 计算
  compositeMax: number;     // 60
}
```

**数据填充来源汇总**：

| 字段路径 | 数据来源 | 字段映射 |
|---------|---------|---------|
| `essayId` | 目录名 | `artifacts/eval/20716/` → "20716" |
| `essayText` | `data/samples/sample_20716.txt` | 全文 |
| `runMeta.*` | `run_trace.json` 顶层字段 | 见第 3 节 |
| `pipeline[].nodeType` | `run_trace.json` → `node_traces[].node_type` | — |
| `pipeline[].durationSeconds` | `finished_at - started_at` | — |
| `pipeline[].outputRef` | `node_traces[].output_ref` | — |
| `dimensions[].score` | `feedback.json` → `dimensions[id].canonical_score` | — |
| `dimensions[].feedbackText` | `feedback.json` → `dimensions[id].feedback_text` | — |
| `dimensions[].descriptors` | `feedback.json` → `dimensions[id].descriptor_refs` | — |
| `dimensions[].humanScore1` | `data/training_set_8.tsv` → `rater1_traitN` | trait 编号见第 6 节 |
| `dimensions[].humanScore2` | `data/training_set_8.tsv` → `rater2_traitN` | — |
| `compositeScore` | 按权重公式计算 | `2×I + 2×O + 2×S + 4×C` |

---

## 附：关键文件路径速查

| 类型 | 路径 |
|------|------|
| 量规配置 | `configs/rubrics/asap_set8_baseline.yaml` |
| 复合分公式 | `configs/policies/aggregation/asap_set8_composite.yaml` |
| 样本文章 | `data/samples/sample_20716.txt` |
| 人工评分 | `data/training_set_8.tsv` |
| 真实运行产物 | `artifacts/eval/20716/feedback.json` |
| 真实运行追踪 | `artifacts/eval/20716/run_trace.json` |
| feedback 结构代码 | `src/agents/feedback.py` |
| span 结构定义 | `src/contracts/evidence.py` |
| trace 结构定义 | `src/contracts/trace.py` |
| QWK 计算 | `src/evaluation/qwk.py` |
| 评估导出 | `src/evaluation/export.py` |
