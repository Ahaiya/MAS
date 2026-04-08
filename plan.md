# 实施计划：代码对齐配置文件

## 原则

**配置文件（YAML）是已确定的真值，不得修改。代码需改动以适配配置。**
**dimension_relevance 步骤已废弃，全量 chunk 直接传给 evidence_extraction。**

---

## 现状诊断

通过对 `configs/` 全部 YAML 与 `src/` 代码的交叉比对，发现以下脱节：

### 脱节 1：Config 加载层 — bundle/rubric/policy 格式不兼容

| 配置文件 | 配置实际格式 | 代码期望格式 | 脱节点 |
|---------|------------|------------|-------|
| `bundle.yaml` | 扁平格式：`rubric.source`、`context.task`、`prompts.chunking` | `BundleFileSchema` 要求 `artifact_bundle` 包装对象，含 `rubric_core_ref`、`bundle_version` 等数十字段 | `resolver.py:104` 直接 `BundleFileSchema(**raw)` 报 ValidationError |
| `task_a4_rubric.yaml` | `dimensions[].code/name/anchors`，`scale.type/min/max/levels` | `RubricFileSchema` 要求 `rubric_core` 包装，`DimensionSchema` 要求 `dimension_id/scale_ref/observation_schema/evidence_requirements/levels[].rank/summary/descriptors` | `compiler.py:234` 读 `rubric_core` 键报 KeyError |
| `engineering_eval_adjudication.yaml` | 缺少 `policy_version/policy_name/description/output_policy`；`raters` 缺少 `optional_resolution_rater` | `AdjudicationFileSchema` + `AdjudicationPolicySchema(extra="forbid")` 严格校验 | ValidationError |
| `engineering_eval_aggregation.yaml` | 缺少 `policy_version/policy_name/description`；`outputs[]` 缺少 `description`；`composite_formula[]` 缺少 `description/formula_representation` | `AggregationFileSchema` 严格校验 | ValidationError |
| `engineering_eval_chunking.yaml` | `document_processing` 含 `chunk_size_hint/pre_split/material_strategies` | `ChunkingDocumentProcessingSchema` 要求 `target_chunk_size_hint`（不同字段名），无 `pre_split/material_strategies` | ValidationError |
| `task_a4_context.yaml` | 顶级 `material_context/score_anchors/scoring_context` | `ScoringContextFileSchema` 要求 `scoring_context: ScoringContextSchema(context_id, role_description, ...)` | 路由到错误 schema 或 ValidationError |

**结论**：当前代码无法加载任何一个配置文件。`ConfigCompiler.compile()` 在 Step 1 即失败。

### 脱节 2：Prompt 模板变量名不匹配

| 模板文件 | 模板要求的变量 | `prompt_builders.py` 注入的变量 | 缺失/多余 |
|---------|-------------|---------------------------|---------|
| `chunking.yaml` | `material_strategy`, `word_count`, `chunk_size_hint`, `normalized_text` | `document_type`, `word_count`, `normalized_text` | 缺 `material_strategy`、`chunk_size_hint`；多 `document_type` |
| `evidence_extraction.yaml` | `dimension_name`, `anchor_excellent`, `anchor_needs_improvement`, `evidence_focus`, `chunks` | `dimension_name`, `dimension_code`, `chunks`, `levels`, `facet_descriptions`, `minimum_evidence_units` | 缺 `anchor_excellent`、`anchor_needs_improvement`、`evidence_focus`；多 `dimension_code`、`levels`、`facet_descriptions`、`minimum_evidence_units` |
| `scoring.yaml` | `dimension_name`, `anchor_excellent`, `anchor_needs_improvement`, `evidence_focus`, `evidence_spans`, `score_anchors`, `calibration_notes`, `prior_rater_context` | `role_description`, `dimension_name`, `dimension_code`, `levels`, `facet_evidence`, `observation_confidence`, `uncertainty_notes`, `score_anchors`, `dataset_notes`, `calibration_notes`, `dimension_override_notes`, `prior_rater_context` | 缺 `anchor_excellent`、`anchor_needs_improvement`、`evidence_focus`、`evidence_spans`；多 `role_description`、`dimension_code`、`levels`、`facet_evidence`、`observation_confidence`、`uncertainty_notes`、`dataset_notes`、`dimension_override_notes` |
| `explanation.yaml` | `dimension_name`, `final_score`, `was_adjudicated`, `justification_1`, `justification_2`, `evidence_spans[].span_id/quote/support_type`, `evidence_focus`, `audience` | `dimension_name`, `dimension_code`, `canonical_score`, `display_annotation`, `descriptor_refs`, `facet_evidence`, `observation_confidence`, `uncertainty_notes`, `scorer_rationale`, `decision_note`, `was_adjudicated`, `dimension_override_notes`, `evidence_spans[].span_id/quote/source_type/source_label` | 缺 `final_score`、`justification_1`、`justification_2`、`evidence_focus`、`audience`；多 `dimension_code`、`canonical_score`、`display_annotation`、`descriptor_refs`、`facet_evidence`、`observation_confidence`、`uncertainty_notes`、`scorer_rationale`、`decision_note`、`dimension_override_notes` |

### 脱节 3：Task Context 未接入

`task_a4_context.yaml` 包含：
- `material_context.type = "conversation"` — 应推导 chunking 的 `material_strategy`
- `material_context.evidence_focus` — 应注入 extraction/scoring/explanation prompt
- `scoring_context[].calibration_notes` — 按维度 code 的校准提示，应注入 scoring prompt

当前代码完全未消费这些字段。`compiler.py:266` 只取 `scoring_context_file_data.get("scoring_context")`，而 `task_a4_context.yaml` 的 `scoring_context` 是列表 `[{code, calibration_notes}]`，不是 `ScoringContextSchema` 对象。

### 脱节 4：feedback 未解析 JSON

`explanation.yaml` 模板要求返回 `{"feedback": "..."}` JSON，但 `feedback.py:104` 直接 `response.content.strip()` 作为文本，不解析 JSON。

---

## 已完成的清理

- [x] 删除 `src/agents/coverage.py`
- [x] 删除 `configs/prompts/dimension_relevance.yaml`（git status 已标记 D）
- [x] 删除 `tests/unit/agents/test_coverage_llm.py`
- [x] `runner.py` 中 coverage 阶段替换为全量 full_scan CoveragePlan
- [x] `scripts/eval.py` 移除 dimension_relevance 模板加载
- [x] `batch_runner.py` 移除 dimension_relevance 模板加载
- [x] `tests/e2e/test_real_provider_smoke.py` 移除 coverage 依赖

---

## 模块改动清单

### 第 1 阶段：Config 加载层（前置，阻塞一切）

#### 1-1. `src/config/schema.py` — 新增简化 Schema 类

**问题**：所有现有 schema 都使用 `extra="forbid"` 严格模式，且字段名/结构与配置文件实际格式不匹配。配置文件无法通过现有 schema 校验。

**改动**：新增以下宽松 schema（保留旧 schema 不动，新旧并存）：

```python
class SimplifiedBundleFileSchema(BaseModel):
    schema_version: str
    bundle_id: str
    active_task_id: str
    rubric: dict       # {source, task}
    context: dict      # {task}
    prompts: dict      # {chunking, evidence_extraction, scoring, explanation}
    policies: dict     # {chunking, adjudication, aggregation}

class TaskRubricFileSchema(BaseModel):
    schema_version: str
    task_id: str
    task_name: str
    indicator_description: str
    scale: dict        # {type, min, max, levels}
    dimensions: list   # [{code, name, anchors}]

class TaskContextFileSchema(BaseModel):
    schema_version: str
    material_context: dict   # {type, description, evidence_focus}
    score_anchors: list = []
    human_instructions: str = ""
    scoring_context: list = []  # [{code, calibration_notes}]

class SimplifiedAdjudicationFileSchema(BaseModel):
    schema_version: str
    adjudication_policy: dict

class SimplifiedAggregationFileSchema(BaseModel):
    schema_version: str
    aggregation_policy: dict

class SimplifiedChunkingPolicyFileSchema(BaseModel):
    schema_version: str
    chunking_policy: dict
```

**测试**：每个 schema 对应配置 YAML 文件 `Schema(**yaml.safe_load(file))` 通过。

---

#### 1-2. `src/config/resolver.py` — 支持简化 bundle 格式

**问题**：`load_bundle_file()` 硬编码 `BundleFileSchema`，对简化格式 bundle 报 ValidationError。路径模板 `{active_task_id}` 未替换。

**改动**：
- `load_bundle_file()` 检测 YAML 是否含 `artifact_bundle` 字段；若无则走简化格式路径：
  1. 用 `SimplifiedBundleFileSchema` 校验
  2. 将 `{active_task_id}` 替换为实际值
  3. 从 `prompts` 字段构造 `prompt_refs`（4 个模板路径）
  4. 从 `policies` 字段构造 `chunking_policy_ref`、`adjudication_policy_ref`、`aggregation_policy_ref`
  5. 从 `context.task` 构造 `scoring_context_ref`
  6. `explanation_policy_ref` 置 None（bundle 未声明）
  7. 构建 `ArtifactBundle` 返回，填充 `bundle_id/bundle_version/bundle_name/description` 默认值
- 更新 `_SCHEMA_ROUTE`：
  - `rubrics/tasks/` → `TaskRubricFileSchema`
  - `tasks/` → `TaskContextFileSchema`
  - `policies/adjudication/` → `SimplifiedAdjudicationFileSchema`
  - `policies/aggregation/` → `SimplifiedAggregationFileSchema`
  - `policies/chunking/` → `SimplifiedChunkingPolicyFileSchema`

**测试**：`ConfigResolver().load_bundle_file("configs/bundles/engineering_eval_baseline.bundle.yaml")` 成功返回 `ArtifactBundle`，各 ref 路径正确。

---

#### 1-3. `src/config/compiler.py` — 支持任务量规格式 + 简化 policy

**问题**：`_build_rubric_snapshot()` 从 `rubric_core` 读取，而任务量规无 `rubric_core`。`_build_policy_snapshot()` 读 `scoring_context` 时假设其为 dict，而实际为 list。`explanation_policy_ref` 为 None 时 `load_artifact()` 报错。

**改动**：

`_build_rubric_snapshot()` 分支：
- 检测有无 `rubric_core` 字段；若无则走任务量规格式：
  - `dimensions[].code`（如 `"A4-1"`）→ `dimension_id = code.lower().replace("-", "_")`（如 `"a4_1"`）
  - `dimensions[].anchors` → `levels`：`{rank: score, summary: anchor_text, descriptors: [anchor_text]}`
  - `scale` → 构建 `scale_id = f"ordinal_{min}_{max}"`，`ScaleSchema` 兼容结构
  - 构建 `observation_schema.required_facets = [dimension_id]`
  - 构建 `evidence_requirements = {"minimum_evidence_units": 1}`

`_build_policy_snapshot()` 修改：
- `scoring_context_file_data` 透传整个 dict（含 `material_context`、`scoring_context` 列表、`score_anchors`），不再假设其为 `ScoringContextSchema` 格式
- `explanation_policy_ref` 为 None 时跳过 `load_artifact()`，`exp_file_data` 默认 `{"explanation_policy": {}}`

**测试**：`ConfigCompiler().compile("configs/bundles/engineering_eval_baseline.bundle.yaml")` 返回 `ResolvedArtifactBundle`，`rubric_snapshot.dimensions` 含 3 个维度 `a4_1/a4_2/a4_3`，`policy_snapshot.scoring_context` 含 `material_context` 和 `scoring_context` 列表。

---

### 第 2 阶段：Chunking（文档切分）

#### 2-1. `src/agents/chunker.py` — 注入 `material_strategy` 和 `chunk_size_hint`

**问题**：`_render_chunking_prompt()` 注入 `document_type`，但模板要求 `material_strategy`（策略描述文本）和 `chunk_size_hint`。渲染时报 `UndefinedError: 'material_strategy' is undefined`。

**改动**：
- `run()` 签名增加 `chunking_policy: Optional[dict] = None`
- `_render_chunking_prompt()` 签名增加 `material_strategy: str` 和 `chunk_size_hint: str`
- 注入逻辑：
  - 从 `chunking_policy["document_processing"]["material_strategies"]` 按 `document_type` 查找策略文本赋给 `material_strategy`
  - 从 `chunking_policy["document_processing"]["chunk_size_hint"]` 读取赋给 `chunk_size_hint`
  - 回落：若 policy 为空则 `material_strategy = document_type`，`chunk_size_hint = ""`
- 移除 context 中的 `document_type`（模板无此变量）

**涉及文件**：`src/agents/chunker.py`

**测试**：
- 构造含 `material_strategies = {"conversation": "按对话轮次..."}` 的 policy，验证渲染后 prompt 含该文本
- 验证 `chunk_size_hint` 正确注入
- 验证 `document_type` 不在 context 中

---

#### 2-2. `src/pipeline/runner.py` — 传递 `chunking_policy` 给 chunker

**问题**：`runner.py` 调用 `chunker.run()` 时未传 `chunking_policy`。

**改动**：`chunker.run()` 调用处传入 `chunking_policy=chunking_policy`（runner 中已有此变量）。同时传入 `document_type` 推导所需的 `material_context.type`（从 `scoring_context` 读取）。

**测试**：runner 调用 chunker 时不报错，prompt 中含正确策略文本。

---

### 第 3 阶段：Evidence Extraction（证据抽取）

#### 3-1. `src/agents/prompt_builders.py` — `build_extraction_prompt()` 对齐模板变量

**问题**：模板要求 `anchor_excellent`、`anchor_needs_improvement`、`evidence_focus`；代码注入 `levels`、`facet_descriptions`、`minimum_evidence_units`、`dimension_code`。Jinja2 StrictUndefined 报 `UndefinedError`。

**改动**：
- 从 rubric dimension 的 `levels` 中提取：
  - `anchor_excellent` = `rank == scale_max` 的 level 的 summary
  - `anchor_needs_improvement` = `rank == scale_min` 的 level 的 summary
- 签名增加 `evidence_focus: str = ""`
- 移除 `dimension_code`、`levels`、`facet_descriptions`、`minimum_evidence_units`
- 保留 `dimension_name`、`chunks`

**涉及文件**：`src/agents/prompt_builders.py`（`build_extraction_prompt()`）

**测试**：
- mock rubric 含 levels（rank=5 summary="优秀", rank=1 summary="待改进"），验证 `anchor_excellent = "优秀"`
- 验证 `evidence_focus` 出现在渲染后 prompt

---

#### 3-2. `src/agents/extractor.py` — 透传 `evidence_focus`

**改动**：`run()` 签名增加 `evidence_focus: str = ""`，透传至 `build_extraction_prompt()`。

**测试**：验证 `evidence_focus` 出现在 prompt 中。

---

### 第 4 阶段：Scoring（评分）

#### 4-1. `src/agents/prompt_builders.py` — `build_scoring_prompt()` 对齐模板变量

**问题**：模板要求 `anchor_excellent`、`anchor_needs_improvement`、`evidence_focus`、`evidence_spans[].span_id/chunk_id/quote/support_type`、`score_anchors`、`calibration_notes`、`prior_rater_context`。代码注入 `role_description`、`dimension_code`、`levels`、`facet_evidence`、`observation_confidence` 等。

**改动**：
- 同 3-1 方式提取 `anchor_excellent`、`anchor_needs_improvement`
- 签名增加 `evidence_focus: str = ""`
- 构造 `evidence_spans` 扁平列表：从 `observation.facet_findings` 展平所有 span_id，查找 `span_by_id` 组装 `{span_id, chunk_id, quote, support_type}`
- `calibration_notes`：从 `scoring_context` 参数中按维度 code 查找 per-dimension `calibration_notes`（`scoring_context["scoring_context"]` 列表中 `code == dim_code` 的项），再回落全局
- `score_anchors`：从 `scoring_context.get("score_anchors", [])` 读取
- 移除 `role_description`、`dimension_code`、`levels`、`facet_evidence`、`observation_confidence`、`uncertainty_notes`、`dataset_notes`、`dimension_override_notes`
- 保留 `dimension_name`、`prior_rater_context`

**涉及文件**：`src/agents/prompt_builders.py`（`build_scoring_prompt()`）

**测试**：
- 验证 `evidence_spans` 为扁平列表，包含 `span_id/chunk_id/quote/support_type`
- 验证 `calibration_notes` 按维度 code 正确匹配
- 验证仲裁路径（`prior_rater_context` 非空）渲染正确

---

#### 4-2. `src/agents/scorer.py` — 透传 `evidence_focus`

**改动**：`run()` 签名增加 `evidence_focus: str = ""`，透传至 `build_scoring_prompt()`。

**测试**：验证 `evidence_focus` 出现在 prompt 中。

---

### 第 5 阶段：Explanation / Feedback（反馈生成）

#### 5-1. `src/agents/prompt_builders.py` — `build_explanation_prompt()` 对齐模板变量

**问题**：模板要求 `final_score`、`justification_1`、`justification_2`、`evidence_spans[].span_id/quote/support_type`、`evidence_focus`、`audience`、`was_adjudicated`。代码注入 `canonical_score`、`display_annotation`、`descriptor_refs`、`facet_evidence`、`observation_confidence`、`scorer_rationale`、`decision_note` 等。

**改动**：
- `final_score` = `decision.final_score.canonical_score`
- 签名增加 `hypotheses: Optional[List[ScoreHypothesis]] = None`、`evidence_focus: str = ""`、`audience: str = "evaluator"`
- `justification_1` / `justification_2`：
  - `was_adjudicated=True` 时：`justification_1` = rater_3 的 rationale
  - `was_adjudicated=False` 时：`justification_1` = rater_1 rationale，`justification_2` = rater_2 rationale
  - 从 `hypotheses` 中按 `dimension_id` 筛选
- `evidence_spans` 扁平列表：`[{span_id, quote, support_type}]`
- 移除 `dimension_code`、`canonical_score`、`display_annotation`、`descriptor_refs`、`facet_evidence`、`observation_confidence`、`uncertainty_notes`、`scorer_rationale`、`decision_note`、`dimension_override_notes`

**涉及文件**：`src/agents/prompt_builders.py`（`build_explanation_prompt()`）

**测试**：
- 验证 `was_adjudicated=True` 时 `justification_1` 来自 rater_3
- 验证 `was_adjudicated=False` 时 `justification_1/2` 来自 rater_1/rater_2
- 验证 `evidence_focus` 和 `audience` 出现在 prompt

---

#### 5-2. `src/agents/feedback.py` — 解析 JSON 响应 + 传递新参数

**问题**：
1. 模板返回 `{"feedback": "..."}` JSON，但 `_render_commentary()` 直接取 `response.content.strip()` 不解析 JSON
2. `build_explanation_prompt()` 新增参数未传递

**改动**：
- `_render_commentary()` 尝试 `json.loads(response.content)` 提取 `feedback` 字段；解析失败则回落原始文本
- `_render_commentary()` 和 `run()` 签名增加 `hypotheses`、`evidence_focus`、`audience`，透传至 `build_explanation_prompt()`

**涉及文件**：`src/agents/feedback.py`

**测试**：
- 验证 JSON `{"feedback":"文本"}` 正确解析
- 验证非 JSON 时回落到原始文本
- 验证新参数透传正确

---

### 第 6 阶段：Runner 集成

#### 6-1. `src/pipeline/runner.py` — 从 task context 提取参数并透传各阶段

**问题**：`policy_snapshot.scoring_context` 已包含 `task_a4_context.yaml` 数据（经 1-3 改造后），但 runner 未提取 `evidence_focus` 等字段传给各 agent。

**改动**：
- 在 `run()` 开始处提取：
  ```python
  task_ctx = policy_snapshot.scoring_context or {}
  material_ctx = task_ctx.get("material_context", {})
  evidence_focus = str(material_ctx.get("evidence_focus", ""))
  material_type = str(material_ctx.get("type", "unknown"))
  ```
- `chunker.run()` 传入 `chunking_policy=chunking_policy`
- `extractor.run()` 传入 `evidence_focus=evidence_focus`
- `scorer.run()` 传入 `evidence_focus=evidence_focus`（通过 `scoring_context` 或单独参数）
- `feedback.run()` 传入 `evidence_focus=evidence_focus`、`audience="evaluator"`、`hypotheses=hypotheses`

**涉及文件**：`src/pipeline/runner.py`

**测试**：端到端 `python scripts/eval.py "data/training/1组-xxx.md"`：
- 全流水线无 `UndefinedError`
- prompt 中出现 `evidence_focus` 文本
- feedback 返回合法 JSON 并解析成功

---

## 实施顺序

```
Phase 1: Config 加载层（阻塞后续所有阶段）
  1-1 schema.py 新增简化 schema
  → 1-2 resolver.py 支持简化 bundle 格式
  → 1-3 compiler.py 支持任务量规格式 + 简化 policy
  验收：ConfigCompiler.compile(bundle) 成功，rubric 含 a4_1/a4_2/a4_3

Phase 2: Chunking
  2-1 chunker.py 注入 material_strategy + chunk_size_hint
  → 2-2 runner.py 传 chunking_policy 给 chunker
  验收：chunker.run() 不报 UndefinedError，prompt 含正确策略文本

Phase 3: Evidence Extraction
  3-1 prompt_builders.build_extraction_prompt() 对齐模板变量
  → 3-2 extractor.run() 透传 evidence_focus
  验收：extractor.run() 渲染成功，evidence_focus 出现在 prompt

Phase 4: Scoring
  4-1 prompt_builders.build_scoring_prompt() 对齐模板变量
  → 4-2 scorer.run() 透传 evidence_focus
  验收：scorer.run() 渲染成功，calibration_notes 按维度命中

Phase 5: Explanation / Feedback
  5-1 prompt_builders.build_explanation_prompt() 对齐模板变量
  → 5-2 feedback.py 解析 JSON + 传入新参数
  验收：feedback 返回正确文本，adjudicated 路径正确

Phase 6: Runner 集成
  6-1 runner.py 提取 evidence_focus，透传到各阶段
  验收：端到端 eval.py 单样本完整跑通
```

---

## 测试要求汇总

| 阶段 | 测试类型 | 关键验证点 |
|------|---------|---------|
| Phase 1 | Schema 验证 | 每个 YAML 通过对应简化 schema；compile() 返回含 3 维度的 RubricSnapshot |
| Phase 2 | 单元 | `material_strategy` 从 policy 正确查找；`chunk_size_hint` 注入 |
| Phase 3 | 单元 | `anchor_excellent` = rank=5 的 summary；`evidence_focus` 在 prompt 中 |
| Phase 4 | 单元 | `evidence_spans` 为扁平列表；`calibration_notes` 按维度 code 匹配 |
| Phase 5 | 单元 | JSON `{"feedback":"..."}` 正确解析；`justification_1/2` 来源正确 |
| Phase 6 | 端到端 | 单样本跑通，无 UndefinedError，产出 feedback.json |

---

## 不在此计划范围

- 修改任何 configs/ 下的文件
- 外环（outer_loop）代码
- 新增评估探针
- 多维度（B1/C2/F2）扩展
