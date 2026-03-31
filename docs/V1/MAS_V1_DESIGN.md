# MAS V1 系统设计文档

> 本文档是 MAS 系统 V1 阶段的设计全景，记录架构决策、设计讨论结论与迭代日志。
> 所有讨论均在**接入真实 LLM** 的视角下进行。
> 每次新的讨论结论、Review 发现、系统变更，均在对应章节追加，不覆盖历史。

---

## 零、全局设计原则

### 0.1 系统最终目标：评价学生解决复杂工程问题的能力

本系统的评价对象不局限于 ASAP Set 8 短文章。真实使用场景包括：

- 学生与 AI 模型的长对话记录（8000–20000 词）
- 关于工程问题的研究报告（含章节结构、代码、图表引用）
- 多文档混合输入

**所有架构决策必须在这个长文档、多类型输入的视角下成立。** ASAP Set 8 是当前的验证集，不是设计边界。

### 0.2 "可回放性"的正确含义

"Replay" 在本系统中的含义是：

> **学生或评价者读到 MAS 给出的分数和理由后，能够被其说服——能从理由中找到文本中实际存在的、不符合量规的具体依据。**

这不是"技术上能逐 bit 重现同一次运行"，而是**推理链的可解释性与文本可溯性**：

- `EvidenceSpan.text_quote` 必须是文档中真实存在的引文
- `ScoreHypothesis.rationale` 必须引用可识别的具体证据
- `feedback_text` 必须指向文本中可验证的问题

**推论**：Preprocess / Coverage 阶段不必强制追求 bit-for-bit 确定性（如固定的 document_id hash）。轻量 LLM 带来的语义理解收益，优先于纯确定性带来的技术回放便利。

### 0.3 所有设计决策默认外环存在

内环的每一个设计选择，都要在"外环会读取这些产物做诊断"的前提下作出：

- 落盘的数据结构和字段，要服务于外环的 Diagnose 和 Measure 能力
- 推理链（rationale、facet_findings、evidence_spans）是外环最核心的诊断材料
- Preprocess / Coverage 的输出质量，直接决定 Extractor 能给外环留下多少有用的上下文痕迹

### 0.4 Preprocess + Coverage 的职责定位

这两个阶段存在的唯一目的是：**让 Extractor 能抽得准、抽得快**。

- **Preprocess** 负责把原始文档切成语义完整的块（chunk），不是语言学意义的句子
- **Coverage** 负责为每个维度选出最相关的块，屏蔽无关上下文
- 两者共同解决 Extractor 面对长文档时的**上下文管理问题**

对长文档（工程报告/对话记录），当前 "full_scan 全传" 策略不可行：
6 个维度 × 全文 = 6 倍 token 消耗，且 LLM 对长文中段注意力下降（Lost in the Middle）。

**演进方向**：用轻量 LLM（Haiku 级别）做语义分块 + 维度相关性预筛。详见第七章。

### 0.5 双环边界铁律：configs 与 artifacts 的职责分离

> **内环建立机制，外环调节参数使之效果最佳。**

这条原则决定了整个系统中每一个值的归属：

```
外环能修改的 → 必须在 configs/ 中有对应表示
外环用来诊断的 → 必须保存在 artifacts/ 中
```

**具体对应关系**：

| 分类 | 存放位置 | 举例 |
|------|---------|------|
| 外环可调节的语义指令 | `configs/prompts/` | extraction/scoring/explanation/chunking/dimension_relevance prompt |
| 外环可调节的数值参数 | `configs/policies/` | top_k、token_threshold、score_distance 阈值、aggregation 权重 |
| 外环可调节的量规呈现 | `configs/rubrics/` | 量规内容固定，但 prompt 中对量规的描述方式可调 |
| 外环读取的评分结果 | `artifacts/eval/*/feedback.json` | canonical_score、composite |
| 外环读取的推理链 | `artifacts/eval/*/hypotheses.json` | rationale、confidence |
| 外环读取的证据材料 | `artifacts/eval/*/evidence_spans.json` | text_quote、facet_ids |
| 外环读取的覆盖质量 | `artifacts/eval/*/observations.json` | facet_findings、coverage miss |
| 外环读取的执行轨迹 | `artifacts/eval/*/run_trace.json` | 节点耗时、状态 |

**推论**：
- 内环代码中不应出现任何"魔法数字"或硬编码的调节参数——所有可调量必须来自 configs/
- 内环落盘数据必须完整记录推理过程——外环需要的诊断信息不能在运行时被丢弃
- 外环永远不直接修改代码或运行时状态——它只产出新版 configs/，再触发内环重新运行

---

## 一、内环：单篇评价流水线

以一篇作文文本为例，执行 `python scripts/eval.py --essay-id 20757`。

### 第零步：启动与初始化

`eval.py` 完成以下准备：

1. **读取 TSV**：从 `data/training_set_8.tsv` 找到目标 essay，取出原始文本和人工评分
2. **编译 Bundle**：`config_resolver` → `ConfigCompiler` 将 `configs/bundles/asap_set8_baseline.bundle.yaml` 编译为冻结的 `ResolvedArtifactBundle`
   - `rubric_snapshot`：6 个维度 I/O/V/W/S/C，每维 1–6 分级描述符
   - `policy_snapshot`：裁决阈值、Cusp Rule、聚合公式
   - bundle 整体 SHA-256 hash（保障回放一致性）
3. **构造 Provider**：从 `.env` 读取模型名和 API Key
   - `rater_1_provider`（DeepSeek）、`rater_2_provider`（Qwen）
   - 每个被 `LoggingProvider` + `GuardedProvider` 包装
4. **加载 Prompt 模板**：`evidence_extraction.yaml`、`scoring.yaml`、`explanation.yaml`
5. **构造 `PipelineRunner`**，创建 `EvaluationRequest`

### 第一步：预处理 `INIT → PREPROCESSED`

`preprocess.run(request)` — 当前纯确定性，未来可引入轻量 LLM

- 清洗文本，切分为 `TextUnit[]`（当前：按 `.!?。！？` 正则分句；演进方向：结构感知分块）
- 产出 `NormalizedDocument`

> **演进方向**：对长文档应改为结构感知分块（段落/章节/对话轮次），
> 而非语言学分句。块的粒度决定 Extractor 每次调用的上下文质量。
> 参见第 0.4 节。

### 第二步：覆盖规划 `PREPROCESSED → COVERAGE_PLANNED`

`coverage.run(document, rubric)` — 当前纯确定性，待引入轻量 LLM 预筛

- 调用 `rubric_core` 遍历维度，为每个维度生成 `CoveragePlan`
- 当前策略：`full_scan`，所有 TextUnit 传给所有维度
- 产出 `CoveragePlan[]`（维度数量由量规配置决定）

> **已知问题**：`full_scan` 对长文档不可行。
> **演进方向**：Coverage 阶段用轻量 LLM 做维度-块相关性预筛，
> 每个 CoveragePlan 的 `target_unit_ids` 只包含与该维度语义相关的 Top-K 块。
> 详见第七章"待决策问题"。

### 第三步：证据抽取 `COVERAGE_PLANNED → EVIDENCE_EXTRACTED`

`extractor.run(plan, document, rubric, provider, template)` — **调用 LLM**

- 每个维度渲染 `evidence_extraction` 模板 → 调用 LLM → 解析为 `EvidenceSpan[]`
- **6 次 LLM 调用**，产出 `all_spans_by_dim: Dict[dim_id → EvidenceSpan[]]`

### 第四步：观察构建 `EVIDENCE_EXTRACTED → OBSERVATION_BUILT`

`observer.run(spans, plan)` — 纯确定性，不调用 LLM

- 按 `facet_id` 分组，生成 `FacetFinding`，计算覆盖率 confidence
- 产出 6 个 `DimensionObservation[]`

### 第五步：双评审打分 `OBSERVATION_BUILT → SCORED`

`scorer.run(obs, spans, rubric, doc, provider, template, rater_id)` — **调用 LLM**

- 每个维度 × 每个 rater（rater_1、rater_2）渲染 `scoring` 模板 → 调用 LLM
- 解析为 `ScoreHypothesis`（含 `canonical_score` 整数 + `rationale` 文字理由）
- **6 维度 × 2 rater = 12 次 LLM 调用**

### 第六步：一致性检验 `SCORED → CONSISTENCY_CHECKED`

`consistency_checker.run(hypotheses, policy)` — 纯计算，不调用 LLM

- `score_distance` 触发器：`|rater_1分 - rater_2分| > 阈值`
- `pattern_match / Cusp Rule`：跨维度临界分值模式匹配
- 产出 `ConflictRecord[]`

**分叉：**
- **无冲突**：`deterministic_adjudicator` 取 rater_1 结果生成 `FinalDimensionDecision[]`，直接进入第八步
- **有冲突**：触发 rater_3 裁决

### 第七步（仅冲突时）：rater_3 重评 + 裁决 `→ ADJUDICATED`

`scorer.run(..., rater_3_provider, ...)` — **调用 LLM**

- rater_3 对全部 6 个维度重新评分（ASAP Set 8 "resolution read" 规则）
- **6 次额外 LLM 调用**

`adjudicator.run(conflicts, hypotheses, policy)`：
- 有冲突维度：采用 rater_3 分数（权威裁决）
- 无冲突维度：保持 rater_1/rater_2 均值
- 产出 `FinalDimensionDecision[]` + `AdjudicationRecord[]`

> rater_3 缺失时路由到 `HUMAN_REVIEW`，流水线提前终止。

### 第八步：聚合总分

`compute_composite(decisions, hypotheses, adjudications, policy)` — 纯计算

```
无裁决：avg(I)*2 + avg(O)*2 + avg(S)*2 + avg(C)*4  →  量程 10–60
有裁决：I_R3*2  + O_R3*2  + S_R3*2  + C_R3*4      →  量程 10–60
```
V/W 权重为 0，不计入总分。产出 `CompositeDecision`。

### 第九步：反馈生成 `FEEDBACK_RENDERED`

`feedback_agent.run(...)` — **调用 LLM**

- 每个维度用 `explanation` 模板生成自然语言解释
- **canonical_score 在此阶段之前已锁定**，explanation prompt 不影响 QWK

> 延伸讨论：explanation 阶段的 LLM 实际上具备发现"解释与分数矛盾"的隐含能力。
> 将其作为分数自检层（无法写出自洽解释时标记低置信度或触发重评）是可行的增强方向，
> 但不属于第一阶段优先项，待内环稳定后再考虑。

产出 `feedback` dict（含 `dimensions`、`summary`、`composite`）。

### 第十步：终止验证 `VALIDATED`

`terminal_validation` 检查所有维度有合法分数，通过后写入产物。

### LLM 调用统计

| 阶段 | 次数 | Provider |
|------|------|---------|
| 证据抽取 | 6 | 默认 provider |
| rater_1 评分 | 6 | rater_1_provider |
| rater_2 评分 | 6 | rater_2_provider |
| 反馈生成 | 6 | 默认 provider |
| **合计（无冲突）** | **24** | — |
| rater_3 评分（有冲突） | +6 | rater_3_provider |
| **合计（有冲突）** | **30** | — |

### 最终落盘产物（`artifacts/eval/{essay_id}/`）

| 文件 | 内容 |
|------|------|
| `feedback.json` | 各维度分数 + 解释 + composite 总分 |
| `hypotheses.json` | rater_1 / rater_2 各自原始分数（**rationale 待确认是否已落盘**） |
| `run_trace.json` | 每个节点执行时间、input/output ref、状态 |
| `report.md` | 人类可读的 Markdown 报告 |

---

## 二、外环：Strategy Agent 设计

### 2.1 设计前提

- 系统目标：让 MAS 评分与人类评分的一致性更高，以 QWK 为北极星。
- 单篇运行时**拿不到真实标签**，无法直接知道 QWK 是否变好。
- 因此"自学习/自优化"必须发生在**离线外环**，而非运行时编排器内部。

### 2.2 为什么学习不能放进运行时编排器

- 运行时直接"学"会破坏 bundle 冻结、可回放、可审计边界。
- 让编排器自由修改 prompt / 权重 / policy，很容易把 orchestrator 变成隐式 scorer。
- 运行时学习显著增加不稳定性、成本与重现难度。

结论：**运行时编排器可以更聪明，但学习与策略更新必须放在离线外环。**

### 2.3 双环结构

```
┌─────────────────────────────────────────────────────────────────┐
│  外环：Strategy Agent（待建，第二阶段）                          │
│                                                                  │
│  Collect → Measure → Diagnose → Propose → Evaluate → Promote    │
│                                                                  │
│  读取：artifacts/eval/*/   输出：新版 configs/ bundle（版本化）  │
└───────────────────────────┬─────────────────────────────────────┘
                            │ 下发新 bundle
┌───────────────────────────▼─────────────────────────────────────┐
│  内环：单篇评价流水线（现有系统，第一阶段完善）                  │
│                                                                  │
│  预处理 → 覆盖规划 → 证据抽取 → 双评审 → 一致性检验             │
│       → [裁决] → 聚合总分 → 反馈生成 → VALIDATED               │
│                                                                  │
│  产出：feedback.json / hypotheses.json / run_trace.json          │
└─────────────────────────────────────────────────────────────────┘
```

### 2.4 外环六步循环

| 步骤 | 职责 |
|------|------|
| **Collect** | 批量运行样本，收集 run_trace / hypotheses / feedback / 人工标签 |
| **Measure** | 计算 composite QWK、per-dim QWK、inter-agent consistency、conflict rate、third-rater trigger rate、score distribution |
| **Diagnose** | LLM 语义分析 K 条推理链，识别系统性误差模式，定位根因阶段 |
| **Propose** | LLM 直接生成新版 prompt 文件 / bundle 定义 |
| **Evaluate** | 触发内环批量验证，对比新旧 QWK |
| **Promote/Reject** | holdout 上稳定提升才晋级，否则回滚 |

**自动化程度**：全自动（C 级），同时输出人类可读摘要报告。

### 2.5 外环 Agent 的三项核心能力

- **误差归因能力**：区分"两位 scorer 一致地错"vs"adjudication 问题"vs"calibration 偏移"
- **策略搜索能力**：在可调旋钮空间内提出有依据的调整方向
- **分数校准能力**：识别系统性偏高/偏低，提出补偿策略

### 2.6 外环的硬边界

- 不直接给单篇文章改最终分
- 不绕过现有 scorer / adjudicator / router
- 不在运行时修改 rubric / policy / aggregation
- 没有 holdout 验证不允许晋级策略

---

## 三、外环输入输出详细设计

### 3.1 输入（四类）

| 来源 | 内容 |
|------|------|
| 指标摘要 | composite QWK、per-dim QWK、inter-agent consistency、conflict rate、score distribution（均值/方差对比） |
| K 条完整推理链（误差最大的 K 个样本） | EvidenceSpan 内容、DimensionObservation facet 覆盖、ScoreHypothesis.rationale 原文、ConflictRecord、FinalDimensionDecision、人类 ground truth |
| 当前 configs 快照 | bundle 版本号、scoring/extraction prompt 模板原文、量规描述符原文 |
| 历史迭代日志 | 历史各版本 QWK、每次调整内容与效果 |

### 3.2 Diagnose：LLM 语义分析，而非规则匹配

外环 LLM 阅读 K 条推理链，发现规则无法捕捉的系统性问题：

| 问题类型 | 示例 |
|---------|------|
| 证据与评分脱节 | rationale 引用了正面证据，但对应文本实际质量很低 |
| 评分语言习惯偏差 | 过高评分案例的 rationale 普遍出现"尝试表达"等宽松措辞 |
| 量规理解偏差 | scorer 把 voice 理解成了文风，但量规定义的是作者个性 |
| 证据缺口 | extraction 没有找到 conventions 维度的语法错误证据 |
| 跨维度混淆 | ideas_content 和 organization 分数总是同向偏移 |

归因粒度：**粗粒度**（定位到 extraction / scoring / adjudication / calibration 阶段即可）。

### 3.3 输出（两个层次）

- **层次 A（人类可读）**：误差模式描述 + 根因定位 + 具体指向的 prompt 片段
- **层次 B（机器可操作）**：外环 LLM 直接重写的新版 prompt 文件 + 新版 bundle 定义

---

## 四、外环可调整动作空间

### 4.1 ASAP Set 8 阶段（第一、二阶段）

```
可调（Prompt 层）：
  ├── scoring prompt（全局模板）
  ├── scoring prompt（per-dimension override）  ← 内环需新增此能力
  ├── extraction prompt（全局模板）
  └── extraction prompt（per-dimension override）← 内环需新增此能力

固定不动：
  ├── adjudication policy（Set 8 协议绑定）
  ├── aggregation weights（Set 8 协议绑定：I/O/S/C=2,2,2,4，V/W=0）
  ├── 量规内容（维度定义、分档描述符）
  └── 状态机结构
```

> 量规**内容**固定，但量规向 LLM 的**呈现方式**（facet 定义、描述符在 prompt 中的表达）属于 prompt 层，可调。

> explanation prompt 不影响 QWK（分数在此阶段之前已锁定），不纳入外环调整范围。

### 4.2 全新量规阶段（第三阶段）

Policy 无预设，外环可额外调整 adjudication threshold 和 aggregation weights。

---

## 五、系统全景：三层架构

### 5.1 完整系统愿景

目标是一个**通用的量规驱动评价系统**：

1. 用户提供量规（任意格式：txt / md / pdf / word）
2. 系统理解量规，自动生成评分配置
3. 用户传入待评文本
4. 系统输出评分 + 可解释报告
5. 外环持续优化，使评分越来越接近"人类用这套量规打的分"

### 5.2 三层结构

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 0: Rubric Ingestion（待建，第三阶段）                 │
│                                                              │
│  输入：txt / md / pdf / word 等非/半结构化量规文件           │
│  Step 1: 文档解析（→ 纯文本）                               │
│  Step 2: 结构提取（LLM）                                    │
│           维度、量表档数、描述符、裁决规则、聚合方式          │
│  Step 3: 配置生成                                           │
│           rubric.yaml / adjudication_policy.yaml /          │
│           aggregation.yaml / bundle.yaml                    │
│                                                              │
│  原则：在精确结构定义约束下直接生成，外环自动校准偏差        │
└───────────────────────────┬─────────────────────────────────┘
                            │ configs/ bundle
┌───────────────────────────▼─────────────────────────────────┐
│  Layer 1: 内环（现有，第一阶段完善）                         │
└───────────────────────────┬─────────────────────────────────┘
                            │ artifacts/eval/*/
┌───────────────────────────▼─────────────────────────────────┐
│  Layer 2: 外环（待建，第二阶段）                             │
└─────────────────────────────────────────────────────────────┘
```

### 5.3 推进优先顺序

| 阶段 | 目标 | 状态 |
|------|------|------|
| **第一阶段** | 完善内环：补齐落盘数据、支持 per-dimension prompt override | 进行中 |
| **第二阶段** | 建立外环：Strategy Agent，基于 ASAP Set 8 ground truth 跑通完整优化循环 | 待建 |
| **第三阶段** | 建立 Rubric Ingestion Layer，接通三层形成完整通用系统 | 待建 |

---

## 六、内环需补齐的落盘数据

| 数据 | 当前状态 | 外环用途 |
|------|---------|---------|
| `ScoreHypothesis.rationale` 原文 | **待确认** | 诊断 scoring prompt 问题 |
| `EvidenceSpan` 内容（文本、facet_id、confidence） | **待确认** | 诊断 extraction prompt 问题 |
| `DimensionObservation` facet 覆盖情况 | **待确认** | 判断 facet 呈现是否有效 |
| `canonical_score`（每个 rater 每个维度） | 应已有 | QWK 计算基础 |
| `ConflictRecord` 详情 | 部分有 | 诊断 adjudication 触发是否合理 |

**不需要保存：**

| 数据 | 原因 |
|------|------|
| 原始 LLM API 响应 | 信息已结构化提取，冗余 |
| Prompt 渲染后完整文本 | 可从模板 + 上下文随时重现 |
| `NormalizedDocument` 全量 TextUnit | 原始文本可重新切分 |
| `CoveragePlan` 详情 | 可从 rubric 配置重现 |

---

## 七、Preprocess + Coverage LLM 化：已决策设计

### 7.1 架构方案（已确定）

两次独立 LLM 调用，职责分离：

```
Call 1 [Haiku]  结构分块
  输入：原始文本 + 文档类型 hint
  输出：{"chunks": [{"id":"c0","title":"...","text":"..."},...]}

        ┌─ 常规文档（token < 阈值）：直接全文传入
        └─ 长文档（token ≥ 阈值）：硬切 + 摘要 → LLM 识别语义边界

Call 2 [Haiku × 6 并行]  维度相关性预筛
  输入：chunks 摘要（id+title+首句）+ 单个维度 facets 描述
  输出：{"relevant_chunk_ids": ["c2","c5","c1"]}
  兜底：任意维度失败 → 该维度 full_scan，不影响其他维度
```

**token 估算**（无需真实 tokenizer）：
- 英文：`len(text.split()) * 1.3`
- 中文：`len(text) * 1.5`

### 7.2 外环可调节旋钮（Preprocess + Coverage 专项）

遵循 0.5 节原则：**可调量全部在 configs/ 中有对应表示**。

#### 第一层：语义指令（最高影响，外环主要调节对象）

| 旋钮 | 位置 | 调节效果 |
|------|------|---------|
| 分块 prompt | `configs/prompts/chunking.yaml` | 块的粒度、文档类型专用指令、分块质量要求 |
| 维度相关性 prompt | `configs/prompts/dimension_relevance.yaml` | 查找重点描述、相关性判断松紧程度 |

#### 第二层：数值参数（configs/policies/chunking/ 管理）

| 旋钮 | 配置键 | 说明 |
|------|-------|------|
| `top_k`（per-dimension）| `per_dimension_top_k` | 每维度保留的 Top-K 块数；不同维度敏感度不同（conventions 宜大，voice 宜小） |
| `token_threshold` | `document_processing.token_threshold` | 常规 vs 长文档分支的切换阈值，默认 4000 |
| `target_chunk_size_hint` | `document_processing.target_chunk_size_hint` | 传给分块 LLM 的粒度建议 |

#### 配置文件：`configs/policies/chunking/asap_set8_chunking.yaml`

```yaml
schema_version: "2.0"

chunking_policy:
  policy_id: "asap_set8_chunking_v1"
  policy_version: "v1"

  document_processing:
    token_threshold: 4000
    target_chunk_size_hint: "100-500 words"

  coverage:
    default_top_k: 5
    fallback_to_full_scan_on_error: true
    per_dimension_top_k:       # 外环可单独调节每维度的 top_k
      ideas_content: 5
      organization: 4
      voice: 3
      word_choice: 4
      sentence_fluency: 4
      conventions: 6           # conventions 需覆盖更多（语法错误散布全文）
```

### 7.3 外环 Measure 新增指标（Preprocess + Coverage 专项）

这三个指标直接从落盘数据计算，不需要额外 LLM 调用：

| 指标 | 计算方式 | 信号含义 | 对应调节旋钮 |
|------|---------|---------|------------|
| `coverage_recall_rate` | `span.unit_id ∈ target_unit_ids` 的比率 | 低 → 过滤太激进，Extractor 被迫在规划外找证据 | 增大 `top_k` |
| `coverage_precision_rate` | 至少贡献 1 个 span 的 unit_id / target_unit_ids 总数 | 低 → 过滤太宽松，噪声上下文进入 Extractor | 减小 `top_k` 或收紧 relevance prompt |
| `chunk_boundary_quality` | `text_quote` 完整落在单个 TextUnit 内的比率 | 低 → 分块粒度不合适，span 横跨多块 | 调整 `target_chunk_size_hint` |

**实现**：这三个指标的计算逻辑加入 `scripts/compute_qwk.py` 或单独的 `scripts/compute_coverage_metrics.py`。

### 7.4 coverage miss 的落盘（artifacts 补充）

为支持 `coverage_recall_rate` 的计算，`observations.json` 需补充记录：

> 当 Extractor 找到的 span 的 `unit_id` 不在该维度的 `target_unit_ids` 里时，
> 记录为 `coverage_miss`。

在 `DimensionObservation` 中增加字段 `coverage_miss_span_ids: List[str]`（默认空列表），
由 runner 在 observer 阶段后、feedback 阶段前计算并回填。

---

## 八、Review 记录

> 格式：`#### [日期] 标题` + 发现内容 + 结论/决策

---

## 九、调整与迭代记录

> 格式：`#### [日期] 变更标题` + 变更内容 + 涉及文件

#### [2026-03-29] 补全落盘数据：EvidenceSpan + DimensionObservation

- 新增 `runner.last_spans` / `runner.last_observations` 属性
- eval.py 每次评估新增保存 `evidence_spans.json` + `observations.json`
- 涉及文件：`src/pipeline/runner.py`、`scripts/eval.py`

#### [2026-03-29] 修复 minimum_evidence_units 读取

- coverage.py 原来用 `max(1, len(facets))` 计算，忽略了量规中的 `evidence_requirements.minimum_evidence_units`
- 修复后直接读量规配置值，保持代码与量规一致
- 涉及文件：`src/agents/coverage.py`

#### [2026-03-29] 补充中文分句支持

- preprocess.py 正则扩展为 `(?<=[.!?。！？])\s*`，支持中文句末标点
- 涉及文件：`src/agents/preprocess.py`

#### [2026-03-29] 确立全局设计原则（见第零章）

- 明确系统目标为长文档/复杂工程问题评价，不局限于 ASAP Set 8
- 明确"可回放性"的正确含义：推理链可解释、文本可溯，而非 bit-for-bit 确定性
- 确立"所有设计决策默认外环存在"原则
- 确立"Preprocess + Coverage 服务于 Extractor"的职责定位

#### [2026-03-30] 确立 configs/artifacts 双环边界铁律（见第零章 0.5 节）

- 原则：外环可修改的内容必须在 configs/ 中有对应表示；外环用于诊断的内容必须保存在 artifacts/ 中
- 内环建立机制，外环调节参数，两者通过 configs/ 和 artifacts/ 解耦
- 所有内环代码中的可调量必须来自 configs/，不允许硬编码魔法数字

#### [2026-03-30] 确定 Preprocess + Coverage LLM 化方案（见第七章）

- 两次独立调用：Call 1（Haiku）结构分块 + Call 2（Haiku ×6 并行）维度相关性预筛
- 长文档分支：token 超阈值时先硬切+摘要再 LLM 分块
- 新增 chunking_policy.yaml，管理 top_k（per-dimension）、token_threshold、chunk_size_hint
- 外环新增三个 Measure 指标：coverage_recall_rate、coverage_precision_rate、chunk_boundary_quality
- DimensionObservation 补充 coverage_miss_span_ids 字段，支持 recall_rate 计算

---
