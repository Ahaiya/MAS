# MAS — 基于量规的工程能力自动评价系统

> 本文档面向首次接触本仓库的开发者或 Agent，提供架构、数据流、关键文件路径的完整概览。

---

## 一、系统定位

**MAS（Multi-Agent Scoring）** 是一套面向高校工程教育场景的 **AI 评价系统**。
核心目标：给定学生解决复杂工程问题时产生的非结构性文档（Markdown 文件），系统按预先设计的量规（Rubric，由教师上传）自动打分并生成反馈，支持教师人工审核与修正，修正意见通过外环机制自动更新评分配置，持续提升与教师判断的一致性。

---

## 二、整体架构

系统分为**内环流水线**和**人工反馈外环**两层；仓库中不再保留自动实验优化闭环：

```
┌─────────────────────────────────────────────────────────┐
│                        外环（Outer Loop）                 │
│  教师审核结果 ──► 修正队列 ──► CorrectionAgent ──► 更新配置  │
└──────────────────────────┬──────────────────────────────┘
                           │ 每次评估前检查并应用修正
┌──────────────────────────▼──────────────────────────────┐
│                      内环流水线（Inner Loop）              │
│                                                          │
│  输入文本                                                  │
│    │                                                      │
│    ▼                                                      │
│  Chunker ──► Extractor ──► Scorer × N ──► Reconciliation │
│                                              │            │
│                                          FeedbackAgent    │
│                                              │            │
│                                         输出 JSON 产物     │
└─────────────────────────────────────────────────────────┘
```

---

## 三、内环流水线详解

流水线由 `src/pipeline/runner.py` 的 `PipelineRunner` 编排，阶段状态机定义在 `src/orchestrator/`。

| 阶段 | 模块 | 说明 |
|------|------|------|
| **Chunking** | `src/agents/chunker.py` | 将长对话按轮次/主题分块；文本 < 4000 token 时跳过 |
| **Evidence Extraction** | `src/agents/extractor.py` | 从分块中抽取与各维度相关的证据 span（带 span_id 和 quote） |
| **Observation** | `src/agents/observer.py` | 将 span 聚合为维度级观测结构 |
| **Scoring × 2** | `src/agents/scorer.py` | 两个独立 Rater（rater_1 / rater_2）各自打分，产出 ScoreHypothesis |
| **Consistency Check** | `src/agents/reconciliation.py` | 检测评分冲突；分差 > 1 或 ≥2 维度同向偏移时触发 rater_3 裁决 |
| **Feedback** | `src/agents/feedback.py` | 基于最终决策生成每维度的中文反馈文本 |
| **Aggregation** | `src/policies/aggregation.py` | 等权均值聚合各观测点得分，产出综合指标分 |

### 状态机

```
INIT → CHUNKING → COVERAGE_PLANNING → EXTRACTING → OBSERVING
     → SCORING → CONSISTENCY_CHECK → [ADJUDICATION →] FEEDBACK → DONE
```

回退路径：`RE_EXTRACT`、`RE_SCORE`（最多重试 2 次，超限进入 `FAILED`）

---

## 四、人工反馈外环

教师在前端修改评分结果后，触发外环：

1. **修正队列**：前端 POST `/api/corrections` → 写入 `experiments/pending_corrections.json`
2. **触发时机**：每次运行 `scripts/eval.py` 前，`check_and_apply_corrections()` 自动检查队列
3. **CorrectionAgent**（`src/outer_loop/correction_agent.py`）：读取当前 bundle 指向的 `task_context.yaml` + 修正事件 → 调用 LLM → 生成更新后的 YAML → 通过 `ConfigPatcher` 写入并快照
4. **修正信号映射**：

| 教师操作 | 写入字段 | 位置 |
|---------|---------|------|
| 改最终分数 | `calibration_notes` | `task_context.yaml → scoring_context[i]` |
| 改反馈文本 | `feedback_hints` | `task_context.yaml → scoring_context[i]` |
| 新增证据引用 | `extraction_hints` | `task_context.yaml → scoring_context[i]` |

---

## 五、配置体系

所有业务参数通过 YAML 配置注入，代码中不硬编码任何业务值。

### 目录结构

```
configs/
├── bundles/
│   └── engineering_eval_baseline.bundle.yaml   # 入口 bundle，定义所有引用
├── model_config.yaml                            # LLM 分配（各阶段/各 rater）
├── tasks/
│   └── {task_name}/
│       ├── task_context.yaml                   # 任务说明 + 各维度 calibration/hints（外环唯一修改目标）
│       └── dimension/
│           └── {dim_id}_rubric.yaml            # 二级指标量规（1-5 级锚点）
├── policies/
│   ├── adjudication/engineering_eval_adjudication.yaml  # 裁决触发规则（全局）
│   ├── aggregation/engineering_eval_aggregation.yaml    # 聚合策略（auto_equal，全局）
│   └── chunking/engineering_eval_chunking.yaml          # 分块策略（全局）
├── prompts/
│   ├── chunking.yaml          # 分块提示词模板（Jinja2，支持 chunking_hints）
│   ├── evidence_extraction.yaml  # 证据提取提示词（支持 extraction_hints）
│   ├── scoring.yaml           # 评分提示词
│   └── explanation.yaml       # 反馈生成提示词（支持 feedback_hints）
└── rubrics/
    └── source/rubric.md       # 量规原始来源（参考文档）
```

### Bundle 解析

Bundle 是配置入口，`src/config/resolver.py` 的 `ConfigResolver` 负责解析路径模板：

- `{active_task_id}` → bundle 中的 `active_task_id` 字段（如 `maker_hackathon`）
- `{active_dim_id}` → 运行时 `--dim` 参数注入（如 `a4`）

### task_context.yaml 结构

```yaml
schema_version: "2.0"
task_name: "maker_hackathon"
material_context:
  type: "conversation"
  evidence_focus: "..."          # 约束评价对象（不可被外环修改）
chunking_hints: ""               # 注入分块阶段
human_instructions: ""
scoring_context:
  - code: "A4-1"
    extraction_hints: ""         # 注入证据提取阶段
    calibration_notes: "..."     # 注入评分阶段（外环重点修改）
    feedback_hints: ""           # 注入反馈生成阶段
```

---

## 六、运行方式

### 环境准备

```bash
cp .env.example .env   # 填写 LLM_API_KEY 等
pip install -e ".[real-provider]"
```

### 单篇评估

```bash
python -m scripts eval data/training/maker_hackathon/sample.md --dim a4
# 等价写法
python -m scripts eval --input data/training/maker_hackathon/sample.md \
    --bundle configs/bundles/engineering_eval_baseline.bundle.yaml \
    --dim a4
```

产物写入 `artifacts/{task}/{sample_name}/{dim}/`：
- `feedback.json`：各维度分数 + 反馈文本 + 证据引用
- `hypotheses.json`：各 rater 原始假设
- `adjudication_records.json`：裁决记录
- `conflicts.json`：分歧记录

### 启动前端审核台

```bash
python scripts/server.py          # 默认 8000 端口，包含静态文件服务和 /api/corrections
# 浏览器访问 http://127.0.0.1:8000/frontend/index.html
```

不要用 `python3 -m http.server` 启动审核台：它只能提供静态文件，不支持 `POST /api/corrections`，点击 `Release` 时会返回 501。

前端（纯 HTML+JS，无构建步骤）展示：
- 右侧原文展示区：渲染被评价的 Markdown 文件内容
- 评分审核区：各观测点评分 + 反馈（可直接编辑）、证据引用链
- Release 按钮：提交修改意见 → `POST /api/corrections`

---

## 七、关键文件速查

| 文件 | 职责 |
|------|------|
| `scripts/eval.py` | 主评估入口（含修正前检查） |
| `scripts/server.py` | 前端开发服务器 + `/api/corrections` 接口 |
| `src/evaluation/runner.py` | 单篇材料评估执行器（被 `scripts/eval.py` 调用） |
| `src/pipeline/runner.py` | 内环流水线状态机编排 |
| `src/config/resolver.py` | Bundle 解析 + Artifact 加载 + Schema 校验 |
| `src/outer_loop/correction_agent.py` | 教师修正 → 配置更新的 LLM Agent |
| `src/outer_loop/correction_models.py` | 修正事件数据模型 + JSON 序列化 |
| `src/outer_loop/config_patcher.py` | ConfigPatcher（白名单 + 快照 + patch） |
| `src/policies/aggregation.py` | 聚合逻辑（支持 `auto_equal` 等权） |
| `src/agents/feedback.py` | 反馈生成，支持 per-dimension `feedback_hints` |
| `frontend/src/app.js` | 前端主逻辑（含 Release 提交修正） |
| `frontend/src/data/loadReviewData.js` | 从 artifacts/ 加载评估产物 |

---

## 八、新增任务步骤

添加一个新评价任务只需：

1. 在 `configs/tasks/{task_name}/dimension/` 下按 `a4_rubric.yaml` 格式创建量规文件
2. 在 `configs/tasks/{task_name}/task_context.yaml` 中填写任务说明
3. 修改 bundle 中的 `active_task_id` 为新任务名（或新建一个 bundle 文件）
4. 在 `data/training/{task_name}/` 下放入待评估的 `.md` 文件
5. 运行 `python -m scripts eval {file.md} --dim {dim_id}`

无需修改任何代码或策略文件（聚合策略已改为 `auto_equal`，自动适配任意维度数量）。

---

## 九、.env 关键变量

| 变量 | 说明 |
|------|------|
| `LLM_API_KEY` | 主模型 API Key（默认 deepseek-chat） |
| `LLM_MODEL` | 主模型名 |
| `LLM_API_BASE` | 主模型 API Base URL |
| `RATER_2_API_KEY` | 第二个 rater 的 API Key（可与主模型不同） |
| `OUTER_LOOP_API_KEY` | 外环 Agent 使用的 API Key |
| `OUTER_LOOP_MODEL` | 外环 Agent 模型名 |
| `OUTER_LOOP_API_BASE` | 外环 Agent API Base URL |

未填写的 `RATER_*` / `OUTER_LOOP_*` 变量自动回落至 `LLM_API_KEY`。
