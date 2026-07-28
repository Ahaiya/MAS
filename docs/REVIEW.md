# MAS 内核技术文档

> 面向后续接入 Web 网站的开发者，全面描述 MAS（Multi-Agent System）评价引擎的架构、数据流与集成接口。

> [!WARNING]
> **本文描述的是引擎 v1，已随 v2 重构大面积过期，暂勿据此接入。**
>
> v2 把流水线换成了「确定性切分 + 独立双链路 + 编号锚点证据」，本文提到的
> `chunker.py` / `extractor.py` / `observer.py` / `scorer.py` / `orchestrator/` /
> `pipeline/` / `outer_loop/` / `ConfigCompiler` / `configs/bundles/` 均已删除；
> 产物里的 `hypotheses.json` / `conflicts.json` 也已被 `rater_chains.json` 取代，
> 证据引用从复述原文改为 `unit_ids` 编号。
>
> 当前准确的入口与架构见 [`OVERVIEW.md`](./OVERVIEW.md) 与 [`../scripts/README.md`](../scripts/README.md)；
> 契约细节以 `src/contracts/` 为准。本文的重写是独立待办，需与前端负责人对齐后进行。

---

## 目录

1. [系统概述](#1-系统概述)
2. [目录结构](#2-目录结构)
3. [核心概念](#3-核心概念)
4. [配置层（Config Layer）](#4-配置层config-layer)
5. [契约层（Contracts Layer）](#5-契约层contracts-layer)
6. [Provider 层](#6-provider-层)
7. [流水线状态机](#7-流水线状态机)
8. [各阶段 Agent 详解](#8-各阶段-agent-详解)
9. [输出结构](#9-输出结构)
10. [现有 CLI 与 Server](#10-现有-cli-与-server)
11. [Web 接入指南](#11-web-接入指南)
12. [环境变量与密钥](#12-环境变量与密钥)
13. [依赖清单](#13-依赖清单)

---

## 1. 系统概述

MAS 是一个基于量规（Rubric）的多智能体文本自动评价引擎，设计目标是对学生提交的长对话记录或工程材料进行多维度评分，并生成带引用的自然语言反馈。

**典型场景**：
- 输入：一份学生与 AI 协同完成工程设计的 Markdown 对话记录
- 输出：按量规各维度（如 A4-1、A4-2、A4-3）给出分数 + 反馈文字 + 证据引用

**架构关键词**：
- 零硬编码：所有维度名、量表范围、权重、Prompt 均从 YAML 配置读取
- 状态机驱动：评价过程是一个有明确状态图的确定性流水线
- 多评委模式：2–3 个 LLM Rater 独立打分，发现分歧后自动裁决
- OpenAI-compatible：任何兼容 `/v1/chat/completions` 的服务均可接入

---

## 2. 目录结构

```
MAS/
├── src/                          # 核心内核（可作为 Python 包导入）
│   ├── agents/                   # 各 LLM 调用 Agent（无状态函数）
│   │   ├── chunker.py            # 文档分块
│   │   ├── extractor.py          # 证据抽取
│   │   ├── observer.py           # 证据整理 → DimensionObservation
│   │   ├── scorer.py             # 单维度评分 → ScoreHypothesis
│   │   ├── reconciliation.py     # 一致性检查 + 裁决
│   │   ├── feedback.py           # 反馈组装
│   │   ├── config_resolver.py    # bundle → ResolvedArtifactBundle
│   │   └── prompt_builders.py    # 各阶段 Prompt 构建
│   ├── config/                   # 配置编译层
│   │   ├── compiler.py           # ConfigCompiler（编译入口）
│   │   ├── resolver.py           # ConfigResolver（文件加载+校验）
│   │   ├── schema.py             # Pydantic 文件格式校验 Schema
│   │   └── freeze.py             # 内容哈希计算
│   ├── contracts/                # 数据契约（不可变 dataclass）
│   │   ├── artifact_bundle.py    # Bundle / Snapshot 结构
│   │   ├── request_models.py     # 输入请求 + 文档 + 覆盖计划
│   │   ├── evidence.py           # 证据片段 + 维度观察
│   │   ├── scoring.py            # 假设分 + 裁决 + 最终决定
│   │   ├── score_representation.py # 分数表示
│   │   └── trace.py              # 运行轨迹（审计追踪）
│   ├── orchestrator/             # 状态机 + 路由
│   │   ├── states.py             # PipelineState 枚举 + 转换矩阵
│   │   ├── graph.py              # StateGraph（运行时状态机）
│   │   ├── router.py             # 路由决策函数
│   │   ├── checkpoints.py        # CheckpointManager（重试保护）
│   │   └── trace_store.py        # TraceStore（节点记录）
│   ├── pipeline/                 # 流水线编排
│   │   ├── runner.py             # PipelineRunner（唯一编排入口）
│   │   ├── export.py             # 指标分 payload 导出
│   │   └── validators.py         # 终止态校验
│   ├── policies/                 # 策略计算（无 LLM 调用）
│   │   ├── aggregation.py        # 聚合公式（compute_composite）
│   │   ├── adjudication.py       # 裁决策略
│   │   ├── explanation.py        # 解释渲染
│   │   └── rubric_core.py        # 量规工具函数
│   ├── providers/                # LLM Provider 抽象层
│   │   ├── base.py               # BaseProvider 抽象基类
│   │   ├── openai_compatible.py  # OpenAI-兼容适配器
│   │   ├── factory.py            # Provider 工厂（从 Config 构建）
│   │   ├── guards.py             # GuardedProvider（重试包装）
│   │   ├── logging_provider.py   # LoggingProvider（统计包装）
│   │   ├── prompt_loader.py      # PromptTemplate 加载器
│   │   ├── registry.py           # Provider 注册表
│   │   └── structured_output.py  # JSON 输出归一化
│   ├── evaluation/
│   │   └── runner.py             # run_single_eval()（单文档评估入口）
│   ├── outer_loop/               # 外环优化（配置自动调参，与 Web 接入无直接关系）
│   ├── debug/                    # 调试 bundle 写出
│   └── utils/                    # 杂项工具
├── configs/                      # YAML 配置（量规 + 策略 + Prompt）
│   ├── bundles/                  # Bundle 入口文件
│   ├── tasks/                    # 任务量规（按 task_id/dimension 组织）
│   ├── policies/                 # 裁决 / 聚合 / 分块策略
│   ├── prompts/                  # Prompt 模板 YAML
│   └── model_config.yaml         # LLM 分配配置
├── scripts/
│   ├── eval.py                   # CLI 评估入口
│   ├── server.py                 # 开发用静态文件 + corrections API 服务器
│   └── mas.py                    # 统一入口调度
├── frontend/                     # 前端审阅工作台（纯静态 HTML/JS）
├── artifacts/                    # 评估结果输出目录
├── data/training/                # 训练样本 + 人工分数 TSV
└── experiments/                  # 外环实验日志 + 待应用批改
```

---

## 3. 核心概念

### 3.1 Bundle

Bundle 是整个评价任务的"配置包"入口，是一个 YAML 文件，声明了：
- 使用哪份量规（Rubric）
- 使用哪些策略（裁决 / 聚合 / 分块）
- 使用哪些 Prompt 模板
- 激活哪个任务（`active_task_id`）和哪个维度（`active_dim_id`）

当前内核支持**简化 Bundle 格式**（无 `artifact_bundle` 包装键）：

```yaml
# configs/bundles/engineering_eval_baseline.bundle.yaml
schema_version: "2.0"
bundle_id: "engineering_eval_baseline"
active_task_id: "physics_experiment"

rubric:
  dimension: "configs/tasks/{active_task_id}/dimension/{active_dim_id}_rubric.yaml"

context:
  task: "configs/tasks/{active_task_id}/task_context.yaml"

prompts:
  chunking: "configs/prompts/chunking.yaml"
  evidence_extraction: "configs/prompts/evidence_extraction.yaml"
  scoring: "configs/prompts/scoring.yaml"
  explanation: "configs/prompts/explanation.yaml"

policies:
  chunking: "configs/policies/chunking/engineering_eval_chunking.yaml"
  adjudication: "configs/policies/adjudication/engineering_eval_adjudication.yaml"
  aggregation: "configs/policies/aggregation/engineering_eval_aggregation.yaml"
```

路径模板中的 `{active_dim_id}` 在运行时被替换为实际维度 ID（如 `a4`、`b1`）。

### 3.2 ResolvedArtifactBundle

`ConfigCompiler.compile(bundle_path)` 的返回值。它是一个**冻结的不可变对象**，包含：

| 字段 | 类型 | 说明 |
|------|------|------|
| `artifact_bundle` | `ArtifactBundle` | 原始 Bundle 元数据 + 所有 ArtifactRef（含文件哈希） |
| `rubric_snapshot` | `RubricSnapshot` | 量规核心数据（维度列表、量表、快速查找表） |
| `policy_snapshot` | `PolicySnapshot` | 裁决 / 聚合 / 解释策略 + 分块策略 + 评分上下文 |
| `prompt_templates` | `Dict[str, str]` | 各阶段 Prompt 模板字符串 |
| `provider_config` | `ProviderConfig \| None` | Bundle 内声明的 LLM 分配（可空） |
| `total_hash` | `str` | 所有工件的组合哈希，用于版本可验证性 |

### 3.3 EvaluationRequest

评价流水线的统一输入边界：

```python
@dataclass(frozen=True)
class EvaluationRequest:
    raw_text: str        # 待评估文本（学生材料全文）
    bundle_ref: str      # bundle_id@bundle_version，标识配置版本
    request_id: Optional[str] = None   # 调用方提供的 ID（None 时由流水线生成）
    metadata: Dict[str, Any] = {}      # 透传元数据（essay_id、source 等）
```

### 3.4 PipelineRunner

系统唯一的编排入口。持有 `ResolvedArtifactBundle` + 各阶段 Provider，调用 `.run(request)` 驱动整个状态机，返回 `(RunTrace, Dict)` 元组。

---

## 4. 配置层（Config Layer）

### 4.1 编译流程

```
bundle.yaml
    ↓  ConfigResolver.load_bundle_file()
ArtifactBundle（refs 未加载）
    ↓  ConfigResolver.load_artifact() × N
ArtifactRef（loaded_data + content_hash）
    ↓  _build_rubric_snapshot() + _build_policy_snapshot()
RubricSnapshot + PolicySnapshot
    ↓  compute_bundle_hash()
ResolvedArtifactBundle（完全冻结，可直接传给 PipelineRunner）
```

**关键入口**：
```python
from src.agents.config_resolver import run as resolve_bundle
# 或者直接：
from src.config.compiler import ConfigCompiler
resolved = ConfigCompiler(configs_root="configs").compile("configs/bundles/xxx.bundle.yaml")
```

### 4.2 量规格式

任务量规文件位于 `configs/tasks/{task_id}/dimension/{dim_id}_rubric.yaml`，结构示例：

```yaml
dim_id: "a4"
dim_name: "工程实践能力"
indicator_description: "..."

scale:
  min: 1
  max: 5
  type: ordinal
  levels:
    1: "初步"
    2: "基础"
    3: "发展中"
    4: "胜任"
    5: "卓越"

dimensions:
  - code: "A4-1"
    name: "问题识别"
    anchors:
      1: "无法识别核心问题"
      3: "能识别主要问题，分析有限"
      5: "准确识别并深度分析问题"
```

### 4.3 模型配置

`configs/model_config.yaml` 按角色分配 LLM，优先级高于 bundle 内的 provider_config：

```yaml
default:
  model: "deepseek-chat"
  api_base: "https://api.deepseek.com/v1"
  api_key_env: "LLM_API_KEY"
  params: {temperature: 0.0, max_tokens: 1536}

raters:
  rater_1: {...}   # 第一评委
  rater_2: {...}   # 第二评委（支持思维链模型）

stages:
  chunking: {...}           # 分块阶段
  evidence_extraction: {...} # 证据抽取阶段
  scoring: {...}            # 评分阶段
  explanation: {...}        # 反馈生成阶段
```

---

## 5. 契约层（Contracts Layer）

所有跨阶段数据流均通过 `src/contracts/` 下的冻结 dataclass 传递，**不使用临时 dict**。

### 5.1 输入端数据流

```
EvaluationRequest
    → NormalizedRequest      （input normalization，加时间戳 + request_id）
    → NormalizedDocument     （文本切分 → TextUnit[]，含 char_count / token_estimate）
    → CoveragePlan[]         （每维度一份，指定扫描哪些 TextUnit）
```

**TextUnit 关键字段**：

| 字段 | 说明 |
|------|------|
| `unit_id` | 块 ID |
| `text` | 块文本 |
| `start_offset / end_offset` | 字符级偏移（半开区间） |
| `chunk_method` | `"rule"` / `"llm_semantic"` / `"llm_hierarchical"` |
| `source_type` | `"human"` / `"ai"` / `"system"` / `"mixed"` / `"unknown"` |

> 对话记录中 AI 输入会被标记为 `source_type="ai"`，证据抽取阶段会**自动过滤**，只采信 `human` 来源的片段。

### 5.2 证据与评分

```
EvidenceSpan[]          （文本引用 + 字符偏移 + facet_ids + support_type）
    → DimensionObservation（每维度一份，汇总 supporting/counter span IDs）
    → ScoreHypothesis[]   （每 rater × 每维度一份，含 score + rationale + descriptor_refs）
    → ConflictRecord[]    （分歧检测结果）
    → AdjudicationRecord[]（裁决结果）
    → FinalDimensionDecision[]（每维度最终分数）
    → CompositeDecision   （可选，聚合总分）
```

### 5.3 运行轨迹

`RunTrace` 是每次评价的完整审计记录：

```python
RunTrace:
  run_id: str                    # 唯一运行 ID
  bundle_id / bundle_version     # 配置版本（用于可重放性验证）
  status: RunStatus              # completed / failed / human_review
  started_at / finished_at       # UTC 时间戳
  node_traces: List[NodeTrace]   # 每个流水线节点的执行记录
  terminal_validation_passed: bool
```

---

## 6. Provider 层

### 6.1 抽象接口

```python
class BaseProvider(ABC):
    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse: ...
```

`LLMRequest` 包含：`prompt`、可选 `system`、可选 `output_schema`（JSON Schema，触发结构化输出）、`params`（temperature 等）。

`LLMResponse` 包含：`content`（raw 文本）、`structured_data`（解析后的 dict，仅当提供了 schema 时有值）、`usage`（token 计量）。

### 6.2 具体实现

| 类 | 说明 |
|----|------|
| `OpenAICompatibleProvider` | 对接任意 `/v1/chat/completions` 端点（OpenAI / DeepSeek / Qwen / 本地 Ollama 等） |
| `GuardedProvider` | 包装层，实现带退避的重试（`RetryConfig`） |
| `LoggingProvider` | 包装层，统计调用次数 / token 消耗 / 耗时 |

### 6.3 工厂构建

```python
from src.providers.factory import build_provider, build_provider_map
from src.contracts.artifact_bundle import ProviderEntryConfig

# 从单条配置构建
provider = build_provider(ProviderEntryConfig(
    api_key_env="LLM_API_KEY",
    model="deepseek-chat",
    api_base="https://api.deepseek.com/v1",
))

# 从 model_config.yaml 构建全套 providers
default_p, rater_ps, stage_ps = _build_providers_from_model_config(
    Path("configs/model_config.yaml")
)
```

---

## 7. 流水线状态机

### 7.1 状态枚举

```
INIT
  ↓
CONFIG_RESOLVED          # bundle 编译完成
  ↓
PREPROCESSED             # 文档归一化 + 分块（TextUnit[]）
  ↓
COVERAGE_PLANNED         # 各维度覆盖计划（CoveragePlan[]）
  ↓
EVIDENCE_EXTRACTED       # LLM 证据抽取（EvidenceSpan[]）
  ↓
OBSERVATION_BUILT        # 证据整理（DimensionObservation[]）
  ↓
SCORED                   # 双评委打分（ScoreHypothesis[]）
  ↓
CONSISTENCY_CHECKED ─────┬→ ADJUDICATED ─────┬→ FEEDBACK_RENDERED → VALIDATED（终止）
                         ├→ FEEDBACK_RENDERED ↑
                         ├→ RE_EXTRACT → COVERAGE_PLANNED（回退重试）
                         ├→ RE_SCORE → OBSERVATION_BUILT（回退重试）
                         └→ HUMAN_REVIEW（终止，需人工介入）
```

**终止状态**：`VALIDATED`（正常完成）、`FAILED`（不可恢复错误）、`HUMAN_REVIEW`（裁决失败，需人工）。

### 7.2 重试保护

`CheckpointManager` 保护 `RE_EXTRACT` / `RE_SCORE` 回退循环，默认最大重试次数为 2（通过 bundle 的 `operational_params.max_retries` 配置）。超限后强制进入 `FAILED`。

---

## 8. 各阶段 Agent 详解

所有 Agent 均为**纯函数**（无状态），由 `PipelineRunner` 按状态机顺序调用。

### Stage 1–2：分块（Chunker）

- 当文档 token 数超过 `chunking_policy.document_processing.token_threshold`（默认 4000）时，调用 LLM 做语义分块
- 短文档直接按段落切分（`rule` 模式）
- 返回带 `chunk_title` 和 `chunk_method` 的 `TextUnit[]`

### Stage 3：覆盖计划（Coverage Planner）

- 按维度和 chunking_policy 中的 `top_k` 配置，选取最相关的 chunks 纳入抽取范围
- 生成 `CoveragePlan`（含 `target_unit_ids`、`required_facets`、`coverage_strategy`）

### Stage 4：证据抽取（Extractor）

```python
# src/agents/extractor.py
def run(plan, document, rubric, provider, template, ...) -> List[EvidenceSpan]
```

- 对每个 `CoveragePlan` 调用一次 LLM，输出 JSON：`{"evidence_spans": [{"quote": ..., "chunk_id": ..., "facets": [...], "support_type": ...}]}`
- 内核自动把 `quote` 与原文做精确/模糊匹配，计算字符偏移
- **过滤掉** `source_type` 为 `"ai"` / `"system"` 的片段
- 每个 required_facet 至少保留一个兜底 span（避免覆盖校验失败）

### Stage 5：证据整理（Observer）

- 按维度汇总 `EvidenceSpan[]` → `DimensionObservation`
- 计算 `observation_confidence`（HIGH / MEDIUM / LOW）

### Stage 6：评分（Scorer）

```python
# src/agents/scorer.py
def run(observation, evidence_spans, rubric, provider, template, rater_id, ...) -> ScoreHypothesis
```

- 每个维度 × 每个 Rater 独立调用一次 LLM
- 输出：`{"proposed_score": int, "descriptor_refs": [...], "confidence": float, "justification": str}`
- 分数被 clamp 到量规量表范围 `[scale_min, scale_max]`
- 若解析失败则以 `temperature=0.0` 重试一次

### Stage 7：一致性检查 + 裁决（Reconciliation）

- 对比各 Rater 的 `ScoreHypothesis`，差值 > 1 或多个维度同向偏移则触发裁决
- 裁决策略来自 `adjudication_policy`（支持第三评委、平均等方式）
- 输出 `ConflictRecord[]` + `AdjudicationRecord[]` + `FinalDimensionDecision[]`

### Stage 8：反馈生成（Feedback）

```python
# src/agents/feedback.py
def run(decisions, observations, spans, ...) -> Dict[str, Any]
```

- 对每个维度调用一次 LLM，生成自然语言评语
- 返回标准化的反馈 dict（详见 [输出结构](#9-输出结构)）

---

## 9. 输出结构

### 9.1 `feedback.json`（主要输出，Web 展示用）

```json
{
  "dimensions": {
    "a4_1": {
      "dimension_name": "问题识别",
      "score": 3,
      "descriptor_refs": ["level_3"],
      "evidence": [
        {"span_id": "span-ext-abc123", "quote": "学生在此处识别出..."}
      ],
      "feedback": "该学生能识别主要工程问题，但分析深度尚待提升...",
      "audit": {
        "uncertainty_note": null,
        "decision_confidence": 0.85,
        "was_adjudicated": false,
        "scoring_records": [
          {"rater_id": "rater_1", "score": 3, "confidence": 0.8, "rationale": "..."},
          {"rater_id": "rater_2", "score": 3, "confidence": 0.9, "rationale": "..."}
        ]
      }
    }
  },
  "generated_at": "2026-05-22T10:30:00+00:00",
  "indicator_score": {"score": 3}
}
```

### 9.2 `run_trace.json`（流水线审计）

```json
{
  "run_id": "run-xxxx",
  "bundle_id": "engineering_eval_baseline",
  "bundle_version": "1.0",
  "status": "completed",
  "started_at": "...",
  "finished_at": "...",
  "node_traces": [
    {
      "node_id": "node_preprocess",
      "status": "success",
      "started_at": "...",
      "finished_at": "...",
      "output_ref": "text_units:42"
    }
  ],
  "terminal_validation_passed": true
}
```

### 9.3 其他产出文件

| 文件 | 说明 |
|------|------|
| `hypotheses.json` | 所有 Rater 的原始假设分（含 rationale） |
| `evidence_spans.json` | 所有证据片段（含字符偏移 + 来源类型） |
| `observations.json` | 各维度观察 + CoveragePlan + TextUnit |
| `conflicts.json` | 分歧检测结果 |
| `adjudication_records.json` | 裁决记录 |
| `_debug/run-{id}/` | LLM 请求/响应原始记录（`debug_bundle=True` 时） |

---

## 10. 现有 CLI 与 Server

### 10.1 单篇评估 CLI

```bash
# 评估一份文件，指定维度 A4
python -m scripts eval data/my_essay.md --dim a4

# 完整参数
python scripts/eval.py data/my_essay.md \
  --bundle configs/bundles/engineering_eval_baseline.bundle.yaml \
  --dim a4 \
  --model-config configs/model_config.yaml \
  --output-dir artifacts/my_task/sample1/A4
```

**内部执行流**（`scripts/eval.py` → `run_single_eval()`）：

```python
# 1. 编译 bundle
resolved = resolve_bundle(bundle_path)

# 2. 构建 providers
default_provider, rater_providers, stage_providers = \
    _build_providers_from_model_config(model_config_path)

# 3. 加载 Prompt 模板
prompt_templates = _load_prompt_templates()

# 4. 执行评估
result = run_single_eval(
    essay_id=essay_id,
    essay_text=essay_text,
    resolved=resolved,
    default_provider=default_provider,
    rater_providers=rater_providers,
    stage_providers=stage_providers,
    prompt_templates=prompt_templates,
    output_dir=out_dir,
)
# result.feedback_dict 即为最终 feedback.json 内容
```

### 10.2 开发服务器（`scripts/server.py`）

一个极简的纯 Python HTTP 服务器，提供：

- 静态文件服务（`/` → 项目根目录）
- `POST /api/corrections`：接收前端人类批改事件，写入 `experiments/pending_corrections.json`

**启动**：
```bash
python scripts/server.py --port 8000
```

该服务器仅用于开发期前端调试，**不包含评估 API**。Web 接入需另行封装（见下节）。

---

## 11. Web 接入指南

### 11.1 集成原则

内核（`src/`）是纯 Python 库，完全**框架无关**。推荐接入方式：

1. 在 Web 框架中（FastAPI / Flask / Django）实现一个 `/api/evaluate` POST 端点
2. 在该端点内部调用 `run_single_eval()` 或直接操作 `PipelineRunner`
3. 将返回的 `feedback_dict` 序列化为 JSON 响应

### 11.2 最小集成示例（FastAPI）

```python
# app/api/evaluate.py
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.agents.config_resolver import run as resolve_bundle
from src.evaluation.runner import run_single_eval
from src.providers.factory import build_provider
from src.contracts.artifact_bundle import ProviderEntryConfig
from src.providers.prompt_loader import PromptLoader

router = APIRouter()

# --- 应用启动时预编译（推荐缓存，避免每次请求重复编译） ---
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLE_PATH = _PROJECT_ROOT / "configs/bundles/engineering_eval_baseline.bundle.yaml"
_MODEL_CONFIG = _PROJECT_ROOT / "configs/model_config.yaml"

# 可在 lifespan 中初始化，此处简化为模块级
_resolved = None
_providers = None


def _get_resolved(dim_id: str):
    """按维度编译 bundle（实际应用中应带缓存）。"""
    import yaml, tempfile
    raw = yaml.safe_load(_BUNDLE_PATH.read_text())
    raw["active_dim_id"] = dim_id.lower()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".bundle.yaml", delete=False) as f:
        yaml.safe_dump(raw, f)
        tmp = Path(f.name)
    try:
        return resolve_bundle(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def _load_providers():
    import yaml
    from src.providers.factory import build_provider
    from src.contracts.artifact_bundle import ProviderEntryConfig
    data = yaml.safe_load(_MODEL_CONFIG.read_text())

    def _entry(d):
        return ProviderEntryConfig(
            api_key_env=d.get("api_key_env", "LLM_API_KEY"),
            model=d.get("model", ""),
            api_base=d.get("api_base", ""),
            params=dict(d.get("params") or {}),
        )

    default = build_provider(_entry(data.get("default", {})))
    raters = {k: build_provider(_entry(v)) for k, v in (data.get("raters") or {}).items()}
    stages = {k: build_provider(_entry(v)) for k, v in (data.get("stages") or {}).items()}
    return default, raters, stages


def _load_prompt_templates():
    loader = PromptLoader()
    templates = {}
    prompts_dir = _PROJECT_ROOT / "configs/prompts"
    for name, filename in [
        ("evidence_extraction", "evidence_extraction.yaml"),
        ("scoring", "scoring.yaml"),
        ("explanation", "explanation.yaml"),
        ("chunking", "chunking.yaml"),
    ]:
        p = prompts_dir / filename
        if p.exists():
            templates[name] = loader.load(p)
    return templates


# --- API 端点 ---

class EvaluateRequest(BaseModel):
    text: str           # 学生提交的文本全文
    dim: str            # 评价维度，如 "a4"、"b1"
    essay_id: str = "web_submission"

class EvaluateResponse(BaseModel):
    success: bool
    essay_id: str
    feedback: dict      # feedback.json 内容
    run_id: str


@router.post("/evaluate", response_model=EvaluateResponse)
async def evaluate(req: EvaluateRequest):
    import asyncio
    from pathlib import Path

    try:
        resolved = _get_resolved(req.dim)
        default_p, rater_ps, stage_ps = _load_providers()
        templates = _load_prompt_templates()

        # 临时输出目录（可改为内存 / S3）
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = run_single_eval(
                essay_id=req.essay_id,
                essay_text=req.text,
                tsv_row=None,
                resolved=resolved,
                default_provider=default_p,
                rater_providers=rater_ps,
                stage_providers=stage_ps,
                log_providers=[],
                prompt_templates=templates,
                output_dir=Path(tmp_dir),
                verbose=False,
                debug_bundle=False,
            )

        return EvaluateResponse(
            success=result.success,
            essay_id=result.essay_id,
            feedback=result.feedback_dict,
            run_id=result.trace_dict.get("run_id", ""),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

### 11.3 异步化建议

`run_single_eval()` 是同步阻塞调用（底层 openai SDK 是同步的），单次评估耗时 30–120 秒。接入 Web 时需：

```python
import asyncio

# 在异步路由中，用 run_in_executor 包裹同步调用
result = await asyncio.get_event_loop().run_in_executor(
    None,       # 使用默认 ThreadPoolExecutor
    lambda: run_single_eval(...)
)
```

或使用任务队列（Celery / RQ / ARQ）将评估任务异步化，前端轮询状态。

### 11.4 性能优化建议

| 优化点 | 建议 |
|--------|------|
| Bundle 编译 | 应用启动时预编译所有维度并缓存 `ResolvedArtifactBundle`（按 `dim_id` 键） |
| Provider 构建 | 提前初始化所有 provider，复用 HTTP 连接池 |
| Prompt 模板 | 启动时一次性加载到内存 |
| 并发控制 | 每次评估涉及 8–15 个 LLM 调用，建议限制并发评估数（如 4–8 并行） |
| 结果持久化 | 将 `feedback_dict` 和 `trace_dict` 写入数据库，支持查询历史记录 |

### 11.5 输出目录策略

`run_single_eval()` 要求提供 `output_dir: Path`，会在其中写出 7 个 JSON 文件。Web 场景可以：

- **选项 A**：传入临时目录，评估完成后从 `result.feedback_dict` / `result.trace_dict` 取值，无需读文件
- **选项 B**：传入持久化目录（如 `artifacts/{task}/{essay_id}/{dim}/`），直接对外暴露静态文件

### 11.6 数据流概览（Web 版）

```
Browser
  POST /api/evaluate { text, dim, essay_id }
        │
        ▼
Web API Layer (FastAPI/Flask)
  1. 参数校验
  2. run_in_executor → run_single_eval()
        │
        ▼
MAS 内核 (src/)
  ConfigCompiler.compile()          → ResolvedArtifactBundle
  PipelineRunner(bundle, providers)
    .run(EvaluationRequest)         → (RunTrace, feedback_dict)
        │
        ▼
Web API Layer
  3. 持久化到 DB / 文件系统
  4. 返回 JSON 响应
        │
        ▼
Browser
  渲染评分 + 反馈 + 证据引用
```

### 11.7 Corrections API（人工批改回写）

现有 `scripts/server.py` 已实现 `POST /api/corrections` 端点，接受：

```json
{
  "sample_id": "sample_001",
  "score_corrections": [
    {"dimension_id": "a4_1", "corrected_score": 4, "reason": "学生展示了更多细节"}
  ],
  "feedback_corrections": [],
  "evidence_additions": []
}
```

数据写入 `experiments/pending_corrections.json`，由外环优化流程在下次评估前应用。Web 接入时可直接复用该接口逻辑，或移植到 Web 框架中。

---

## 12. 环境变量与密钥

所有 API Key 通过环境变量注入，不在代码或配置文件中硬编码。

| 变量 | 说明 |
|------|------|
| `LLM_API_KEY` | 主模型 API Key（兜底） |
| `RATER_1_API_KEY` | 第一评委 Key（空时回落到 `LLM_API_KEY`） |
| `RATER_2_API_KEY` | 第二评委 Key |
| `RATER_3_API_KEY` | 第三评委 Key（仅裁决时用） |
| `CHUNKING_API_KEY` | 分块阶段 Key |
| `EXTRACTOR_API_KEY` | 证据抽取阶段 Key |
| `SCORING_API_KEY` | 评分阶段 Key |
| `FEEDBACK_API_KEY` | 反馈生成阶段 Key |
| `LLM_MODEL` | 全局模型 ID（被 model_config.yaml 覆盖） |
| `LLM_API_BASE` | 全局 API Base URL |
| `LLM_TIMEOUT_SECONDS` | 单次调用超时（默认 60s） |
| `LLM_MAX_RETRIES` | 单次调用最大重试（默认 3） |
| `OUTER_LOOP_API_KEY` | 外环 Agent Key（仅外环优化使用） |

推荐使用 `.env` 文件 + `python-dotenv` 管理（项目已内置）：

```bash
# .env
LLM_API_KEY=sk-xxx
RATER_2_API_KEY=sk-yyy
LLM_MODEL=deepseek-chat
LLM_API_BASE=https://api.deepseek.com/v1
```

---

## 13. 依赖清单

### 运行时依赖（`pyproject.toml [dependencies]`）

| 包 | 版本 | 用途 |
|----|------|------|
| `pydantic` | >=2.5.0 | Bundle / Schema 校验 |
| `pyyaml` | >=6.0.1 | YAML 配置加载 |
| `jinja2` | >=3.1.3 | Prompt 模板渲染 |
| `typer` | >=0.9.0 | CLI 入口 |

### LLM 调用依赖（`[real-provider]` extras）

```bash
pip install 'mas-rubric-evaluation[real-provider]'
# 等同于：
pip install openai>=1.10.0 httpx>=0.26.0
```

### 安装（开发模式）

```bash
# 1. 创建虚拟环境
python -m venv .venv && source .venv/bin/activate

# 2. 安装内核 + LLM 依赖 + 开发工具
pip install -e '.[dev,real-provider]'
# 或使用 uv（更快）：
uv sync --extra real-provider

# 3. 配置密钥
cp .env.example .env && vim .env
```

### Web 框架（需自行添加）

内核不依赖任何 Web 框架。根据需要添加：

```bash
# FastAPI + uvicorn（推荐）
pip install fastapi uvicorn[standard]

# 或 Flask
pip install flask

# 异步任务队列（长任务推荐）
pip install celery redis
# 或
pip install arq
```

---

## 附录：常见问题

**Q：如何添加新的评价任务？**

1. 在 `configs/tasks/{task_id}/dimension/` 下创建量规 YAML
2. 在 `configs/tasks/{task_id}/task_context.yaml` 配置任务上下文
3. 修改 `configs/bundles/xxx.bundle.yaml` 中的 `active_task_id`
4. 无需修改任何 Python 代码

**Q：如何支持新的 LLM 提供商？**

只要该服务兼容 OpenAI Chat Completions API（即有 `/v1/chat/completions` 端点），直接修改 `configs/model_config.yaml` 中的 `api_base` 和 `api_key_env` 即可。

**Q：`run_single_eval()` 和 `PipelineRunner.run()` 有什么区别？**

`run_single_eval()` 是更高层的封装：
- 负责构建 `EvaluationRequest`
- 调用 `PipelineRunner.run()`
- 将所有中间产物（hypotheses / spans / observations 等）写出到 `output_dir`
- 返回 `RunResult`（含 `feedback_dict` 和 `trace_dict`）

`PipelineRunner.run()` 是纯内核入口，不做文件 I/O，返回 `(RunTrace, Dict)`。Web 服务中直接调用 `run_single_eval()` 即可，无需手动组装所有中间产物。

**Q：评估失败（`result.success == False`）时如何处理？**

检查 `result.trace_dict["status"]`：
- `"failed"`：流水线内部错误，看 `node_traces` 中 `status != "success"` 的节点和 `error_message`
- `"human_review"`：分歧无法自动裁决，需要人工介入；`feedback_dict` 仍会有部分内容

**Q：并发评估会有状态污染吗？**

不会。`PipelineRunner` 的每个实例是独立的（持有各自的内部状态 `_last_*`），`ResolvedArtifactBundle` 是冻结的不可变对象，可安全地在多个 runner 实例间共享。
