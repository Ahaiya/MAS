# 外环设计文档

> 本文档描述 MAS 评分系统外环的完整设计意图

---

## 0. 系统全局定位

本系统分为内环和外环两层：

**内环**是单次评分流水线，负责对一篇文本执行完整的评估并产出结构化结果。内环已基本实现，核心编排器为 `src/pipeline/runner.py`。

**外环**是优化控制循环，负责在一批有人类标注的样本上，通过迭代修改内环配置，使系统评分与人类专家的一致性（QWK）向目标值收敛。

**优化目标**： composite QWK 达到 0.8 以上。QWK 是代理指标，最终质量标准是"评分理由与分数匹配、言之有理、符合量规语义"。【不要求 各个观测点的 QWK，但是可以作为中间探针】

---

## 0.5 评价任务启动流程（Task Setup）

每次评价任务开始前，需要完成以下启动流程。这是整个系统的入口，产出的配置文件是内环和外环共同的基础。

### 输入

- **量规原文**：`configs/rubrics/source/rubric.md`，描述完整的能力指标体系（如复杂工程问题解决能力评价指标）。此文件永久只读，任何流程不得修改。
- **Task Brief**：教师输入的自由文本，描述本次工程项目的背景、核心问题和学生需要产出的材料类型。

### 三层冻结架构

启动流程产出的配置文件分三层，冻结程度不同：

```
层次 1：量规原文
  文件：configs/rubrics/source/rubric.md
  状态：永久只读，系统级保护
  说明：评价任务定义的最终权威来源

层次 2：选定指标 + 观测点定义
  文件：configs/rubrics/tasks/{task_id}_rubric.yaml
  状态：本次任务内冻结，外环不可修改
  说明：本次评价"评什么"的完整定义【当前执行不正确， 一般不会全覆盖所有维度，评测的基本单元是观测点，层次结构是 维度A-二级指标 A4- 观测点A4-1】（一般选择 3 个观测点）

层次 3：对观测点的理解方式（这也就是 观测点的具体评分细则，举例，对应 A4.doc中的量规列）
  文件：configs/prompts/tasks/{task_id}_scoring_context.yaml
  状态：外环可迭代优化
  说明：内环 scorer 如何理解每个观测点，冷启动时留空，由外环逐步填入
```

### Step 1｜AI 解析与推荐

系统读取量规原文 + Task Brief，**由外环 Agent自动完成**：

- 从全部**二级指标**中推荐本次适用的指标子集
- 为每个选中指标生成 3 个观测点草案
- 每个观测点包含：名称 + **四级行为锚点描述（优秀 4-5 分 / 良好 3-4 分 / 合格 2-3 分 / 待改进 1-2 分）**

四级锚点的**能力判断标准**（第一类）由 AI 从量规语义直接推理得出。**任务特定的情境细节**（第二类，如具体工具名称、项目场景细节）在冷启动后由外环迭代填入 `configs/prompts/tasks/{task_id}_scoring_context.yaml`，不强求在此阶段生成。

### Step 2｜带脚手架的对话确认

AI 呈现草案，教师通过 **CLI 脚手架式对话流程** 迭代修改，直到显式确认。支持：

- 修改某个观测点的名称或锚点描述
- 删除或新增观测点
- **直接覆写某个观测点的全部内容（人工设定优先**）

推荐 CLI 形态：

```bash
python -m scripts task draft --task-id xxx --task-brief-file ...
python -m scripts task show --task-id xxx
python -m scripts task revise --task-id xxx --instruction "..."
python -m scripts task confirm --task-id xxx
```

**确认后立即冻结**，编译为 `configs/rubrics/tasks/{task_id}_rubric.yaml`。随后进行格式校验，直到通过。此后本次任务内不再接受对该文件的任何修改。

同时，`task confirm` 负责把当前活动任务绑定到 `configs/bundles/engineering_eval_baseline.bundle.yaml`，即：

- 更新 bundle 中的 `rubric_*` 引用到当前任务的 frozen rubric
- 更新 bundle 中的 `scoring_context_*` 引用到当前任务的 scoring context
- 同步初始化与当前任务维度相关的可变配置（例如 aggregation policy 中的维度权重键）

### Step 3｜冷启动准备

`{task_id}_scoring_context.yaml` 初始化为**最小合法 YAML 壳子**，等待外环第一个 artifacts 产出后由 Agent 填入。

说明：

- 不是字面意义上的空文件
- 必须保留 `schema_version` 和 `scoring_context` 结构
- 文本字段初始化为空字符串
- `score_anchors` 初始化为空数组

### 产出文件示例

```
configs/rubrics/
  source/
    rubric.md                              # 永久只读
  tasks/
    {task_id}_rubric.yaml                    # 层次2：冻结
configs/prompts/tasks/{task_id}_scoring_context.yaml       # 层次3：冷启动留空，外环迭代
```

---

## 1. 外环整体架构

### 1.1 外环是一个在线 Agent 循环

外环不是离线批处理器，而是每轮迭代都有 Agent 参与推理的在线决策系统。

单轮迭代的完整流程：

```
Phase 1｜读取
  加载实验日志摘要（experiment_log.yaml）
  加载当前配置状态快照

Phase 2｜决策
  Agent 推理：当前最薄弱的环节是什么？
  决定本轮改哪一个变更单元、如何改
  产出结构化变更提案（ChangeProposal）

Phase 3｜执行
  变更执行器（ConfigPatcher）校验并应用变更提案
  触发内环，对训练样本集（或子集）执行评估
  调用对应的评估探针，获取中间信号

Phase 4｜复盘
  Agent 基于探针结果撰写本轮 verdict
  提出 next_hypothesis（下一轮优化方向）

Phase 5｜归档
  将完整迭代记录写入实验日志
  检查终止条件
```

### 1.2 Agent 的推理依据

每轮 Phase 2，Agent 持有以下输入：

- `experiment_log.yaml`：历史迭代记录摘要
- 当前各配置文件的状态
- 可调用的评估探针列表（见第 3 节）
- 搜索空间约束规则（见第 4 节）

Agent 完全自主决定：本轮改哪个变更单元、改什么内容、用哪个探针验证。不需要人工介入。**【最重要的观察内容是 所给的分数 和 给出的 评分理由是否符合量规 逻辑是否通畅， 对量规的理解是否准确合理； 探针只是数学上的依据和参考】**

---

## 2. 实验日志格式

实验日志是外环 Agent 的记忆载体，路径为 `experiments/experiment_log.yaml`。

每轮迭代产出一条记录，格式如下：

```yaml
- iteration: 12
  timestamp: "2026-04-01T10:23:00"
  changed_unit: "xxx"
  change_description: "强化了对主旨清晰度的锚定描述，增加负向示例"
  target_file: "configs/xxx"
  target_path: "xxx"
  probe_used:
    - rater_consistency_probe
    - qwk_probe
  probe_results:
    qwk_composite: 0.71 -> 0.72
  verdict: "有效。xxx"
  next_hypothesis: "xxx"
  config_snapshot_path: "experiments/snapshots/iter_012/"
```

**注意**：`verdict` 和 `next_hypothesis` 由本轮 Agent 在拿到探针结果后（Phase 4）写入，不是由下一轮写入，避免归因被后续决策意图污染。

---

## 3. 评估探针体系（Evaluation Probes）

探针是外环 Agent 可调用的轻量验证工具。不同的配置变更对应不同的探针，不需要每次都等完整的 QWK 信号。

| 探针名称 | 对应内环层次 | 验证粒度 | 数据来源 |
|---|---|---|---|
| `coverage_probe` | Chunking + Coverage | chunk 级 | `observations.json` 中的 `coverage_recall_rate`、`chunk_boundary_quality` |
| `evidence_quality_probe` | Evidence extraction | span 级 | `evidence_spans.json` 中的 quote 对齐率、`facet_findings` 完整性 |
| `observation_confidence_probe` | Observation | 维度级 | `observations.json` 中的 `observation_confidence` 分布 |
| `rater_consistency_probe` | Scoring | 维度级 | `hypotheses.json` 中的 rater 间分数分布、`confidence` 均值 |
| `conflict_pattern_probe` | Reconciliation | 批次级 | `conflicts.json` 中的 `conflict_type` 分布、触发率 |
| `resolution_cost_probe` | Adjudication | 批次级 | `adjudication_records.json` 中的三评触发率、fallback 率 |
| `feedback_grounding_probe` | Feedback | 篇级 | `feedback.json` 中的 descriptor-evidence 链闭合率、violations |
| `qwk_probe` | Composite output | 批次级 | `python -m scripts metrics qwk` 产出的 per-dimension + composite QWK |
| `cost_probe` | RunTrace（全链路） | 调用级 | `run_trace.json` 中的 per-stage token、latency、retry 率 |

**Agent 使用规则**：Agent 自主决定本轮使用哪些探针。原则是：变更作用在哪一层，就用那一层及其下游的探针验证，不必每次都跑到 QWK。例如，只改了 `evidence_extraction` prompt，使用 `evidence_quality_probe` 即可，无需等 QWK。

---

## 4. 搜索空间约束

### 4.1 代码层硬约束（ConfigPatcher 强制执行，不可绕过）

- **单变更原则**：每轮 ChangeProposal 只能包含一个变更单元。超过一个，执行器直接拒绝，不执行内环。
- **白名单校验**：只有白名单内的文件路径允许写入。rubric 文件不在白名单内，物理上不可修改。
- **格式校验**：变更执行后，立即验证 YAML 合法性。验证失败则自动回退到本轮快照，记录失败原因。
- **快照机制**：每轮变更执行前，将当前配置目录完整复制到 `experiments/snapshots/iter_{N}/`，按迭代编号保存，保留最近 20 轮。

### 4.2 System prompt 软策略（Agent 推理遵守）

- **搜索优先级**：P1 scoring → P2 coverage/extraction → P3 adjudication → P4 feedback。上游层问题未收敛前，不往下游深挖。具体来说：`coverage_probe` recall rate 低于阈值时，优先解决 P2 层问题，不跳到 P1 scoring。
- **转移规则**：同一变更单元连续失败（probe 无改善）2 次，强制转移到其他变更单元。
- **回退触发**：某轮变更导致 QWK 显著下降（超过 0.03），自动回退配置，并在实验日志中将该变更方向标记为"禁区"。
- **探索模式**：连续 5 轮 QWK 无改善，Agent 进入探索模式，允许尝试更激进的变更（如大幅调整 reasoning budget、重写整段 prompt，而非局部 patch）。

---

## 5. 变更执行器（ConfigPatcher）

ConfigPatcher 是外环 Agent 和配置文件之间的强制中间层。Agent 永远不直接操作文件，只产出结构化 ChangeProposal，由 ConfigPatcher 执行。

### 5.1 ChangeProposal 格式

```yaml
change_unit: "xxx"
change_type: "field_patch"          # field_patch | file_overwrite
target_file: "configs/prompts/scoring_context.yaml"
target_path: ""
new_value: |
  当评分 xxx 时，重点关注...
rationale: "xxx"
```

### 5.2 ConfigPatcher 执行流程

```
1. 白名单校验 → target_file 不在白名单则拒绝，返回错误
2. 单变更校验 → ChangeProposal 包含多个变更单元则拒绝
3. 快照 → 将当前 configs/ 快照到 experiments/snapshots/iter_{N}/
4. Patch 执行 → 按 target_path 定位字段，写入 new_value
5. 格式验证 → 验证修改后的 YAML 合法性
6. 失败回退 → 格式验证失败则从快照恢复，记录失败原因
```

### 5.3 可修改文件白名单

```
configs/prompts/tasks/*_scoring_context.yaml
configs/prompts/scoring.yaml
configs/prompts/evidence_extraction.yaml
configs/prompts/explanation.yaml
configs/prompts/chunking.yaml
configs/prompts/dimension_relevance.yaml
configs/prompts/scoring_overrides/*.yaml
configs/prompts/explanation_overrides/*.yaml
configs/prompts/evidence_extraction_overrides/*.yaml
configs/policies/adjudication/*.yaml
configs/policies/chunking/*.yaml
configs/policies/aggregation/*.yaml
configs/policies/explanation/*.yaml
```

`engineering_eval_baseline.bundle.yaml` 的角色调整为：

- 仅保存**本次任务内冻结**的入口配置
- 包括：模型接入配置、当前活动任务的 rubric/scoring_context 引用、任务元数据
- 外环迭代过程中**不得修改**
- 只有 Task Setup 的 `confirm` 动作可以更新它，用于切换当前活动任务

**明确不可修改**：

- `configs/rubrics/` 下所有文件（量规原文和观测点定义冻结，变更破坏 QWK 口径）
- 所有 bundle 文件——bundle 在任务期内是冻结入口配置，只能由 Task Setup 在确认任务时更新


