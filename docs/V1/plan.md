# MAS V1 内环改造执行清单

> 状态标记：`[ ]` 待执行 | `[x]` 已完成 | `[-]` 进行中 | `[~]` 已跳过/已废弃
>
> 本文件记录 Preprocess + Coverage LLM 化改造的全部子任务。
> 完整背景与设计决策见 `docs/V1/MAS_V1_DESIGN.md` 第零章和第七章。
> 会话交接说明见 `docs/V1/INNER_LOOP_REVIEW.md`。
>
> **架构铁律（所有阶段均须遵守）**：
> - 外环可修改的参数 → 必须在 `configs/` 中有对应表示（不允许硬编码魔法数字）
> - 外环用于诊断的数据 → 必须保存在 `artifacts/` 中（不允许运行时丢弃）
> - 内环建立机制，外环调节参数，两者通过 configs/artifacts 解耦

---

## 已完成的前置修复

- [x] `coverage.py`：`minimum_evidence_units` 改为读量规配置，不再用 `max(1, len(facets))`
- [x] `preprocess.py`：正则扩展支持中文句末标点 `。！？`（规则逻辑现已迁移至 `deterministic_chunker.py`）
- [x] `runner.py`：新增 `last_spans` / `last_observations` 属性
- [x] `eval.py`：每次评估保存 `evidence_spans.json` + `observations.json`
- [x] 回归测试：965 passed, 4 skipped（全部通过）

---

## 阶段 A：合约层扩展

> 目标：在现有 contracts 中增加新字段，向下兼容，不破坏任何现有测试。
> 文件：`src/contracts/request_models.py`

- [x] **A1** `TextUnit` 增加字段：
  - `chunk_title: Optional[str] = None`（LLM 分块时的语义标题）
  - `chunk_method: str = "rule"`（取值：`"rule"` | `"llm_semantic"` | `"llm_hierarchical"`）
  - 同步更新 `to_dict()` / `from_dict()`（allowed 集合 + 反序列化逻辑）

- [x] **A2** `NormalizedDocument` 增加字段：
  - `document_type: str = "unknown"`（取值：`"essay"` | `"report"` | `"dialogue"` | `"unknown"`）
  - `token_estimate: int = 0`（预估 token 数，用于长文档判断）
  - 同步更新 `to_dict()` / `from_dict()`

- [x] **A3** `CoveragePlan` 增加字段：
  - `relevance_scores: Dict[str, float] = field(default_factory=dict)`（chunk_id → 相关性分数，full_scan 时为空 dict）
  - 同步更新 `to_dict()` / `from_dict()`

- [x] **A4** `DimensionObservation`（`src/contracts/evidence.py`）增加字段：
  - `coverage_miss_span_ids: List[str] = field(default_factory=list)`
  - 含义：Extractor 找到的 span 的 unit_id **不在**该维度 target_unit_ids 里时记录
  - 用途：支持外环计算 `coverage_recall_rate`
  - 同步更新 `to_dict()` / `from_dict()`

- [x] **A5** 验证：运行全套测试，确认 965 passed

---

## 阶段 B：Prompt 模板设计

> 目标：定义两个新的 Jinja2 prompt 模板。
> 新建文件：`configs/prompts/chunking.yaml`、`configs/prompts/dimension_relevance.yaml`

- [x] **B1** 创建 `configs/prompts/chunking.yaml`
  - `template_id: "document_chunking_v1"`
  - system：文档结构分析专家角色，说明分块要求（语义完整、边界清晰、100-500词/块）
  - user：接收 `{{ document_type }}`、`{{ word_count }}`、`{{ normalized_text }}`
  - 输出格式：`{"chunks": [{"id": "c0", "title": "...", "text": "..."}, ...]}`

- [x] **B2** 创建 `configs/prompts/dimension_relevance.yaml`
  - `template_id: "dimension_relevance_v1"`
  - system：写作评价专家角色，判断块与维度的相关性
  - user：接收 `{{ dimension_name }}`、`{{ dimension_description }}`、`{{ required_facets }}`、`{{ chunks }}`（id+title+首句）、`{{ top_k }}`
  - 输出格式：`{"relevant_chunk_ids": ["c2", "c5", "c1"]}`
  - **设计要求**：模板变量支持 per-dimension 覆盖（外环可为单个维度提供不同的 facet 强调描述）

- [x] **B3** 验证：用 `PromptLoader` 加载两个模板，确认可渲染无语法错误

---

## 阶段 C：Chunker Agent 实现

> 目标：LLM 语义分块，替代 preprocess.py 的分句逻辑。
> 新建文件：`src/agents/chunker.py`、`src/agents/deterministic_chunker.py`

- [x] **C1** 新建 `src/agents/chunker.py`，接口：
  ```python
  def run(
      request: EvaluationRequest,
      provider: Optional[BaseProvider] = None,
      template: Optional[PromptTemplate] = None,
      token_threshold: int = 4000,
  ) -> Tuple[NormalizedRequest, NormalizedDocument]:
  ```

- [x] **C2** 实现 token 估算函数：
  - `_estimate_tokens(text) -> int`
  - 英文：`len(text.split()) * 1.3`；中文（检测中文字符占比）：`len(text) * 1.5`

- [x] **C3** 实现常规文档分支（token < threshold）：
  - 渲染 `chunking` 模板 → LLM 调用（Call 1）
  - 解析 JSON `chunks[]` → 构造 `TextUnit[]`（`chunk_method="llm_semantic"`）
  - 解析失败时降级到规则分块（调用 deterministic_chunker）

- [x] **C4** 实现长文档分支（token >= threshold）：
  - Step A：按 ~1500 词硬切 → 每块生成首句摘要（截取前 2 句）
  - Step B：渲染 `chunking` 模板（传入摘要列表而非全文）→ LLM 调用（Call 1）
  - 构造 `TextUnit[]`（`chunk_method="llm_hierarchical"`）

- [x] **C5** 新建 `src/agents/deterministic_chunker.py`：
  - 承载规则分块逻辑（原 `preprocess.py` 逻辑已迁移）
  - 作为 mock 模式的降级实现，保持与改造前规则路径行为一致

- [x] **C6** 验证：
  - 短文本 mock provider → 合法 `NormalizedDocument`，`chunk_method="llm_semantic"`
  - 长文本（>4000 token）→ 走层次化分支，`chunk_method="llm_hierarchical"`
  - LLM 返回格式错误 → 降级到规则分块，不抛异常

---

## 阶段 D：Coverage Agent LLM 化

> 目标：在 coverage.py 增加 LLM 维度相关性预筛路径，保留 full_scan 作为降级路径。
> 修改文件：`src/agents/coverage.py`

- [x] **D1** 扩展 `run()` 接口，新增参数：
  ```python
  def run(
      document: NormalizedDocument,
      rubric: RubricSnapshot,
      provider: Optional[BaseProvider] = None,
      template: Optional[PromptTemplate] = None,
      chunking_policy: Optional[dict] = None,  # 来自 policy_snapshot，含 per_dimension_top_k
  ) -> List[CoveragePlan]:
  ```
  - `top_k` 不再从调用方直接传入，改为从 `chunking_policy.coverage.per_dimension_top_k` 读取
  - 遵循"可调量来自 configs/"原则

- [x] **D2** 实现 LLM 模式（provider 不为 None）：
  - 为每个 TextUnit 构造摘要条目（id + title + first_sentence）
  - 用 `ThreadPoolExecutor` 并行发出 6 次 LLM 调用（每维度一次）
  - 每次调用渲染 `dimension_relevance` 模板
  - 解析 `relevant_chunk_ids` → 过滤 `target_unit_ids`
  - 计算 `relevance_scores = {chunk_id: 1/(rank+1)}`（倒数排名分）

- [x] **D3** 实现失败兜底：
  - 任意维度 LLM 调用失败（解析错误/超时）→ 该维度降级为 full_scan
  - 不影响其他维度的结果
  - 失败信息记录到 CoveragePlan 的 relevance_scores 中（空 dict）

- [x] **D4** mock 模式（provider 为 None）：
  - 保留现有 full_scan 逻辑，`relevance_scores = {}`
  - 确保全套 mock 回归测试通过

- [x] **D5** 验证：
  - mock 模式：target_unit_ids = 全部（与现有行为一致）
  - LLM 模式 + mock provider：target_unit_ids 被过滤到 top_k 个
  - 某维度失败：该维度 full_scan，其余正常

---

## 阶段 E：Runner 整合

> 目标：将 chunker + 更新后的 coverage 接入主流水线。
> 修改文件：`src/pipeline/runner.py`

- [x] **E1** `PipelineRunner.__init__` 增加参数：
  - `stage_providers` 新增识别键：`"chunking"` / `"coverage_planning"`
  - `prompt_templates` 新增识别键：`"chunking"` / `"dimension_relevance"`
  - **不新增** `token_threshold` / `coverage_top_k` 作为构造参数——这些值改为从 `bundle.policy_snapshot.chunking_policy` 读取，遵循"可调量来自 configs/"原则

- [x] **E2** Stage 1 替换：
  - LLM 模式：调用 `chunker.run(request, provider, template, token_threshold)`
  - mock 模式：调用 `deterministic_chunker.run(request)`
  - 更新 `store.record_node_success` 的 `output_ref`，增加 chunk 数量和方法信息

- [x] **E3** Stage 2 更新：
  - LLM 模式：`coverage.run(document, rubric, provider, template, chunking_policy)`
  - mock 模式：`coverage.run(document, rubric)`（不传 provider）
  - 更新 `output_ref` 反映过滤后的 target_unit 数量

- [x] **E4** 追加修正记录（runner.py 文件头注释）

- [x] **E5** 验证：全套 mock 回归测试 976 passed, 4 skipped（mock 路径行为不变）

---

## 阶段 F：Bundle 配置 + eval.py 适配

> 目标：把新 provider 和 prompt 模板接入配置体系。

- [x] **F1** 新建 `configs/policies/chunking/asap_set8_chunking.yaml`：
  ```yaml
  chunking_policy:
    policy_id: "asap_set8_chunking_v1"
    document_processing:
      token_threshold: 4000
      target_chunk_size_hint: "100-500 words"
    coverage:
      default_top_k: 5
      fallback_to_full_scan_on_error: true
      per_dimension_top_k:
        ideas_content: 5
        organization: 4
        voice: 3
        word_choice: 4
        sentence_fluency: 4
        conventions: 6
  ```
  - 这是外环调节 top_k / token_threshold 的唯一入口（遵循"可调量来自 configs/"原则）

- [x] **F2** 修改 `configs/bundles/asap_set8_baseline.bundle.yaml`：
  - `prompt_templates` 增加 `"prompts/chunking.yaml"` / `"prompts/dimension_relevance.yaml"`
  - `stage_providers` 增加 `chunking` / `coverage_planning` 条目（model 用 Haiku）
  - 增加 `chunking_policy_ref` + `chunking_source_file` 引用新建的 chunking policy

- [x] **F3** 更新 `src/config/schema.py`（bundle schema Pydantic 定义）：
  - 新增 `chunking_policy_ref` / `chunking_source_file` 字段（Optional）
  - `stage_providers` 支持 `chunking` / `coverage_planning` 键

- [x] **F4** 更新 `src/config/resolver.py`（配置解析器）：
  - 加载 chunking policy 并挂载到 `policy_snapshot.chunking_policy`

- [x] **F5** 修改 `scripts/eval.py`：
  - `_load_prompt_templates()` 增加 `chunking.yaml` / `dimension_relevance.yaml`
  - 终端输出增加：`chunks:N (method)` / `coverage: N→K`

- [x] **F6** 验证：`python scripts/eval.py --essay-id 20716 --mock-provider --no-verbose`，产出文件包含新字段

---

## 阶段 G：测试补充

> 目标：补充新模块单元测试，更新集成测试。

- [x] **G1** 新建 `tests/unit/agents/test_chunker.py`：
  - `test_mock_mode_produces_valid_document`
  - `test_short_doc_calls_llm_once`（token < 4000，仅 Call 1）
  - `test_long_doc_uses_hierarchical_branch`（token >= 4000）
  - `test_llm_json_parse_failure_falls_back_to_rules`
  - `test_chunk_title_populated_in_text_units`
  - `test_token_estimate_chinese_text`（中文字符权重正确）

- [x] **G2** 新建 `tests/unit/agents/test_coverage_llm.py`：
  - `test_full_scan_when_no_provider`
  - `test_llm_filters_target_unit_ids`
  - `test_failed_dimension_falls_back_to_full_scan`
  - `test_relevance_scores_populated`
  - `test_parallel_calls_all_dimensions`

- [x] **G3** 更新 `tests/integration/test_mock_pipeline.py`：
  - 确认 mock 模式下 Stage 1/2 行为与改造前一致
  - 确认 `NormalizedDocument` / `CoveragePlan` / `DimensionObservation` 新字段有合法默认值

- [x] **G4** 新建 `tests/unit/config/test_chunking_policy.py`：
  - 确认 `asap_set8_chunking.yaml` 可被 resolver 正确加载
  - 确认 `policy_snapshot.chunking_policy.coverage.per_dimension_top_k` 可按维度读取

- [x] **G5** 全套回归：`python -m pytest tests/ --override-ini="addopts=" -q`，确认无退化（1019 passed, 11 skipped）

---

## 阶段 H：外环度量指标补充

> 目标：实现三个 Preprocess+Coverage 专项质量指标，供外环 Measure 阶段读取。
> 新建文件：`scripts/compute_coverage_metrics.py`

- [x] **H1** 实现 `coverage_recall_rate`：
  - 遍历 `evidence_spans.json`，检查每个 span 的 `unit_id` 是否在对应维度的 `target_unit_ids` 中
  - `target_unit_ids` 来源：从 `observations.json` 中的 `coverage_miss_span_ids` 反推
  - 输出：per-dimension 召回率 + 整体均值

- [x] **H2** 实现 `coverage_precision_rate`：
  - 统计每个维度的 `target_unit_ids` 中，实际贡献了 span 的 unit_id 比例
  - 需要 `evidence_spans.json`（span 的 unit_id）+ `observations.json`（target_unit_ids）

- [x] **H3** 实现 `chunk_boundary_quality`：
  - 检查每个 span 的 `text_quote` 是否完整落在某个 TextUnit 的 `text` 字段中（子串检查）
  - 输出：跨块 span 比率（低 = 分块质量好）

- [x] **H4** 将三个指标输出纳入 `compute_qwk.py` 的汇总报告，或单独输出到 `artifacts/eval/*/coverage_metrics.json`
  - 已采用单独输出路径：新增 `scripts/compute_coverage_metrics.py`，每篇生成 `coverage_metrics.json`

- [x] **H5** 验证：用已有样本（essay 20716 等）运行，确认指标可正常计算
  - `python scripts/eval.py --essay-id 20716 --mock-provider --no-verbose`
  - `python scripts/compute_coverage_metrics.py --essay-id 20716`

---

## 阶段 I：Extraction Agent 改造

> **前置假设**：阶段 A-H 均已按计划完成。Preprocess 产出语义 chunk（含 chunk_title/chunk_method），
> Coverage 产出过滤后的 CoveragePlan（target_unit_ids 仅含相关 chunk，relevance_scores 已填充）。
>
> **改造目标**：
> 1. Extractor 消费 Coverage 成果——只将 target_unit_ids 对应的 chunk 传给 LLM，而非全文
> 2. LLM 不再输出 offset——代码通过 quote 文本匹配回填 offset 和 unit_id
> 3. Prompt 上下文丰富化——传入完整分档描述符和 facet 语义描述
> 4. 支持 per-dimension prompt override——外环可为单个维度定制不同的 extraction 指令
>
> **遵循原则**：
> - 外环可调的 prompt → `configs/prompts/`
> - extraction_note 等诊断信息 → 落盘到 `artifacts/`
> - 代码中不硬编码任何维度名、facet 名、evidence 数量要求

---

### I1：quote 文本匹配工具

> 新建 `src/utils/quote_matcher.py`
> 用途：LLM 返回 quote 后，在原文中定位精确位置，回填 start_offset / end_offset / unit_id。

- [x] **I1.1** 实现 `match_quote()` 函数：
  ```python
  def match_quote(
      quote: str,
      normalized_text: str,
      text_units: List[TextUnit],
  ) -> QuoteMatchResult:
      """
      在 normalized_text 中定位 quote，返回匹配结果。
  
      Returns:
          QuoteMatchResult(
              start_offset: Optional[int],
              end_offset: Optional[int],
              unit_id: Optional[str],       # quote 主要落在哪个 TextUnit
              match_method: str,            # "exact" | "normalized" | "fuzzy" | "unmatched"
              confidence: float,            # 0.0-1.0，unmatched 时为 0.0
          )
      """
  ```

- [x] **I1.2** 实现三级匹配策略：
  - **Level 1 — exact**：`normalized_text.find(quote)`，精确子串匹配
  - **Level 2 — normalized**：双方做空白符归一化（连续空白→单空格、strip）后再匹配
  - **Level 3 — fuzzy**：滑动窗口 + 字符级相似度（`SequenceMatcher`），阈值 ≥ 0.85 视为命中
  - 三级依次尝试，命中即停

- [x] **I1.3** 实现 `_locate_unit_id()` 辅助函数：
  - 给定 `(start_offset, end_offset)` 和 `text_units`，找到**重叠最多**的 TextUnit 的 unit_id
  - 若 span 横跨多个 unit，返回重叠字符数最多的那个（供外环 `chunk_boundary_quality` 指标使用）

- [x] **I1.4** 定义 `QuoteMatchResult` dataclass（frozen=True），放在同一文件中
  - 字段：`start_offset`, `end_offset`, `unit_id`, `match_method`, `confidence`

- [x] **I1.5** 单元测试 `tests/unit/utils/test_quote_matcher.py`：
  - `test_exact_match`：quote 是原文的精确子串
  - `test_normalized_match`：quote 与原文仅空白符差异
  - `test_fuzzy_match`：quote 与原文有少量字符差异（LLM 改写/截断）
  - `test_unmatched`：quote 完全不在原文中
  - `test_locate_unit_id`：span 横跨两个 unit 时返回重叠最大的
  - `test_chinese_text`：中文文本的匹配

---

### I2：Prompt 模板重写

> 重写 `configs/prompts/evidence_extraction.yaml`
> 新增 per-dimension override 机制

- [x] **I2.1** 重写 `configs/prompts/evidence_extraction.yaml`，变更要点：
  - **输入变量变更**：
    - 新增 `{{ chunks }}`：chunk 列表，每个含 `id`、`title`、`text`（替代原 `{{ essay_text }}`）
    - 新增 `{{ levels }}`：该维度的完整分档描述符列表（rank + summary + descriptors）
    - 新增 `{{ facet_descriptions }}`：facet 语义描述列表（facet_id + 描述文本）
    - 保留 `{{ dimension_name }}`、`{{ dimension_code }}`
    - 移除 `{{ essay_text }}`（改为 chunks 传入）
    - 移除 `{{ trait_description }}`（改为完整 levels 传入）
  - **输出格式变更**：
    ```json
    {
      "evidence_spans": [
        {
          "quote": "exact text from the document",
          "chunk_id": "c2",
          "facets": ["clarity_focus", "main_idea_salience"],
          "support_type": "supporting|counter|neutral"
        }
      ]
    }
    ```
    - 移除 `start_offset` / `end_offset`（由 quote_matcher 回填）
    - 新增 `chunk_id`（LLM 回传所引用的 chunk 标识，辅助 unit_id 定位）
  - **指令优化**：
    - 要求 LLM 的 quote 必须是文档中的**逐字引用**，不允许改写或总结
    - 明确每个 facet 至少提供 1 条 evidence（对应量规 `minimum_evidence_units`）
    - 要求标注 `support_type`（supporting = 正面证据 / counter = 反面证据 / neutral = 中性参考）
  - `template_version` 升级为 `"v2"`

- [x] **I2.2** 设计 per-dimension prompt override 机制：
  - 在 `configs/prompts/` 下新增可选目录 `evidence_extraction_overrides/`
  - 约定文件名 = `{dimension_id}.yaml`（如 `conventions.yaml`）
  - override 文件结构与主模板相同，但只包含需要覆盖的部分（如 system 指令中的额外提示）
  - 加载优先级：存在 override 时用 override，不存在时用全局模板
  - **本阶段只建立机制和目录结构，不需要实际创建 override 文件**（留给外环迭代时使用）

- [x] **I2.3** 更新 `PromptLoader`（如需要）：
  - 新增 `load_with_override(template_name, dimension_id)` 方法
  - 按优先级查找：`overrides/{dimension_id}.yaml` → 全局 `{template_name}.yaml`
  - 若 PromptLoader 不需要改动（直接在 prompt_builders 中处理），则跳过此步

---

### I3：prompt_builders 更新

> 修改 `src/agents/prompt_builders.py` 中的 `build_extraction_prompt`

- [x] **I3.1** 重写 `build_extraction_prompt()` 签名与逻辑：
  ```python
  def build_extraction_prompt(
      plan: CoveragePlan,
      document: NormalizedDocument,
      rubric: RubricSnapshot,
      template: PromptTemplate,
      override_template: Optional[PromptTemplate] = None,
  ) -> str:
  ```

- [x] **I3.2** 构造 chunks 上下文：
  - 当 `plan.coverage_strategy != "full_scan"` 且 `plan.target_unit_ids` 非空时：
    只传 `target_unit_ids` 对应的 TextUnit，按 `sequence_index` 排序
  - 当 `plan.coverage_strategy == "full_scan"` 时：传全部 TextUnit
  - 每个 chunk 格式：`{"id": unit.unit_id, "title": unit.chunk_title or "", "text": unit.text}`

- [x] **I3.3** 构造 levels 上下文：
  - 从 `rubric.dimension_by_id[plan.dimension_id]["levels"]` 读取完整分档列表
  - 每个 level 包含 `rank`、`summary`、`descriptors`（完整传入，不截断）

- [x] **I3.4** 构造 facet_descriptions 上下文：
  - 从量规维度定义中读取 facet 描述信息
  - 当前量规只有 facet_id 列表，无单独描述字段 → 传 `[{"facet_id": id, "description": ""}]`
  - 预留 description 字段，未来量规丰富后自动生效（外环也可通过修改量规呈现方式来调节）

- [x] **I3.5** 选择模板：有 `override_template` 时用 override，否则用全局 template

- [x] **I3.6** 验证：用 mock 数据调用，确认渲染结果包含 chunks / levels / facet_descriptions

---

### I4：extractor.py 重写

> 修改 `src/agents/extractor.py`，消费新 prompt 格式和 quote_matcher

- [x] **I4.1** 更新 `_OUTPUT_SCHEMA`：
  - 移除 `start_offset` / `end_offset`
  - 新增 `chunk_id`（type: string）

- [x] **I4.2** 更新 `run()` 签名：
  ```python
  def run(
      plan: CoveragePlan,
      document: NormalizedDocument,
      rubric: RubricSnapshot,
      provider: BaseProvider,
      template: PromptTemplate,
      override_template: Optional[PromptTemplate] = None,
  ) -> List[EvidenceSpan]:
  ```

- [x] **I4.3** 调用新的 `build_extraction_prompt`（传入 override_template）

- [x] **I4.4** 解析 LLM 响应 + quote_matcher 回填：
  - 从响应中取 `quote`、`chunk_id`、`facets`、`support_type`
  - 调用 `match_quote(quote, document.normalized_text, document.text_units)` 获取匹配结果
  - 用匹配结果填充 `start_offset`、`end_offset`、`unit_id`
  - 若 LLM 返回了 `chunk_id` 且 match 为 unmatched，尝试在该 chunk 的 text 范围内做局部匹配
  - `extraction_note` 记录 `match_method`（如 `"provider:exact"` / `"provider:fuzzy"` / `"provider:unmatched"`）

- [x] **I4.5** scope 判定逻辑调整：
  - 匹配成功（exact / normalized / fuzzy）→ `EvidenceScope.SPAN`
  - 匹配失败（unmatched）→ `EvidenceScope.GLOBAL`，保留 quote 文本但 offset 为 None

- [x] **I4.6** 保留 facet 兜底逻辑：
  - 已有 `covered_facets` 检查 + fallback span 生成，保持不变
  - fallback span 的 `extraction_note` 改为 `"provider_fallback:no_evidence"`

- [x] **I4.7** 验证：
  - mock provider 返回合法 JSON → spans 有正确的 offset 和 unit_id
  - mock provider 返回不含 chunk_id 的 JSON → 仍能通过全文匹配回填
  - mock provider 返回乱码 quote → scope 降级为 GLOBAL，不抛异常

---

### I5：Runner 适配

> 修改 `src/pipeline/runner.py` 中 Extraction 阶段的调用方式

- [x] **I5.1** extraction 调用传入 override_template：
  - 从 `self._prompt_templates` 中查找 `"evidence_extraction_override_{dimension_id}"`
  - 不存在则传 `None`（使用全局模板）

- [x] **I5.2** 更新 `output_ref` 记录：
  - 增加 match 统计信息：`f"spans:{total_spans} (exact:{n_exact}, fuzzy:{n_fuzzy}, unmatched:{n_unmatched})"`

- [x] **I5.3** 验证：mock 模式下 runner 行为不变（deterministic_extractor 路径不受影响）

---

### I6：Observer 适配

> 确认 `src/agents/observer.py` 能正确处理新的 EvidenceSpan（含 unit_id）

- [x] **I6.1** 确认 observer.run() 不依赖 unit_id 为 None 的假设
  - 当前实现只使用 span.facet_ids 和 span.span_id，不读 unit_id → 预计无需改动
  - 若有依赖则做兼容

- [x] **I6.2** coverage_miss_span_ids 回填逻辑确认：
  - 阶段 A4 在 DimensionObservation 中增加了 `coverage_miss_span_ids` 字段
  - runner 在 observer 阶段后需要计算：对每个维度，找出 span.unit_id 不在 plan.target_unit_ids 中的 span
  - 确认此逻辑在 runner 中（阶段 E 或此阶段）正确实现

---

### I7：测试补充

- [x] **I7.1** 新建 `tests/unit/utils/test_quote_matcher.py`（已在 I1.5 描述，此处标记为测试阶段统一验证入口）

- [x] **I7.2** 新建 `tests/unit/agents/test_extractor_v2.py`：
  - `test_targeted_extraction_only_passes_target_chunks`：验证非 full_scan 时 prompt 只含目标 chunk
  - `test_full_scan_passes_all_chunks`：验证 full_scan 时 prompt 含全部 chunk
  - `test_quote_backfill_exact`：LLM 返回精确 quote → offset 和 unit_id 正确回填
  - `test_quote_backfill_fuzzy`：LLM 返回略有偏差的 quote → fuzzy 匹配成功
  - `test_quote_unmatched_falls_to_global`：无法匹配 → scope=GLOBAL，不抛异常
  - `test_chunk_id_assists_matching`：LLM 返回 chunk_id → 在该 chunk 范围内优先匹配
  - `test_facet_fallback_still_works`：缺失 facet 时自动补 fallback span
  - `test_override_template_used_when_present`：有 override 时用 override 模板

- [x] **I7.3** 更新 `tests/integration/test_mock_pipeline.py`：
  - 确认 mock 模式下 extraction 阶段行为与改造前一致（deterministic_extractor 不受影响）
  - 确认 feedback.json / evidence_spans.json 中新字段（unit_id、extraction_note）有合法值

- [x] **I7.4** 全套回归：`python -m pytest tests/ --override-ini="addopts=" -q`，确认无退化

---

### I8：外环旋钮与诊断数据总结

> 本阶段不新增 configs 文件（extraction prompt 已在 configs/prompts/ 中），
> 但需确认以下外环接口完备。

- [x] **I8.1** 确认外环可调旋钮：

  | 旋钮 | 位置 | 调节效果 |
  |------|------|---------|
  | extraction prompt（全局） | `configs/prompts/evidence_extraction.yaml` | 抽取指令、输出格式要求 |
  | extraction prompt（per-dimension override） | `configs/prompts/evidence_extraction_overrides/{dim}.yaml` | 单维度定制指令 |
  | minimum_evidence_units | `configs/rubrics/` 中各维度 `evidence_requirements` | 每维度最少 evidence 数 |
  | coverage_strategy + target_unit_ids | 由上游 Coverage 阶段决定，间接受 `configs/policies/chunking/` 控制 | 传给 Extractor 的上下文范围 |

- [x] **I8.2** 确认 artifacts 落盘数据完整性：
  - `evidence_spans.json` 中每个 span 包含：`unit_id`（非 None，除 GLOBAL scope）、`extraction_note`（含 match_method）
  - 外环可通过 `extraction_note` 统计 exact/fuzzy/unmatched 比率，诊断 quote 质量
  - 外环可通过 `unit_id` + `target_unit_ids` 计算 `coverage_recall_rate`

---

### 阶段 I 执行顺序

```
I1（quote_matcher 工具，无外部依赖）
  ↓
I2（prompt 模板重写，无代码依赖）  ← 可与 I1 并行
  ↓
I3（prompt_builders 更新，依赖 I2 的模板变量定义）
  ↓
I4（extractor.py 重写，依赖 I1 + I3）
  ↓
I5 ‖ I6（runner 适配 + observer 确认，依赖 I4，可并行）
  ↓
I7（测试，依赖 I4 + I5 + I6）
  ↓
I8（外环接口确认，依赖全部完成）
```

---

## 阶段 J：Observer 改造

> **前置假设**：阶段 I 已完成。EvidenceSpan 携带 `support_type`（supporting/counter/neutral）、
> `unit_id`（quote_matcher 回填）、丰富的 `extraction_note`。
>
> **改造目标**：Observer 是 Extraction → Scorer 之间的确定性整理层，不引入 LLM。
> 职责：① 按 facet 组织证据并正反分流 ② 计算覆盖质量信号 ③ 作为 RE_SCORE 的重入 checkpoint
>
> **改造量**：小，纯逻辑修正。

---

### J1：support_type 正反分流

> 修改 `src/agents/observer.py`

- [x] **J1.1** 在 FacetFinding 构建逻辑中，根据 `span.extraction_note` 中的 `support_type` 信息分流：
  - 需要从 EvidenceSpan 获取 support_type。当前 EvidenceSpan 没有 `support_type` 字段。
  - **方案 A**（推荐）：在 `EvidenceSpan` 合约中新增 `support_type: str = "supporting"` 字段（向下兼容）
  - **方案 B**：从 `extraction_note` 中解析（如 `"provider:exact:supporting"`）——不推荐，过于脆弱
  - 选定方案后同步更新 `to_dict()` / `from_dict()` 的 allowed 集合

- [x] **J1.2** 修改 `run()` 中 FacetFinding 构建：
  ```python
  for facet_id in plan.required_facets:
      matching = [s for s in spans if facet_id in s.facet_ids]
      supporting = [s.span_id for s in matching if s.support_type in ("supporting", "neutral")]
      counter = [s.span_id for s in matching if s.support_type == "counter"]
      facet_findings.append(FacetFinding(
          facet_id=facet_id,
          supporting_span_ids=supporting,
          counter_span_ids=counter,
          finding_note=f"{len(supporting)} supporting, {len(counter)} counter",
      ))
  ```

- [x] **J1.3** 修改维度级汇总：
  - `supporting_span_ids`：所有 `support_type in ("supporting", "neutral")` 的 span
  - `counter_span_ids`：所有 `support_type == "counter"` 的 span

---

### J2：coverage_miss_span_ids 计算

- [x] **J2.1** 在 `observer.run()` 中增加参数或在 runner 中计算（二选一）：
  - **推荐**：在 runner 的 observer 阶段后计算，因为 observer 只接收 spans + plan，
    而 `target_unit_ids` 在 plan 中已有
  - 逻辑：对每个维度，遍历该维度的 spans，若 `span.unit_id is not None` 且
    `span.unit_id not in plan.target_unit_ids`，则记入 `coverage_miss_span_ids`
  - 由于 DimensionObservation 是 frozen dataclass，需要在 observer.run() 内计算
    （在构造 DimensionObservation 时传入）

- [x] **J2.2** 修改 `observer.run()` 签名（如需要）：
  - plan 已经包含 `target_unit_ids`，无需新增参数
  - 在构造 DimensionObservation 前计算 coverage_miss

---

### J3：uncertainty_notes 丰富化

- [x] **J3.1** 对每个无证据的 required_facet，添加 uncertainty note：
  ```python
  if not supporting and not counter:
      uncertainty_notes.append(f"facet '{facet_id}' has no evidence")
  ```

- [x] **J3.2** 若整体 confidence 为 LOW，添加汇总 note：
  ```python
  if confidence == ObservationConfidence.LOW:
      uncertainty_notes.append("observation has significant coverage gaps")
  ```

---

### J4：EvidenceSpan 合约扩展（如选方案 A）

> 修改 `src/contracts/evidence.py`

- [x] **J4.1** `EvidenceSpan` 新增字段：
  - `support_type: str = "supporting"`（取值：`"supporting"` | `"counter"` | `"neutral"`）
  - 默认值 `"supporting"` 保证向下兼容（现有 mock/deterministic 路径不受影响）
  - 同步更新 `to_dict()` / `from_dict()` 的 allowed 集合

- [x] **J4.2** 验证：全套 mock 回归测试通过（新字段有默认值，不破坏现有数据）

---

### J5：测试

- [x] **J5.1** 更新 `tests/unit/agents/test_observer.py`（或新建）：
  - `test_supporting_counter_split`：3 个 span（2 supporting + 1 counter）→ FacetFinding 正确分流
  - `test_neutral_treated_as_supporting`：neutral span 归入 supporting_span_ids
  - `test_coverage_miss_computed`：span.unit_id 不在 target_unit_ids → 记入 coverage_miss
  - `test_uncertainty_notes_for_missing_facet`：无证据 facet → uncertainty_notes 非空
  - `test_backward_compat_no_support_type`：无 support_type 的旧 span → 默认 supporting

- [x] **J5.2** 全套回归

---

### 阶段 J 执行顺序

```
J4（EvidenceSpan 合约扩展，如选方案 A）
  ↓
J1（support_type 分流，依赖 J4）
  ↓
J2 ‖ J3（coverage_miss + uncertainty_notes，可并行）
  ↓
J5（测试）
```

---

## 阶段 K：Scorer Agent 改造

> **前置假设**：阶段 I（Extraction）和 J（Observer）均已完成。
> Observer 产出按 facet 组织、正反分流的 DimensionObservation，
> EvidenceSpan 携带 support_type / unit_id / text_quote。
>
> **改造目标**：
> 1. Scorer 只接收结构化证据打分（不传全文）——用机制解决 Extractor 遗漏问题（RE_EXTRACT），不让 Scorer 重复搜证
> 2. 去除 scoring prompt 中所有 ASAP 硬编码内容，全部配置化
> 3. 支持 per-dimension prompt override（Voice 校准等归入 override，不写在全局模板）
> 4. evidence_span_ids 使用 LLM 实际引用的 span，而非 observation 全量 span
>
> **核心设计决策**：
> - **Scorer 不再看原文**。评分依据 = Observer 产出的结构化证据（按 facet 分组的 quote + 正反标记 + 覆盖信号）。
>   若 Extractor 漏了关键证据，由 RE_EXTRACT 回退机制解决，Scorer 不承担搜证职责。
>
> **遵循原则**：
> - ASAP 专属内容（锚定样例、脱敏说明、维度校准）→ `configs/` 中配置化
> - scoring prompt 模板 → `configs/prompts/scoring.yaml`（外环可调）
> - per-dimension override → `configs/prompts/scoring_overrides/{dim}.yaml`
> - rationale / evidence_ids 等诊断信息 → 落盘到 `artifacts/`

---

### K1：ASAP 专属内容外部化

> 将当前 `configs/prompts/scoring.yaml` 中硬编码的 ASAP 内容抽离到独立配置文件。

- [x] **K1.1** 新建 `configs/prompts/scoring_context.yaml`——数据集级上下文配置：
  ```yaml
  scoring_context:
    context_id: "asap_set8_scoring_context_v1"
  
    role_description: >
      You are a trained essay scorer evaluating student writing
      based on a standardized rubric.
  
    dataset_notes: |
      ## Text Anonymization
      This essay has been processed to protect student privacy...
      （当前 scoring.yaml 第 85-99 行内容迁移至此）
  
    score_anchors:
      - anchor_id: "score_5_the_jump"
        target_score: 5
        title: "The Jump (narrative about peer pressure)"
        per_dimension:
          ideas_content: {score: 5, note: "Writing is clear, focused..."}
          organization: {score: 5, note: "Strong chronological sequencing..."}
          voice: {score: 6, note: "Deep commitment to topic..."}
          # ...（当前第 29-40 行内容结构化迁移）
      - anchor_id: "score_4_student_council"
        # ...（当前第 42-55 行）
      - anchor_id: "score_3_new_truck"
        # ...（当前第 57-69 行）
      - anchor_id: "score_2_job_makes_pay"
        # ...（当前第 71-83 行）
  
    calibration_notes: |
      ## Scoring Calibration
      Avoid the common error of scoring too conservatively...
      （当前第 101-110 行内容迁移至此）
  ```
  - 此文件由 bundle 引用，换量规时替换整个文件即可
  - `score_anchors` 结构化存储，prompt 模板按 Jinja2 渲染

- [x] **K1.2** 新建 `configs/prompts/scoring_overrides/` 目录，迁移 Voice 校准：
  - 新建 `configs/prompts/scoring_overrides/voice.yaml`
  - 内容：当前 scoring.yaml 第 112-127 行的 Voice 校准规则
  - 模板结构与全局 scoring.yaml 相同，仅包含需要覆盖/追加的 section
  - **其他维度暂不创建 override 文件**（留给外环迭代）

- [x] **K1.3** 更新 bundle 配置引用：
  - `configs/bundles/asap_set8_baseline.bundle.yaml` 新增 `scoring_context_ref` 字段
  - `src/config/schema.py` 新增对应字段（Optional）
  - `src/config/resolver.py` 加载 scoring_context 并挂载到 policy_snapshot 或单独字段

---

### K2：Scoring Prompt 模板重写

> 重写 `configs/prompts/scoring.yaml`，去除所有硬编码内容，改为模板变量驱动。

- [x] **K2.1** 重写 `configs/prompts/scoring.yaml`，变更要点：
  - **输入变量变更**：
    - 新增 `{{ role_description }}`：来自 scoring_context（替代硬编码角色描述）
    - 新增 `{{ facet_evidence }}`：按 facet 组织的结构化证据视图（来自 Observer）
      ```
      每个 facet 包含：
        facet_id, supporting quotes[], counter quotes[], finding_note
      ```
    - 新增 `{{ observation_confidence }}`：HIGH/MEDIUM/LOW（来自 Observer）
    - 新增 `{{ uncertainty_notes }}`：覆盖缺口说明（来自 Observer）
    - 新增 `{{ score_anchors }}`：锚定样例列表（来自 scoring_context，可为空）
    - 新增 `{{ dataset_notes }}`：数据集专属说明（来自 scoring_context，可为空）
    - 新增 `{{ calibration_notes }}`：校准提示（来自 scoring_context，可为空）
    - 新增 `{{ dimension_override_notes }}`：per-dimension 追加指令（来自 override，可为空）
    - 保留 `{{ dimension_name }}`、`{{ dimension_code }}`、`{{ levels }}`
    - **移除** `{{ essay_text }}`——Scorer 不再看原文
    - **移除** `{{ evidence_spans }}`——改为更结构化的 `{{ facet_evidence }}`
  - **输出格式保持**：
    ```json
    {
      "proposed_score": integer,
      "descriptor_refs": ["exact text of relevant descriptors"],
      "evidence_ids": ["span-01", "span-03"],
      "confidence": float,
      "justification": "brief explanation linking score to descriptors and evidence"
    }
    ```
    - `evidence_ids` 字段保持不变，但后续代码将实际使用此字段（见 K4）
  - **指令重构**：
    - 强调评分必须基于提供的证据，不得引用未在 facet_evidence 中出现的内容
    - 当 `observation_confidence` 为 LOW 时，要求 Scorer 降低自身 confidence 并说明理由
    - 当某 facet 无证据时，要求在 justification 中明确标注"insufficient evidence for facet X"
  - `template_version` 升级为 `"v3"`

- [x] **K2.2** per-dimension override 加载机制：
  - 复用阶段 I2.2 建立的 override 机制（同一套 `PromptLoader.load_with_override`）
  - scoring 的 override 目录：`configs/prompts/scoring_overrides/{dimension_id}.yaml`
  - override 内容注入到模板变量 `{{ dimension_override_notes }}` 中

---

### K3：prompt_builders 更新

> 修改 `src/agents/prompt_builders.py` 中的 `build_scoring_prompt`

- [x] **K3.1** 重写 `build_scoring_prompt()` 签名：
  ```python
  def build_scoring_prompt(
      observation: DimensionObservation,
      evidence_spans: List[EvidenceSpan],
      rubric: RubricSnapshot,
      template: PromptTemplate,
      scoring_context: Optional[dict] = None,
      override_template: Optional[PromptTemplate] = None,
  ) -> str:
  ```
  - **移除** `document: NormalizedDocument` 参数——Scorer 不再需要原文

- [x] **K3.2** 构造 `facet_evidence` 上下文：
  - 遍历 `observation.facet_findings`
  - 对每个 FacetFinding，从 `evidence_spans` 中查找对应 span 的 `text_quote`
  - 生成结构：
    ```python
    [
        {
            "facet_id": "clarity_focus",
            "supporting": [{"span_id": "span-01", "quote": "..."}],
            "counter": [{"span_id": "span-07", "quote": "..."}],
            "finding_note": "2 supporting, 1 counter",
        },
        ...
    ]
    ```

- [x] **K3.3** 构造 scoring_context 相关变量：
  - 从 `scoring_context` dict 中读取 `role_description`、`dataset_notes`、`calibration_notes`、`score_anchors`
  - 任何字段缺失时使用合理默认值（空字符串或通用角色描述）
  - `score_anchors` 格式化为按当前维度过滤的锚定文本

- [x] **K3.4** 构造 override_notes：
  - 有 override_template 时渲染其内容，注入 `{{ dimension_override_notes }}`
  - 无 override 时为空字符串

- [x] **K3.5** 构造 levels 上下文：
  - 与阶段 I3.3 相同逻辑——从 rubric 读取完整分档描述符
  - 保持与 Extraction prompt 一致的 levels 格式

- [x] **K3.6** 验证：用 mock 数据调用，确认渲染结果：
  - 不含 essay_text
  - 包含按 facet 组织的证据
  - 包含 scoring_context 中的锚定样例和校准提示
  - Voice 维度包含 override_notes

---

### K4：scorer.py 重写

> 修改 `src/agents/scorer.py`，消费新 prompt 格式，正确使用 LLM 返回的 evidence_ids

- [x] **K4.1** 更新 `run()` 签名：
  ```python
  def run(
      observation: DimensionObservation,
      evidence_spans: List[EvidenceSpan],
      rubric: RubricSnapshot,
      provider: BaseProvider,
      template: PromptTemplate,
      rater_id: str,
      scoring_context: Optional[dict] = None,
      override_template: Optional[PromptTemplate] = None,
  ) -> ScoreHypothesis:
  ```
  - **移除** `document: NormalizedDocument` 参数

- [x] **K4.2** 调用新的 `build_scoring_prompt`（传入 scoring_context + override_template）

- [x] **K4.3** evidence_span_ids 改用 LLM 返回值：
  ```python
  # 旧代码（忽略 LLM 选择）：
  # evidence_span_ids = list(observation.supporting_span_ids)
  
  # 新代码（优先用 LLM 返回，fallback 到 observation）：
  raw_evidence_ids = list(data.get("evidence_ids") or [])
  valid_span_ids = {s.span_id for s in evidence_spans}
  evidence_span_ids = [eid for eid in raw_evidence_ids if eid in valid_span_ids]
  if not evidence_span_ids:
      evidence_span_ids = list(observation.supporting_span_ids)
  ```
  - LLM 返回的 evidence_ids 必须在 valid_span_ids 中才采纳（防幻觉）
  - 全部无效时 fallback 到 observation 的 supporting spans

- [x] **K4.4** 保留 score 范围 clamp + descriptor_refs fallback 逻辑（不变）

- [x] **K4.5** 验证：
  - mock provider 返回合法 JSON → evidence_span_ids 使用 LLM 返回值
  - mock provider 返回无效 evidence_ids → fallback 到 observation spans
  - score 超出范围 → 正确 clamp

---

### K5：Runner 适配

> 修改 `src/pipeline/runner.py` 中 Scoring 阶段的调用方式

- [x] **K5.1** 加载 scoring_context：
  - 从 `bundle.policy_snapshot` 或单独属性读取 scoring_context（由 K1.3 的 resolver 挂载）
  - 传入 `scorer.run()` 调用

- [x] **K5.2** scoring 调用移除 document 参数：
  - 旧：`scorer.run(obs, all_spans_flat, rubric, document, provider, template, rater_id)`
  - 新：`scorer.run(obs, all_spans_flat, rubric, provider, template, rater_id, scoring_context, override_template)`

- [x] **K5.3** scoring override_template 查找：
  - 从 `self._prompt_templates` 中查找 `"scoring_override_{dimension_id}"`
  - 不存在则传 `None`

- [x] **K5.4** rater_3 评分路径同步更新：
  - runner 中 rater_3 的 `scorer.run()` 调用同样移除 document，传入 scoring_context + override

- [x] **K5.5** 验证：mock 模式下 runner 行为不变（deterministic_scorer 路径不受影响）

---

### K6：deterministic_scorer 适配

> 确认 `src/agents/deterministic_scorer.py` 不受影响

- [x] **K6.1** 确认 deterministic_scorer.run() 签名不依赖 document 参数
  - 当前签名：`run(obs, rubric, rater_id)` → 不受影响
  - 若有依赖则做兼容

---

### K7：测试补充

- [x] **K7.1** 新建 `tests/unit/agents/test_scorer_v2.py`：
  - `test_no_essay_text_in_prompt`：验证渲染后的 prompt 不包含原文
  - `test_facet_evidence_in_prompt`：验证 prompt 包含按 facet 组织的证据
  - `test_score_anchors_from_context`：有 scoring_context 时 prompt 包含锚定样例
  - `test_no_context_still_works`：无 scoring_context 时 prompt 正常渲染（通用角色描述）
  - `test_evidence_ids_from_llm_response`：LLM 返回 valid evidence_ids → 被采纳
  - `test_invalid_evidence_ids_fallback`：LLM 返回无效 ids → fallback 到 observation spans
  - `test_override_template_for_voice`：voice 维度有 override → prompt 包含校准规则
  - `test_low_confidence_observation`：observation_confidence=LOW → prompt 包含 uncertainty 提示

- [x] **K7.2** 新建 `tests/unit/config/test_scoring_context.py`：
  - 确认 `scoring_context.yaml` 可被 resolver 正确加载
  - 确认 `score_anchors` 可按 dimension 过滤

- [x] **K7.3** 更新 `tests/integration/test_mock_pipeline.py`：
  - 确认 mock 模式下 scoring 阶段行为不变（deterministic_scorer 路径）
  - 确认 hypotheses.json 中 evidence_span_ids 字段有合法值

- [x] **K7.4** 全套回归

---

### K8：外环旋钮与诊断数据总结

- [x] **K8.1** 确认外环可调旋钮：

  | 旋钮 | 位置 | 调节效果 |
  |------|------|---------|
  | scoring prompt（全局模板） | `configs/prompts/scoring.yaml` | 评分指令、输出格式、证据引用要求 |
  | scoring prompt（per-dimension override） | `configs/prompts/scoring_overrides/{dim}.yaml` | 单维度校准规则（如 Voice 校准） |
  | scoring_context（锚定样例+校准+数据集说明） | `configs/prompts/scoring_context.yaml` | 锚定作文、校准提示、脱敏说明 |
  | levels（分档描述符） | `configs/rubrics/` 各维度 levels | 量规呈现方式（内容固定，呈现可调） |

- [x] **K8.2** 确认 artifacts 落盘数据完整性：
  - `hypotheses.json` 中每个 hypothesis 包含：`rationale`（LLM justification 原文）、`evidence_span_ids`（LLM 实际引用的 span）、`confidence`
  - 外环可通过 `evidence_span_ids` 检验 rationale 与证据的关联紧密度
  - 外环可通过 `confidence` 分布检测 scorer 是否普遍过于自信/保守

---

### 阶段 K 执行顺序

```
K1（ASAP 内容外部化 → configs/，无代码依赖）
  ↓
K2（scoring prompt 模板重写，依赖 K1 的变量定义）
  ↓
K3（prompt_builders 更新，依赖 K2）
  ↓
K4（scorer.py 重写，依赖 K3）
  ↓
K5 ‖ K6（runner 适配 + deterministic_scorer 确认，依赖 K4，可并行）
  ↓
K7（测试，依赖 K4 + K5 + K6）
  ↓
K8（外环接口确认，依赖全部完成）
```

---

## 阶段 L：Score Reconciliation（分数调和）

> **前置假设**：阶段 K 已完成。Scorer 产出每个 (rater, dimension) 的 ScoreHypothesis。
>
> **改造目标**：
> 1. 将 Consistency Checker（冲突检测）+ Adjudicator（冲突裁决）合并为一个统一的 Reconciliation 阶段
> 2. 泛化冲突触发后的行为——"重评哪些维度""用什么策略裁决"全部由 configs 控制
> 3. 消除 runner 中 ASAP 特化的"全维度重评"硬编码逻辑
> 4. 丰富诊断数据落盘，服务外环
>
> **核心设计决策**：
> - 冲突检测机制已经是配置驱动的（triggers），保持不变
> - **冲突响应行为**必须完全配置化：重评范围（全维度 vs 仅冲突维度）、裁决策略（rater_3 权威 vs 取平均等）
> - 合并后对外暴露单一入口，内部仍可拆为 detect → [optional re-score] → resolve 三步
>
> **遵循原则**：
> - `re_score_scope` / `resolution_strategy` → `configs/policies/adjudication/`
> - conflicts / adjudication_records → 落盘到 `artifacts/`
> - runner 中不出现 "ASAP Set 8" 注释或特化分支

---

### L1：adjudication policy 配置扩展

> 修改 `configs/policies/adjudication/asap_set8_default.yaml`

- [x] **L1.1** 在 `resolution_strategy` 下新增配置字段：
  ```yaml
  resolution_strategy:
    default: "use_resolution_rater_as_authoritative"
    fallback_if_no_resolution: "average_of_raters"
    re_score_scope: "all_dimensions"       # "all_dimensions" | "conflicted_only"
  ```
  - `re_score_scope` 控制触发 resolution rater 时重评的范围
  - `"all_dimensions"`：ASAP Set 8 规则（冲突存在即全维度重评）
  - `"conflicted_only"`：仅对有冲突的维度触发 resolution rater
  - 默认值 `"all_dimensions"` 保持与 ASAP 行为一致

- [x] **L1.2** 统一 resolution_strategy 命名：
  - 将 `use_rater_3_as_authoritative` 改为 `use_resolution_rater_as_authoritative`
    （去除 "rater_3" 硬编码名称，resolution rater 标签由 `raters.resolution_rater_label` 决定）

- [x] **L1.3** 在 explanation policy 中新增 `low_confidence_threshold`：
  ```yaml
  # configs/policies/explanation/evidence_grounded_v1.yaml
  output_constraints:
    low_confidence_threshold: 0.5    # 新增，原硬编码在 explanation.py
  ```

---

### L2：统一 Reconciliation Agent

> 新建 `src/agents/reconciliation.py`，合并 consistency_checker + adjudicator 逻辑

- [x] **L2.1** 新建 `src/agents/reconciliation.py`，统一入口：
  ```python
  def run(
      hypotheses: List[ScoreHypothesis],
      policy: PolicySnapshot,
  ) -> ReconciliationResult:
      """
      Returns:
          ReconciliationResult(
              conflicts: List[ConflictRecord],
              needs_resolution_scoring: bool,
              resolution_dimension_ids: List[str],  # 需要 re-score 的维度 ID
          )
      """
  ```
  - 步骤 1：调用 `evaluate_all_triggers` 检测冲突
  - 步骤 2：读取 `re_score_scope` 决定重评范围
    - `"all_dimensions"` → `resolution_dimension_ids` = 全部维度
    - `"conflicted_only"` → `resolution_dimension_ids` = 仅有冲突的维度
  - 步骤 3：返回 ReconciliationResult（不直接做 resolution scoring，由 runner 执行）

- [x] **L2.2** 新增 `resolve()` 函数：
  ```python
  def resolve(
      conflicts: List[ConflictRecord],
      hypotheses: List[ScoreHypothesis],
      policy: PolicySnapshot,
  ) -> Tuple[List[AdjudicationRecord], List[FinalDimensionDecision]]:
  ```
  - 从 `resolution_strategy.default` 读取裁决策略
  - `"use_resolution_rater_as_authoritative"`：当前 adjudicator 逻辑（rater_3 优先）
  - `"average_of_raters"`：取冲突维度中各 rater 的平均分（四舍五入），无冲突维度取字典序最小者
  - 策略实现为可注册的函数映射，方便未来扩展

- [x] **L2.3** 定义 `ReconciliationResult` dataclass（frozen=True）：
  - `conflicts: List[ConflictRecord]`
  - `needs_resolution_scoring: bool`
  - `resolution_dimension_ids: List[str]`
  - `resolution_rater_label: str`（从 policy 读取）

- [x] **L2.4** `decision_note` 丰富化：
  - 有冲突且已裁决：`"conflict resolved via {strategy}, {resolution_rater} score used"`
  - 有冲突但 resolution rater 缺失：`"conflict unresolved, fallback to {rater_id}"`
  - 无冲突：`"no conflict, {rater_id} score used (raters converged)"`

---

### L3：Runner 适配

> 修改 `src/pipeline/runner.py`，用 reconciliation 替代分散的 consistency_checker + adjudicator 调用

- [x] **L3.1** 导入 reconciliation 替代 consistency_checker + adjudicator：
  - 移除直接导入 `consistency_checker` 和 `adjudicator`
  - 新增 `from src.agents import reconciliation`

- [x] **L3.2** 重写 SCORED 分支逻辑：
  ```python
  if cs == PipelineState.SCORED:
      # Step 1: 冲突检测
      recon_result = reconciliation.run(hypotheses, policy)

      # Step 2: 如需 resolution scoring，由 runner 调用 scorer
      if recon_result.needs_resolution_scoring:
          resolution_hyps = [
              scorer.run(obs, ..., recon_result.resolution_rater_label, ...)
              for obs in observations
              if obs.dimension_id in recon_result.resolution_dimension_ids
          ]
          hypotheses = hypotheses + resolution_hyps

      # Step 3: 裁决
      adj_records, decisions = reconciliation.resolve(
          recon_result.conflicts, hypotheses, policy
      )
  ```
  - 移除 runner 中的 ASAP 特化注释和分支
  - `re_score_scope` 由 reconciliation 内部读取 policy 后返回 `resolution_dimension_ids`
  - router 逻辑保持（route_after_consistency_check / route_after_adjudication），但由 reconciliation 返回值驱动

- [x] **L3.3** 状态机推进保持兼容：
  - 仍经过 CONSISTENCY_CHECKED → ADJUDICATED → FEEDBACK_RENDERED 状态
  - 只是 runner 代码更简洁，不再散落 ASAP 特化逻辑

- [x] **L3.4** mock 模式适配：
  - mock 模式下：`reconciliation.run()` 返回相同 conflicts（委托 deterministic_consistency_checker）
  - `reconciliation.resolve()` 在无 resolution rater 时走 deterministic_adjudicator 路径
  - 或统一由 reconciliation 内部处理 mock/real 分支

---

### L4：诊断数据落盘

- [x] **L4.1** runner 在 reconciliation 完成后，将以下数据存入 carry-forward 变量供 eval.py 落盘：
  - `conflicts` → `artifacts/eval/{essay_id}/conflicts.json`
  - `adj_records` → `artifacts/eval/{essay_id}/adjudication_records.json`

- [x] **L4.2** eval.py 新增落盘逻辑：
  - 从 runner 获取 conflicts 和 adj_records（新增 `last_conflicts` / `last_adjudication_records` 属性）
  - 序列化并写入 artifacts

- [x] **L4.3** `_LOW_CONFIDENCE_THRESHOLD` 改为从 policy 读取：
  - `explanation.py` 中 `_LOW_CONFIDENCE_THRESHOLD = 0.5` → 从 `policy.explanation_policy.output_constraints.low_confidence_threshold` 读取
  - 缺失时 fallback 到 0.5

---

### L5：测试

- [x] **L5.1** 新建 `tests/unit/agents/test_reconciliation.py`：
  - `test_no_conflict_produces_decisions`：无冲突 → decisions 从字典序最小 rater 取分
  - `test_conflict_detected_needs_resolution`：有冲突 → `needs_resolution_scoring=True`
  - `test_re_score_scope_all_dimensions`：scope=all → resolution_dimension_ids = 全部
  - `test_re_score_scope_conflicted_only`：scope=conflicted_only → resolution_dimension_ids = 仅冲突维度
  - `test_resolve_authoritative`：resolution rater 存在 → 以其分数为权威
  - `test_resolve_average_of_raters`：策略=average → 取平均分
  - `test_resolve_fallback_human_review`：resolution rater 缺失 → HUMAN_REVIEW
  - `test_decision_note_populated`：各场景下 decision_note 非空

- [x] **L5.2** 更新 `tests/integration/test_mock_pipeline.py`：
  - 确认 mock 模式下 reconciliation 行为与原 consistency_checker + adjudicator 一致
  - 确认 decisions 输出不变

- [x] **L5.3** 全套回归

---

### L6：外环旋钮与诊断数据总结

- [x] **L6.1** 确认外环可调旋钮：

  | 旋钮 | 位置 | 调节效果 |
  |------|------|---------|
  | triggers（冲突定义） | `configs/policies/adjudication/` | score_distance 阈值、pattern_match 模式 |
  | re_score_scope | `configs/policies/adjudication/` | "all_dimensions" vs "conflicted_only" |
  | resolution_strategy | `configs/policies/adjudication/` | "use_resolution_rater_as_authoritative" / "average_of_raters" |
  | resolution_rater_label | `configs/policies/adjudication/` | resolution rater 标签 |
  | low_confidence_threshold | `configs/policies/explanation/` | uncertainty_note 触发阈值 |

- [x] **L6.2** 确认 artifacts 落盘完整性：
  - `conflicts.json`：冲突记录（trigger_rule_id、conflict_detail、recommended_path）
  - `adjudication_records.json`：裁决记录（resolution_path、is_resolved、resolution_note）
  - 外环可通过这些数据统计：冲突频率、各触发器命中率、裁决成功率

---

### 阶段 L 执行顺序

```
L1（配置扩展，无代码依赖）
  ↓
L2（reconciliation agent 实现，依赖 L1）
  ↓
L3（runner 适配，依赖 L2）
  ↓
L4（诊断落盘 + _LOW_CONFIDENCE_THRESHOLD 配置化，依赖 L3）
  ↓
L5（测试）→ L6（外环接口确认）
```

---

## 阶段 M：Feedback Agent 改造

> **前置假设**：阶段 L 已完成。Reconciliation 产出 FinalDimensionDecision（含丰富 decision_note）、
> AdjudicationRecord（含 resolution_note）。Scorer 的 ScoreHypothesis 携带 justification 文本。
> Observer 产出结构化的 facet_findings + observation_confidence + uncertainty_notes。
>
> **改造目标**：
> 1. Feedback prompt 利用 Observer 的结构化证据视图（facet_evidence + confidence 信号）
> 2. 传入 Scorer 的 justification 作为反馈文本的种子材料
> 3. 丰富确定性反馈路径（不依赖 LLM 也能生成结构化反馈）
> 4. 建立 per-dimension override 机制
> 5. 将 ASAP 专属反馈说明（如有）配置化
>
> **遵循原则**：
> - feedback prompt 模板 → `configs/prompts/explanation.yaml`（外环可调）
> - per-dimension override → `configs/prompts/explanation_overrides/`
> - 反馈文本质量 → 落盘到 `artifacts/`（feedback.json 已有，确保新字段完整）

---

### M1：build_explanation_prompt 重写

> 修改 `src/agents/prompt_builders.py` 中的 `build_explanation_prompt`

- [x] **M1.1** 重写 `build_explanation_prompt()` 签名：
  ```python
  def build_explanation_prompt(
      decision: FinalDimensionDecision,
      observation: DimensionObservation,
      evidence_spans: List[EvidenceSpan],
      rubric: RubricSnapshot,
      template: PromptTemplate,
      scorer_rationale: Optional[str] = None,
      override_template: Optional[PromptTemplate] = None,
  ) -> str:
  ```
  - 新增 `observation`：获取 facet_findings、observation_confidence、uncertainty_notes
  - 新增 `scorer_rationale`：Scorer 的 justification 文本（反馈的种子材料）
  - 新增 `override_template`：per-dimension override

- [x] **M1.2** 构造 `facet_evidence` 上下文（复用阶段 K3.2 相同模式）：
  - 遍历 `observation.facet_findings`
  - 每个 facet 含 supporting quotes + counter quotes + finding_note
  - 只引用 `decision.evidence_span_ids` 中的 span（决策实际引用的证据）

- [x] **M1.3** 构造新增上下文变量：
  - `observation_confidence`：HIGH/MEDIUM/LOW
  - `uncertainty_notes`：列表
  - `scorer_rationale`：Scorer 的 justification 原文
  - `decision_note`：来自 Reconciliation 的裁决上下文
  - `was_adjudicated`：bool，是否经过裁决
  - `dimension_override_notes`：per-dimension override 渲染结果

- [x] **M1.4** 选择模板：有 override_template 时用 override，否则用全局 template

---

### M2：explanation.yaml 模板重写

> 重写 `configs/prompts/explanation.yaml`

- [x] **M2.1** 重写模板，新增变量：
  - **输入变量**：
    - 保留 `{{ dimension_name }}`、`{{ dimension_code }}`、`{{ canonical_score }}`、`{{ descriptor_refs }}`
    - 新增 `{{ facet_evidence }}`：按 facet 组织的结构化证据（正面/反面 quotes）
    - 新增 `{{ observation_confidence }}`：证据覆盖置信度
    - 新增 `{{ uncertainty_notes }}`：覆盖缺口说明
    - 新增 `{{ scorer_rationale }}`：Scorer 的评分理由（种子材料）
    - 新增 `{{ decision_note }}`：裁决/决策上下文
    - 新增 `{{ was_adjudicated }}`：是否经过裁决
    - 新增 `{{ dimension_override_notes }}`：per-dimension 追加指令
  - **指令重构**：
    - 以 scorer_rationale 为基础，扩写为面向学生的反馈
    - 按 facet 组织反馈结构：每个 facet 的优势和不足
    - 引用具体 evidence quote 支撑论点
    - 当 observation_confidence 为 LOW 或经过 adjudication 时，附加说明
    - 避免简单重复 scorer_rationale，要增加面向学生的改进建议
  - `template_version` 升级为 `"v2"`

- [x] **M2.2** 建立 per-dimension override 机制：
  - 目录：`configs/prompts/explanation_overrides/{dimension_id}.yaml`
  - 与 extraction/scoring 同一套 override 加载逻辑
  - **本阶段只建立机制和目录结构**

---

### M3：确定性反馈路径丰富化

> 修改 `src/policies/explanation.py` 中的 `_build_commentary`

- [x] **M3.1** 重写 `_build_commentary()`，利用 facet_findings 生成结构化文本：
  ```python
  def _build_commentary(
      decision: FinalDimensionDecision,
      observation: DimensionObservation,
      spans: List[EvidenceSpan],
      rubric: RubricSnapshot,
      max_length: int,
      scorer_rationale: Optional[str] = None,
  ) -> str:
  ```
  - 有 scorer_rationale 时：以 rationale 为主体，截断到 max_length
  - 无 scorer_rationale 时：按 facet 生成结构化文本
    - 每个有证据的 facet：`"[facet_id]: supporting evidence: 'quote'"`
    - 有 counter 时追加：`"however, counter evidence suggests: 'quote'"`
    - 比原来只取第一个 span 的 "Score X: descriptor" 信息量大得多

- [x] **M3.2** `_build_uncertainty_note` 改为从 policy 读取阈值：
  - 读 `policy.explanation_policy.output_constraints.low_confidence_threshold`
  - fallback 到 0.5

---

### M4：feedback.py 更新

> 修改 `src/agents/feedback.py`

- [x] **M4.1** 更新 `run()` 签名：
  ```python
  def run(
      decisions: List[FinalDimensionDecision],
      observations: List[DimensionObservation],
      spans: List[EvidenceSpan],
      hypotheses: List[ScoreHypothesis],     # 新增：获取 scorer rationale
      rubric: RubricSnapshot,
      policy: PolicySnapshot,
      provider: Optional[BaseProvider] = None,
      template: Optional[PromptTemplate] = None,
      override_templates: Optional[Dict[str, PromptTemplate]] = None,  # 新增
  ) -> Dict[str, Any]:
  ```

- [x] **M4.2** 为每个维度查找 scorer rationale：
  - 从 hypotheses 中找到 `decision.primary_hypothesis_id` 对应的 hypothesis
  - 取其 `rationale`（即 justification 字段）
  - 传入 `_render_commentary` 和 `build_explanation_prompt`

- [x] **M4.3** `_render_commentary` 更新：
  - 传入 observation + scorer_rationale
  - LLM 路径：调用更新后的 `build_explanation_prompt`（含 facet_evidence、rationale）
  - 确定性路径：调用更新后的 `_build_commentary`（含 facet_findings、rationale）

- [x] **M4.4** 输出结构新增字段：
  - `dimensions[dim_id]["scorer_rationale"]`：原始 scorer justification（透传，方便外环对比）
  - `dimensions[dim_id]["was_adjudicated"]`：是否经过裁决

---

### M5：Runner 适配

- [x] **M5.1** feedback 调用传入 hypotheses：
  - 旧：`feedback_agent.run(decisions, observations, spans, rubric, policy, ...)`
  - 新：`feedback_agent.run(decisions, observations, spans, hypotheses, rubric, policy, ..., override_templates)`

- [x] **M5.2** 构造 override_templates dict：
  - 从 `self._prompt_templates` 中筛选 `"explanation_override_{dimension_id}"` 开头的模板
  - 传入 feedback.run()

- [x] **M5.3** 验证 mock 模式不受影响

---

### M6：测试

- [x] **M6.1** 新建 `tests/unit/agents/test_feedback_v2.py`：
  - `test_facet_evidence_in_prompt`：LLM 路径 prompt 包含按 facet 组织的证据
  - `test_scorer_rationale_in_prompt`：prompt 包含 scorer justification
  - `test_deterministic_commentary_uses_facets`：确定性路径生成按 facet 结构的反馈
  - `test_deterministic_commentary_uses_rationale`：有 rationale 时以其为主体
  - `test_low_confidence_threshold_from_config`：阈值从 policy 读取
  - `test_adjudication_uncertainty_note`：经过裁决 → uncertainty_note 非空
  - `test_override_template_for_dimension`：有 override 时使用 override
  - `test_scorer_rationale_in_output`：输出 dict 包含 scorer_rationale 字段
  - `test_backward_compat_no_hypotheses`：hypotheses=[] 时不崩溃

- [x] **M6.2** 更新集成测试确认 mock 输出稳定

- [x] **M6.3** 全套回归

---

### M7：外环旋钮与诊断数据总结

- [x] **M7.1** 确认外环可调旋钮：

  | 旋钮 | 位置 | 调节效果 |
  |------|------|---------|
  | explanation prompt（全局模板） | `configs/prompts/explanation.yaml` | 反馈指令、结构要求 |
  | explanation prompt（per-dimension override） | `configs/prompts/explanation_overrides/{dim}.yaml` | 单维度反馈风格 |
  | max_commentary_length_per_dimension | `configs/policies/explanation/` | 反馈文本长度上限 |
  | low_confidence_threshold | `configs/policies/explanation/` | uncertainty_note 触发阈值 |
  | citation_rules | `configs/policies/explanation/` | 最少引用数等约束 |

- [x] **M7.2** 确认 artifacts 落盘完整性：
  - `feedback.json` 中每个维度包含：`scorer_rationale`（原始理由）、`was_adjudicated`（裁决标记）
  - 外环可通过 `scorer_rationale` vs `feedback_text` 对比分析反馈质量
  - 外环可通过 `was_adjudicated` 分层统计裁决对反馈质量的影响

---

### 阶段 M 执行顺序

```
M1（prompt_builders 重写）‖ M2（模板重写）
  ↓
M3（确定性路径丰富化，依赖 M1 的签名定义）
  ↓
M4（feedback.py 更新，依赖 M1 + M3）
  ↓
M5（runner 适配，依赖 M4）
  ↓
M6（测试）→ M7（外环接口确认）
```

---

## 待讨论 / 待决策

- [ ] **P1** 分块 prompt 的实际效果验证（需跑真实样本，对比分块前后 Extractor 质量）
- [ ] **P2** `per_dimension_top_k` 最优值实验（当前为经验值，需通过 recall/precision 指标迭代）
- [x] **P3** ~~外环如何评估预筛质量~~ → 已决策：三指标 recall/precision/boundary（见阶段 H）
- [x] **P4** ~~分块结果是否落盘~~ → 已决策：不落盘，replay = 推理链可溯即可
- [ ] **P5** 对话记录的特殊处理：对话轮次作为 TextUnit 的专用分块策略（chunking prompt 特化）
- [x] **P6** ~~外环可调量的归属~~ → 已决策：全部在 configs/，artifacts/ 只读
- [ ] **P7** fuzzy match 阈值（当前设为 0.85）是否需要暴露为外环可调参数——初步判断：不需要，属于内环实现细节
- [x] **P8** ~~Scorer 上下文传递策略~~ → 已决策：只传结构化证据，不传全文（见阶段 K）
- [x] **P9** ~~Consistency Checker + Adjudicator 合并~~ → 已决策：合并为 Score Reconciliation（阶段 L）
- [ ] **P10** feedback 是否需要 scoring_context（锚定样例）——初步判断：不需要，feedback 面向学生，锚定样例面向 scorer

---

> 最后更新：2026-03-30
> 当前进度：阶段 A-K 已完成，L/M 已规划
