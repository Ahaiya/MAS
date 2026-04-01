# 外环实现计划（执行对照清单）

> 本文档是 `docs/outer_loop_design.md` 的落地执行计划。
> 状态标记：`[ ]` 未开始 / `[~]` 进行中 / `[x]` 已完成

---

## 现状确认（编写本计划时的代码库快照）

| 组件 | 状态 |
|------|------|
| `src/outer_loop/metrics/` | 已完成（qwk、consistency、export 已从 evaluation 迁移） |
| `src/outer_loop/datasets/` | 空占位 |
| `src/outer_loop/experiments/` | 空占位 |
| `src/outer_loop/optimization/` | 空占位 |
| `scripts/compute_qwk.py` | 已调用 outer_loop.metrics |
| `scripts/compute_coverage_metrics.py` | 独立脚本，未统一接口 |
| `runner.py` CheckpointManager | `max_retries=2` 硬编码（line 483） |
| `scripts/eval.py` | 有批量模式但无程序化 API |
| `experiments/` 目录 | 不存在 |

---

## Phase 0：内环改造（外环前置依赖）

> 三条任务可以并行。
> 状态：已完成（2026-04-01）

### Task 0.0｜创建 experiments/ 目录骨架

- [x] 新建 `experiments/experiment_log.yaml`（空列表 `[]`）
- [x] 新建 `experiments/cold_start_notes.md`（模板占位）
- [x] 新建 `experiments/snapshots/.gitkeep`
- [x] 新建 `experiments/probes/.gitkeep`
- [x] `.gitignore` 排除 `experiments/snapshots/**` 和 `experiments/probes/**` 的动态内容（保留 `.gitkeep`）

**验收**：目录结构存在，`git status` 中 snapshots/probes 子目录不被跟踪。

---

### Task 0.1｜retry 限额配置化

**改动文件**：
- `configs/bundles/asap_set8_baseline.bundle.yaml`
- `src/pipeline/runner.py`

**步骤**：
- [x] 在 bundle YAML 中新增字段：
  ```yaml
  operational_params:
    max_retries: 2
  ```
- [x] `src/agents/config_resolver.py`（或 `ResolvedArtifactBundle`）：解析 `operational_params.max_retries`，挂载到可从 runner 访问的位置
- [x] `runner.py:483`：`CheckpointManager(run_id, max_retries=2)` 改为从 bundle 读取，缺失时 fallback 到 `2`

**验收**：
- `max_retries=2` 不再出现在 runner.py 中
- 现有 mock 回归测试（157 passed）继续全部通过
- 手动将 bundle 中 `max_retries` 改为 `1`，单测可验证行为变化

---

### Task 0.2｜批量评估程序化 API

**改动文件**：
- 新建 `src/outer_loop/experiments/batch_runner.py`
- 小幅重构 `scripts/eval.py`

**步骤**：
- [x] 定义 `RunResult` dataclass（essay_id, success, output_dir, trace_dict, feedback_dict）
- [x] 从 `scripts/eval.py:_run_single()` 提取不依赖 typer/CLI 的核心逻辑到 `batch_runner.py`
- [x] 实现：
  ```python
  def batch_eval(
      sample_ids: list[str],
      bundle_path: Path,
      tsv_path: Path,
      output_base: Path,
      iter_id: str | None = None,
  ) -> list[RunResult]
  ```
  - `iter_id` 非空时，产出目录 = `{output_base}/iter_{iter_id}/{essay_id}/`
  - `iter_id` 为空时，产出目录 = `{output_base}/{essay_id}/`（保持与现有行为兼容）
- [x] `scripts/eval.py` 中批量模式改为调用 `batch_eval()`，保持 CLI 行为不变

**验收**：
- `python scripts/eval.py --limit 1 --mock-provider` 输出不变
- 以 `iter_id="iter_001"` 调用后，artifacts 写入 `artifacts/eval/iter_001/{essay_id}/`

---

### Task 0.3｜探针接口统一化

**改动文件**：
- 新建 `src/outer_loop/probes.py`

**步骤**：
- [x] 定义基础数据结构：
  ```python
  @dataclass
  class ProbeResult:
      probe_name: str
      essay_count: int
      metrics: dict[str, float | int | None]
      per_essay: dict[str, dict] | None = None
  ```
- [x] 实现以下 9 个探针函数（输入：`artifacts_dir: Path` + 可选过滤参数，输出：`ProbeResult`）：
  - [x] `coverage_probe` — 封装 `compute_coverage_metrics.py:compute_metrics_for_essay()`，聚合 recall/precision/boundary
  - [x] `evidence_quality_probe` — 读 `evidence_spans.json`，计算 quote 对齐率 + facet 完整性
  - [x] `observation_confidence_probe` — 读 `observations.json`，统计 confidence 分布（均值、低置信占比）
  - [x] `rater_consistency_probe` — 封装 `outer_loop.metrics.consistency`，返回 per-dimension 分歧率
  - [x] `conflict_pattern_probe` — 读 `conflicts.json`，统计 conflict_type 分布 + 触发率
  - [x] `resolution_cost_probe` — 读 `adjudication_records.json`，计算三评触发率 + fallback 率
  - [x] `feedback_grounding_probe` — 读 `feedback.json`，检查 descriptor-evidence 链闭合率
  - [x] `qwk_probe` — 封装 `outer_loop.metrics.qwk`，返回 per-dimension + composite QWK（需要 tsv_path）
  - [x] `cost_probe` — 读 `run_trace.json`，统计 per-stage token / latency / retry
- [x] 实现调度入口：
  ```python
  def run_probe(probe_name: str, artifacts_dir: Path, **kwargs) -> ProbeResult
  def run_probes(probe_names: list[str], artifacts_dir: Path, **kwargs) -> dict[str, ProbeResult]
  ```

**验收**：
- 对现有 `artifacts/eval/` 中任意一篇运行所有探针，均返回有效 `ProbeResult`
- 缺失某个 JSON 文件时，探针返回空 metrics 而不是抛异常

---

## Phase 1：外环核心基础设施

> 1.1 和 1.2 可并行，1.3 依赖前两者的接口。

### Task 1.1｜实验日志（experiment_log.py）

**新建文件**：`src/outer_loop/experiments/experiment_log.py`

**步骤**：
- [ ] 定义 `IterationRecord` dataclass，字段与 `outer_loop_design.md §2` 完全对齐：
  ```python
  @dataclass
  class IterationRecord:
      iteration: int
      timestamp: str
      changed_unit: str
      change_description: str
      target_file: str
      target_path: str
      probe_used: list[str]
      probe_results: dict[str, Any]
      verdict: str
      next_hypothesis: str
      config_snapshot_path: str
  ```
- [ ] 实现 `ExperimentLog` 类：
  - [ ] `load(path: Path) -> ExperimentLog`（文件不存在时初始化为空）
  - [ ] `append(record: IterationRecord)` — 追加并立即写盘（YAML）
  - [ ] `latest() -> IterationRecord | None`
  - [ ] `last_n(n: int) -> list[IterationRecord]` — 供 Agent context 截取
  - [ ] `count_consecutive_no_improvement(unit: str) -> int` — 判断强制转移条件
  - [ ] `is_forbidden_unit(unit: str) -> bool` — QWK 下降时的禁区标记
  - [ ] `mark_forbidden(unit: str, reason: str)` — 写入禁区记录
  - [ ] `consecutive_no_improvement_global() -> int` — 全局连续无改善轮数（判断探索模式）
  - [ ] `next_iteration_id() -> int`

**验收**：
- 空文件初始化 → append 一条 → load → latest 返回刚追加的记录
- 连续追加同一 unit 的两条 no-improvement 记录 → `count_consecutive_no_improvement` 返回 2

---

### Task 1.2｜变更执行器（config_patcher.py）

**新建文件**：`src/outer_loop/optimization/config_patcher.py`

**步骤**：
- [ ] 定义 `ChangeProposal` dataclass（与 `outer_loop_design.md §5.1` 对齐）：
  ```python
  @dataclass
  class ChangeProposal:
      change_unit: str
      change_type: Literal["field_patch", "file_overwrite"]
      target_file: str
      target_path: str
      new_value: Any
      rationale: str
  ```
- [ ] 实现 `ConfigPatcher` 类：
  - [ ] 内置白名单（`outer_loop_design.md §5.3` 全部条目）
  - [ ] 内置 bundle 文件保护字段集合：`{"model", "model_id"}`，写入时拦截
  - [ ] `validate(proposal: ChangeProposal) -> tuple[bool, str]` — 仅校验，不执行
  - [ ] `apply(proposal: ChangeProposal, iter_id: str) -> tuple[bool, str]`：
    1. 白名单校验（失败 → 返回错误，不执行）
    2. 保护字段校验（target_path 不得包含保护字段名）
    3. 快照当前 `configs/` → `experiments/snapshots/iter_{iter_id}/configs/`
    4. 执行 patch（YAML 嵌套字段定位 + 写入）
    5. 格式验证（`yaml.safe_load` 验证合法性）
    6. 失败回退（从快照恢复，记录失败原因）
  - [ ] `rollback(iter_id: str) -> bool` — 从指定快照恢复 configs/
  - [ ] `_prune_old_snapshots(keep: int = 20)` — 保留最近 N 轮快照
- [ ] 快照路径：`experiments/snapshots/iter_{iter_id}/configs/`（仅快照 `configs/` 目录）

**验收**：
- 白名单外文件写入 → 被拒绝，configs 未变动
- `model` 字段写入 → 被拦截
- 合法 patch 执行 → YAML 更新，快照存在
- 格式非法的 new_value → 回退到快照，configs 恢复原值

---

### Task 1.3｜搜索空间约束（search_policy.py）

**新建文件**：`src/outer_loop/optimization/search_policy.py`

**步骤**：
- [ ] 实现 `SearchPolicy` 类（封装 `outer_loop_design.md §4.2` 全部软策略规则）：
  - [ ] `should_force_transfer(log: ExperimentLog, unit: str) -> bool` — 同一 unit 连续 2 次无改善
  - [ ] `should_rollback(prev_qwk: float, new_qwk: float) -> bool` — 下降超过 0.03
  - [ ] `is_exploration_mode(log: ExperimentLog) -> bool` — 全局连续 5 轮无改善
  - [ ] `is_forbidden_unit(log: ExperimentLog, unit: str) -> bool`
  - [ ] `get_priority_layer(unit: str) -> int` — P1/P2/P3/P4 层级判断
  - [ ] `should_escalate_layer(log: ExperimentLog, probes: dict) -> bool` — 上游问题未收敛时拦截下游探索
- [ ] 定义优先级层映射（配置化，不硬编码具体 unit 名）：
  ```python
  PRIORITY_LAYERS = {
      "scoring": 1,
      "coverage": 2, "extraction": 2,
      "adjudication": 3,
      "feedback": 4,
  }
  ```

**验收**：单元测试覆盖每条规则的触发 / 不触发路径。

---

## Phase 2：外环 Agent 主循环

> 2.1 可先于 1.3 开始（只需接口设计完成）。2.2 依赖 Phase 0 和 Phase 1 全部。

### Task 2.1｜Agent Prompts

**新建文件**：
- `src/outer_loop/prompts/outer_loop_system.md`
- `src/outer_loop/prompts/outer_loop_user_template.md`

**步骤**：
- [ ] System prompt 包含：
  - 角色定义（MAS 外环优化 Agent）
  - 当前优化目标（per-dimension QWK ≥ 0.8，composite QWK ≥ 0.8）
  - 可用动作：提交 ChangeProposal（YAML 格式）、选择本轮探针
  - 搜索空间约束（§4.2 全部规则的自然语言版本）
  - 单变更原则
  - 输出格式规范（结构化 YAML block）
- [ ] User prompt 模板（Jinja2-style 变量占位）包含：
  - `{{ experiment_log_summary }}` — 最近 N 轮摘要
  - `{{ current_config_snapshot }}` — 当前关键配置字段的摘要
  - `{{ available_probes }}` — 探针列表及其说明
  - `{{ last_verdict }}` — 上一轮 verdict
  - `{{ next_hypothesis }}` — 上一轮 next_hypothesis
  - `{{ probe_results_this_round }}` — Phase 3 执行后才填入

**验收**：Prompt 格式完整，变量占位符清晰，无多余硬编码。

---

### Task 2.2｜Agent 主循环（agent.py）

**新建文件**：`src/outer_loop/agent.py`

**步骤**：
- [ ] 定义 `OuterLoopAgent` 类，依赖注入：`ExperimentLog`, `ConfigPatcher`, `SearchPolicy`, provider
- [ ] 实现 `run_one_iteration(iter_id: str) -> IterationRecord`，按 5 个 Phase 执行：
  - **Phase 1 读取**：加载 experiment_log 摘要 + 当前配置快照
  - **Phase 2 决策**：填充 user_prompt → 调用 LLM → 解析 `ChangeProposal`（YAML block 提取）
  - **Phase 3 执行**：
    - `SearchPolicy` 校验（禁区 / 强制转移 / 层级约束）
    - `ConfigPatcher.apply(proposal, iter_id)`
    - `batch_eval(training_sample_ids, ...)`
    - `run_probes(selected_probes, artifacts_dir)`
  - **Phase 4 复盘**：将探针结果填入 user_prompt → 调用 LLM → 解析 verdict + next_hypothesis
  - **Phase 5 归档**：`ExperimentLog.append(record)` + `_prune_old_snapshots()`
- [ ] 实现 `run_loop(max_iterations: int, stop_on_target: bool = True)`：
  - 逐轮调用 `run_one_iteration()`
  - 终止条件：QWK 达标 / 达到 max_iterations / 外部中断信号
- [ ] 实现 `run_cold_start()` — 冷启动分支（见 Task 2.3）

**验收**：使用 mock provider 完成一轮完整迭代，experiment_log 中增加一条记录，configs 有对应快照。

---

### Task 2.3｜冷启动流程

**步骤**：
- [ ] 在 `OuterLoopAgent.run_loop()` 入口检测：`experiment_log.count() == 0` → 进入冷启动
- [ ] 冷启动流程：
  1. 调用 `batch_eval(all_training_ids, iter_id="cold_start")`
  2. 运行全部探针，产出初始诊断报告（写入 `experiments/probes/cold_start_diagnostics.json`）
  3. 读取 `experiments/cold_start_notes.md`（不存在时跳过）
  4. 产出第一条 ChangeProposal，写入 `experiments/iter_001_proposal.yaml`（供人工审查）
- [ ] 新建 `experiments/cold_start_notes.md`：包含人工初始观察的模板注释

**验收**：冷启动后 `experiments/probes/cold_start_diagnostics.json` 存在，内含全部探针结果。

---

### Task 2.4｜外环 CLI 入口

**新建文件**：`scripts/outer_loop.py`

**步骤**：
- [ ] 实现 typer CLI，子命令：
  - `run` — 启动外环循环
    - `--max-iterations INT`
    - `--bundle PATH`（默认 `asap_set8_baseline.bundle.yaml`）
    - `--training-set PATH`（默认 `data/training_set_8.tsv`）
    - `--mock-agent` — 使用 mock LLM，用于冒烟测试
  - `status` — 展示实验日志摘要 + 最新 QWK
  - `rollback --iter-id STR` — 手动回退到指定迭代的配置快照
  - `probe --name STR` — 手动运行指定探针（输出 JSON）

**验收**：`python scripts/outer_loop.py status` 可运行，`rollback` 可恢复任意快照。

---

## Phase 3：可选补充（提升可靠性）

### Task 3.1｜Provider system prompt 通道独立化（低优先级）

**目标**：将 agent 调用的 system prompt 从 prompt template 中拆出，成为独立可调优的配置层

**步骤**：
- [ ] 调研 `src/agents/scorer.py`、`extractor.py` 的 prompt 构造方式
- [ ] 评估改动范围；如影响超过 3 个 agent 则单独立项，不在本轮做

---

### Task 3.2｜configs/ 目录补充

**步骤**：
- [ ] 检查并创建 `configs/prompts/tasks/`（task-specific scoring_context 落点）
- [ ] 检查并创建 `configs/rubrics/source/`（量规原文永久只读落点）
- [ ] 在 bundle YAML 中添加对应路径引用（如需）

---

## Phase 4：测试覆盖

| Task | 测试文件 | 关键用例 |
|------|---------|---------|
| 0.1 | `tests/unit/pipeline/test_retry_config.py` | max_retries 从 bundle 读取；缺失时 fallback |
| 0.2 | `tests/unit/experiments/test_batch_runner.py` | mock provider 批量执行；iter_id 隔离 |
| 0.3 | `tests/unit/outer_loop/test_probes.py` | 每个探针的输出格式；缺失文件的优雅处理 |
| 1.1 | `tests/unit/outer_loop/test_experiment_log.py` | append/load 一致性；连续失败计数；禁区标记 |
| 1.2 | `tests/unit/outer_loop/test_config_patcher.py` | 白名单拦截；保护字段拦截；快照与回退；YAML 合法性 |
| 1.3 | `tests/unit/outer_loop/test_search_policy.py` | 每条规则的触发/不触发路径 |
| 2.2 | `tests/integration/test_outer_loop_iteration.py` | mock provider 完整一轮迭代；log 追加；快照存在 |

---

## 依赖关系总结

```
Task 0.0 (目录骨架)
    └─ 无依赖，最先做

Task 0.1 / 0.2 / 0.3 (内环改造)
    └─ 三者可并行

Task 1.1 (experiment_log)     ← 无外部依赖
Task 1.2 (config_patcher)     ← 无外部依赖
    └─ 1.1 + 1.2 可并行

Task 1.3 (search_policy)
    └─ 依赖 1.1 + 1.2 的接口定义

Task 2.1 (prompts)
    └─ 只需 0.3 / 1.1 / 1.2 的接口设计，可提前开始

Task 2.2 (agent 主循环)
    └─ 依赖 Phase 0 全部 + Phase 1 全部

Task 2.3 (冷启动)
    └─ 依赖 2.2

Task 2.4 (CLI)
    └─ 依赖 2.2
```

**建议单人串行顺序**：0.0 → 0.1 → 0.2 → 0.3 → 1.1 → 1.2 → 1.3 → 2.1 → 2.2 → 2.3 → 2.4

---

## 关键约束提醒

> 来自 `CLAUDE.md` 和 `outer_loop_design.md`，执行时必须遵守：

1. **外环 Agent 不得在运行时修改评分结果**
2. **外环 Agent 不得绕过 scorer / adjudicator / router 等内环节点**
3. **策略调整必须通过新版 bundle 走完整内环验证，不允许热更新生效**
4. **`configs/rubrics/` 下所有文件不在 ConfigPatcher 白名单内**
5. **bundle 中 model / model_id 字段不可由外环修改**
6. **没有 holdout 集上的稳定提升，不允许策略晋级**

---

*最后更新：2026-04-01*
