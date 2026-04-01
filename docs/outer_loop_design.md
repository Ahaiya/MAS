# 外环设计文档（供 Claude Code / Codex 阅读）

> 本文档描述 MAS 评分系统外环的完整设计意图，以及内环现有代码需要配合修正的部分。
> 阅读本文档后，应能制定出外环的实现计划，以及内环的改造任务清单。

---

## 0. 系统全局定位

本系统分为内环和外环两层：

**内环**是单次评分流水线，负责对一篇文本执行完整的评估并产出结构化结果。内环已基本实现，入口为 `scripts/eval.py`，核心编排器为 `src/pipeline/runner.py`。

**外环**是优化控制循环，负责在一批有人类标注的样本上，通过迭代修改内环配置，使系统评分与人类专家的一致性（QWK）向目标值收敛。外环尚未实现，是本文档的主要设计对象。

**优化目标**：在训练样本集上，per-dimension QWK 和 composite QWK 达到 0.8 以上。QWK 是代理指标，最终质量标准是"评分理由与分数匹配、言之有理、符合量规语义"。

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
  说明：本次评价"评什么"的完整定义

层次 3：对观测点的理解方式
  文件：configs/prompts/tasks/{task_id}_scoring_context.yaml
  状态：外环可迭代优化
  说明：内环 scorer 如何理解每个观测点，冷启动时留空，由外环逐步填入
```

### Step 1｜AI 解析与推荐

系统读取量规原文 + Task Brief，自动完成：

- 从全部二级指标中推荐本次适用的指标子集
- 为每个选中指标生成 3 个观测点草案
- 每个观测点包含：名称 + 四级行为锚点描述（优秀 4-5 分 / 良好 3-4 分 / 合格 2-3 分 / 待改进 1-2 分）

四级锚点的**能力判断标准**（第一类）由 AI 从量规语义直接推理得出。**任务特定的情境细节**（第二类，如具体工具名称、项目场景细节）在冷启动后由外环迭代填入 `scoring_context.yaml`，不强求在此阶段生成。

### Step 2｜带脚手架的对话确认

AI 呈现草案，教师通过对话方式迭代修改，直到显式确认。支持：

- 修改某个观测点的名称或锚点描述
- 删除或新增观测点
- 直接覆写某个观测点的全部内容（人工设定优先）

**确认后立即冻结**，编译为 `{task_id}_rubric.yaml`。此后本次任务内不再接受对该文件的任何修改。

### Step 3｜冷启动准备

`{task_id}_scoring_context.yaml` 初始化为空文件，等待外环第一批 artifacts 产出后由 Agent 填入。

### 产出文件示例

```
configs/rubrics/
  source/
    rubric.md                              # 永久只读
  tasks/
    task_A4_rubric.yaml                    # 层次2：冻结
    task_A4_scoring_context.yaml           # 层次3：冷启动留空，外环迭代
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

Agent 完全自主决定：本轮改哪个变更单元、改什么内容、用哪个探针验证。不需要人工介入。

---

## 2. 实验日志格式

实验日志是外环 Agent 的记忆载体，路径为 `experiments/experiment_log.yaml`。

每轮迭代产出一条记录，格式如下：

```yaml
- iteration: 12
  timestamp: "2026-04-01T10:23:00"
  changed_unit: "scoring.calibration_notes.ideas_content"
  change_description: "强化了对主旨清晰度的锚定描述，增加负向示例"
  target_file: "configs/prompts/scoring_context.yaml"
  target_path: "calibration_notes.ideas_content"
  probe_used:
    - rater_consistency_probe
    - qwk_probe
  probe_results:
    rater_consistency_ideas_content: 0.74 -> 0.81
    qwk_ideas_content: 0.69 -> 0.73
    qwk_composite: 0.71 -> 0.72
  verdict: "有效。ideas_content 维度 rater 一致性显著提升，QWK 同步改善。"
  next_hypothesis: "organization 维度 rater 分歧率仍高（0.68），下轮针对其 score anchors。"
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
| `qwk_probe` | Composite output | 批次级 | `scripts/compute_qwk.py` 产出的 per-dimension + composite QWK |
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
change_unit: "scoring.calibration_notes.ideas_content"
change_type: "field_patch"          # field_patch | file_overwrite
target_file: "configs/prompts/scoring_context.yaml"
target_path: "calibration_notes.ideas_content"
new_value: |
  当评分 ideas_content 时，重点关注...
rationale: "rater_consistency_probe 显示该维度分歧率 0.74，高于阈值 0.5"
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

### 5.3 可修改文件白名单（初始建议）

```
configs/prompts/scoring_context.yaml
configs/prompts/tasks/*_scoring_context.yaml
configs/prompts/scoring.yaml
configs/prompts/scoring_overrides/*.yaml
configs/prompts/evidence_extraction.yaml
configs/prompts/evidence_extraction_overrides/*.yaml
configs/prompts/explanation.yaml
configs/policies/adjudication/asap_set8_default.yaml
configs/policies/chunking/asap_set8_chunking.yaml
configs/policies/aggregation/asap_set8_composite.yaml
configs/bundles/asap_set8_baseline.bundle.yaml  （仅推理参数字段）
  允许字段：temperature / max_tokens / thinking_budget
  禁止字段：model / model_id（模型选择须由人工决定）
```

**明确不可修改**：

- `configs/rubrics/` 下所有文件（量规原文和观测点定义冻结，变更破坏 QWK 口径）
- 所有 bundle 文件中的模型选择字段（`model`、`model_id`）——模型选择涉及系统级成本决策，超出外环 Agent 的权限范围

---

## 6. 冷启动设计

第一次启动外环时，实验日志为空，Agent 需要从第一批 artifacts 数据自主诊断系统瓶颈。

**冷启动流程**：

1. 先用默认配置（`asap_set8_baseline.bundle.yaml`）跑完训练样本集（30 篇）
2. 运行全部探针，产出初始诊断报告
3. Agent 读取探针报告 + 实验日志（空）+ 一份人工撰写的初始观察文件（`experiments/cold_start_notes.md`）
4. Agent 产出第一条 ChangeProposal，开始迭代

**`cold_start_notes.md` 的作用**：提供人工观察到的最明显失败模式，作为 Agent 冷启动的可靠起点，避免第一步就走偏。格式自由，内容例如："当前 conventions 维度 QWK 最低（0.51），rater_2 系统性高于 rater_1 约 1 分；ideas_content 维度 evidence 抽取有明显遗漏。"

---

## 7. 样本集设计

- **训练集**：30 篇，带人类标注分数，外环在这批样本上迭代优化
- **验证集**：另行准备，建议 10-15 篇，刻意包含边界案例（极高分、极低分、写作风格异常）
- **评估原则**：每轮外环迭代使用固定的训练集，保证 QWK 信号可比，排除抽样随机性

---

## 8. 目录结构（外环新增）

```
experiments/
  experiment_log.yaml          # 实验日志主文件
  cold_start_notes.md          # 人工初始观察（冷启动用）
  snapshots/
    iter_001/                  # 第 1 轮迭代前的配置快照
    iter_002/
    ...
  probes/
    iter_001_coverage.json     # 各轮探针原始输出（可选存档）
    iter_001_qwk.json
    ...

src/
  outer_loop/
    agent.py                   # 外环 Agent 主循环
    config_patcher.py          # 变更执行器（ConfigPatcher）
    probes.py                  # 探针调用接口
    experiment_log.py          # 实验日志读写
    prompts/
      outer_loop_system.md     # Agent system prompt（含搜索空间约束）
      outer_loop_user.md       # 每轮 user prompt 模板
```

---

## 9. 内环现有代码需要配合修正的部分

以下是内环现有实现中，外环依赖但当前尚未完全就绪的部分：

### 9.1 必须修正（外环依赖）

**retry 限额配置化**
- 现状：`CheckpointManager(max_retries=2)` 写死在 `src/pipeline/runner.py`
- 需要：移入 bundle 配置，让外环可以通过 ConfigPatcher 调整

**批量执行入口**
- 现状：`scripts/eval.py` 每次只处理单篇或整个 TSV
- 需要：提供一个 `batch_eval(sample_ids: List[str], bundle_ref: str) -> List[RunResult]` 接口，供外环 Agent 调用指定样本子集

**探针计算脚本统一化**
- 现状：`scripts/compute_qwk.py`、`scripts/compute_coverage_metrics.py` 是独立脚本，输出格式不统一
- 需要：将所有探针封装为统一接口，输入为 `artifacts/` 路径，输出为标准化的探针结果字典，供 `src/outer_loop/probes.py` 调用

### 9.2 建议修正（提升外环可靠性）

**artifacts 目录结构按 iteration 隔离**
- 现状：artifacts 按 essay_id 存储，多次 run 会覆盖
- 建议：增加 `iter_{N}/` 层级，保留各轮迭代的完整产出，方便外环做跨轮对比

**provider system prompt 通道独立化**
- 现状：大部分 agent 调用只传 `prompt`，没有独立的 system prompt 层
- 建议：将 system prompt 拆成可单独调优的配置层，让外环可以精准修改 system prompt 而不影响 user prompt

---

## 10. 达不到目标时的决策树

当外环迭代多轮后 QWK 仍停滞在目标以下：

```
连续 5 轮 QWK 无改善
  → 外环 Agent 进入探索模式
  → 尝试更激进变更（大幅重写 prompt、调整 reasoning budget、
    改变 adjudication 策略等）

探索模式仍无改善
  → 人工介入，审查以下两个方向：
    1. 架构层面：evidence extraction 策略是否需要重构？
       observation 到 scoring 的信息损失是否过大？
    2. 量规层面：rubric 定义与人类标注是否存在系统性不一致？
       人类标注本身的噪声是否已是 QWK 上限？

注意：QWK 是代理指标。
若 QWK 达不到 0.8，但抽样审查显示"理由与分数匹配、言之有理"，
系统仍可认为达到可用标准，进入验证集评估阶段。
```

---

## 11. 迁移到工程评价场景的注意事项

本系统当前在 ASAP Set 8（写作评分）上开发，最终目标是迁移到评价学生解决复杂工程问题的能力。

**可直接复用**：
- 整体三层认知框架：抽取 → 评分 → 反馈
- 外环优化循环的全部机制
- ConfigPatcher、实验日志、探针体系的架构

**迁移时必须重写**：
- `rubric` 文件（量规定义完全不同）
- `evidence_extraction` prompt 和 facet 定义（工程问题文本的证据是认知行为痕迹，不是语言表达质量，quote 级对齐可能不适用）
- `scoring_context`（dataset notes、calibration notes 全部重写）
- 冷启动的初始观察文件

**迁移风险提示**：
工程评价场景的量规语义是隐式的（"识别核心约束"、"合理权衡方案"），在文本中难以找到直接的 quote 级证据。extraction 层的重写难度显著高于 ASAP 场景，需要单独设计 facet 和证据类型。
