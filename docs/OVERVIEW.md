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

## 三、评价流水线详解

流水线由 `src/engine.py` 的 `Engine` 编排，是一条线性函数链，没有状态机与回退重入。

| 阶段 | 模块 | 说明 |
|------|------|------|
| **Segmentation** | `src/segment.py` | 零 LLM 的确定性切分：散文按句、代码块整块、表格按行、标题/图片各成单元；全局连续编号，多文件共享编号空间 |
| **Select ×2** | `src/agents/rater.py` | 每个 Rater 独立看「单元号 + 每段前若干字节」选出相关单元（先全局扫描） |
| **Extract ×2** | `src/agents/rater.py` | 只把选中单元全文喂入取证，返回 `unit_ids`（再细节精读） |
| **Score ×2** | `src/agents/rater.py` | 证据 + 锚点 → `DimensionScore`；与取证是两次独立调用，证据先于分数生成 |
| **Reconcile** | `src/agents/reconcile.py` | 双链比较；分差 > 1 或 ≥2 维度同向漂移时触发 Rater3 |
| **Adjudicate** | `src/agents/adjudicator.py` | Rater3 看双链完整证据 + 量规 + 原文，但**看不到双方分数**（防锚定） |
| **Feedback** | `src/agents/report.py` | 基于最终决策生成每个二级指标的中文反馈；聚合走 `src/policies/aggregation.py` 等权均值 |

### 链路形状

```
segment → rate(r1) → rate(r2) → reconcile →[adjudicate]→ feedback
```

两个 Rater 各自独立完成「选段 → 取证 → 评分」完整链路，互不共享证据——独立性落在
"看哪里"这个最关键的分歧点上。同一 sample 下各二级指标并发评价（`ThreadPoolExecutor`，
上限 `model_config.yaml` 的 `runtime.max_workers`，默认 8）。

单个二级指标失败只标记该维度失败并记入 `run_trace.json` 的 `failed_dims`，其余维度
照常产出；一个一级指标整体失败也不拖垮同 sample 的其余一级指标。

---

## 四、人工反馈外环（三期，当前未接线）

前端 POST `/api/corrections` 仍会把教师修正写进 `experiments/pending_corrections.json`
（见 `scripts/server.py`），但消费这个队列的 `CorrectionAgent` / `ConfigPatcher` 已随 v1
外环一并删除。人在回路是三期的事，届时重新接线。

当前唯一相关的钩子是 `feedback.json` 里每个二级指标的 `source` 字段
（`consensus` / `adjudicated`），供教师识别哪些分经过仲裁、需要重点复核。

---------|---------|------|
| 改最终分数 | `calibration_notes` | `task_context.yaml → scoring_context[i]` |
| 改反馈文本 | `feedback_hints` | `task_context.yaml → scoring_context[i]` |
| 新增证据引用 | `extraction_hints` | `task_context.yaml → scoring_context[i]` |

---

## 五、配置体系

所有业务参数通过 YAML 配置注入，代码中不硬编码任何业务值。

### 目录结构

```
configs/
├── bundle.yaml                                  # 入口 bundle：active_task_id + policies + prompts
├── model_config.yaml                            # providers（各角色模型端点）+ runtime（并发/超时/重试）
├── tasks/
│   └── {task_name}/
│       ├── task_context.yaml                   # 任务说明 + 各维度 calibration/hints
│       └── dimension/
│           └── {dim_id}_rubric.yaml            # 一级指标量规，内含多个二级指标（1-5 级锚点）
├── policies/
│   └── adjudication/engineering_eval_adjudication.yaml  # 仲裁触发规则（全局）
├── prompts/
│   ├── select.yaml             # 选段提示词（单元号 + 前若干字节预览）
│   ├── extraction.yaml         # 取证提示词（返回 unit_ids）
│   ├── rater_scoring.yaml      # 评分提示词
│   ├── adjudication.yaml       # Rater3 仲裁提示词
│   └── feedback.yaml           # 反馈生成提示词
└── rubrics/
    └── source/rubric.md       # 量规原始来源（参考文档）
```

聚合不再读 policy——一级指标分固定是各二级指标的 `auto_equal` 等权平均；分块 policy
随 LLM 分块一并删除，切分改为零 LLM 的确定性过程。

### Bundle 结构

`configs/bundle.yaml` 直接列出引用，没有路径模板与冻结哈希：

```yaml
bundle_id: "default"
active_task_id: "maker_hackathon"     # 切任务改这里
policies:
  adjudication: "configs/policies/adjudication/engineering_eval_adjudication.yaml"
prompts:
  select: "configs/prompts/select.yaml"
  extraction: "configs/prompts/extraction.yaml"
  scoring: "configs/prompts/rater_scoring.yaml"
  adjudication: "configs/prompts/adjudication.yaml"
  feedback: "configs/prompts/feedback.yaml"
```

用 `python scripts/cli.py config validate` 校验引用闭包是否完整。

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
cp .env.example .env   # 填写 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY
pip install -e ".[real-provider]"
```

### 单篇评估

```bash
# 评单个一级指标
python scripts/cli.py eval data/training/maker_hackathon/sample.md --dim a4
# 不传 --dim：评当前任务下全部一级指标
python scripts/cli.py eval data/training/maker_hackathon/sample.md
# 校验配置引用闭包（不需要密钥）
python scripts/cli.py config validate
```

参数只有位置 `INPUT_FILE` + `--bundle`（默认 `configs/bundle.yaml`）+ `--dim` + `--output-dir`。
模型/参数固定从 `configs/model_config.yaml` 读，密钥值只从 `.env` 读。

产物写入 `artifacts/{task}/{sample}/`：
- `package.json`（sample 层，各 dim 共享）：切分后带编号的单元，用于把 `unit_ids` 解读回原文
- `{dim}/feedback.json`：一级指标分 + 雷达数据 + 各二级指标 final_score/source/证据 `unit_ids`/文字反馈
- `{dim}/rater_chains.json`：双链完整证据 + 仲裁记录（审计用）
- `{dim}/run_trace.json`：成本与性能（token / 耗时 / 被仲裁的维度 / 失败的维度）

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
| `scripts/cli.py` | 唯一命令行入口（`eval` + `config validate`） |
| `scripts/server.py` | 前端开发服务器 + `/api/corrections` 接口 |
| `src/engine.py` | 门面与线性编排：`Engine.from_bundle(path).evaluate(package, dim)` |
| `src/segment.py` | 零 LLM 确定性单元切分 + 上下文预算裁剪 |
| `src/agents/rater.py` | 单 Rater 完整链 select → extract → score |
| `src/agents/reconcile.py` | 双链比较，分歧时调 Rater3 |
| `src/agents/adjudicator.py` | Rater3 仲裁（不看双方分数） |
| `src/agents/report.py` | feedback.json / rater_chains.json 内容组装 |
| `src/artifacts.py` | 三层产物落盘 `{task}/{sample}/{dim}/` |
| `src/config/compiler.py` | 按 task_id + dim_id 加载量规 → `RubricSnapshot` |
| `src/policies/adjudication.py` | 仲裁触发判断（纯计算） |
| `src/policies/aggregation.py` | 聚合逻辑（`auto_equal` 等权） |
| `src/providers/fake.py` | `FakeProvider` —— 测试主接缝 |
| `frontend/src/app.js` | 前端主逻辑（含 Release 提交修正） |
| `frontend/src/data/loadReviewData.js` | 从 artifacts/ 加载评估产物 |

---

## 八、新增任务步骤

添加一个新评价任务只需：

1. 在 `configs/tasks/{task_name}/dimension/` 下按 `a4_rubric.yaml` 格式创建量规文件
2. 在 `configs/tasks/{task_name}/task_context.yaml` 中填写任务说明
3. 修改 `configs/bundle.yaml` 中的 `active_task_id` 为新任务名
4. 在 `data/training/{task_name}/` 下放入待评估的 `.md` 文件
5. 运行 `python scripts/cli.py eval {file.md} --dim {dim_id}`

无需修改任何代码或策略文件（聚合策略已改为 `auto_equal`，自动适配任意维度数量）。

---

## 九、.env 与配置职责边界

职责边界是硬的：**`.env` 只装凭证值，其余一切都在 `configs/model_config.yaml`**。
两侧都没有兜底——缺什么就报什么，绝不互相顶替。

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek 账号密钥，默认由 rater_1 / rater_3 / feedback 共用 |
| `DASHSCOPE_API_KEY` | 阿里云百炼账号密钥，默认由 rater_2 使用 |

变量名按**厂商**取而非按角色：凭证属于厂商账号，多个角色共用同一账号时不必把同一个
值抄好几遍。要接新厂商，在 `model_config.yaml` 给对应 provider 写上新的 `api_key_env`
名字，再到 `.env` 加一行即可。

模板见仓库根目录的 `.env.example`。**没有任何回落**：某个 `api_key_env` 指向的变量
没有值，引擎启动即报错——回落只会把 A 厂商的 key 发给 B 厂商，换来一句难以归因的 401。

模型名、接口地址、温度、max_tokens、超时、重试、并发一律不在 `.env` 里，全部在
`model_config.yaml`。
