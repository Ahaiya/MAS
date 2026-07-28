# MAS 重构设计总文档

> 本文档是本轮重构的**唯一权威依据**。一切以本文为准，现有代码仅作参考。
> 目标读者：打分引擎负责人（吴海涛）、Web 平台负责人（李领康）、后续接手的开发者/Agent。
> 状态标记：✅ 已定 ｜ 🟡 待进一步讨论 ｜ 🔬 需调研/一期不展开

---

## 0. 阅读指引

本次重构是**双目标并行**：

1. **落地增值评价迭代**——按《增值评价工具迭代需求文档》新增：多源数据接入、评价流水线 v2（独立双链路）、增值计算、学生一级实体。
2. **全局简化重构**——把现有内核（尤其 1300+ 行的 `pipeline/runner.py`）瘦身到符合工程规范的形态。

两个目标在同一套目标架构中统一实现，不做两次改动。

本文档已明确的设计决策见每节；**尚未定稿的部分**集中列在 [§11 待决事项清单](#11-待决事项清单)，动工前需先拍板。

---

## 1. 背景与目标

### 1.1 现状能力

现有系统已跑通「一个量规 + 一个数据包 → 双评 + 仲裁 → 评分与反馈」的单件评价链路，具备**配置驱动、平台无关**特点：

- 零硬编码：维度名、量表、权重、Prompt 全部来自 YAML。
- 状态机驱动的确定性流水线。
- 多评委：2–3 个 LLM Rater 独立打分，分歧自动裁决。
- OpenAI-compatible：任何兼容 `/v1/chat/completions` 的服务可接入。

### 1.2 本轮定位升级

系统定位从「评价一件作品」扩展为「**追踪一个学生**」：学生成为第一级实体，课程/项目只是某次测量的场景。这带来四项新能力和一次全局简化。

| 能力 | 来源 | 本轮范围 |
|------|------|---------|
| 多源数据接入（PDF/Word/Excel/PPT…） | 迭代文档 §4 | ✅ 纳入引擎，新增接入层 |
| 评价流水线 v2（独立双链路） | 迭代文档 §5 | ✅ 必须落地 |
| 增值计算（跨轮次） | 迭代文档 §6 | 🟡 依赖实体模型，一期暂缓细节 |
| 学生一级实体 / 时间轴 | 迭代文档 §3 | 🟡 引擎侧承载多少待定 |
| 全局架构简化 | 本轮新增 | ✅ 状态机线性化、埋点剥离、中间阶段合并 |

### 1.3 协作边界（迭代文档 §2）

| 路线 | 负责人 | 职责 |
|------|--------|------|
| 打分引擎（本仓库） | 吴海涛 | 模态解析、取证+评分流水线、仲裁、增值计算、反馈生成 |
| Web 平台/前端 | 李领康 | 任务发起、学生邀请/提交、档案与时间轴、结果可视化、教师审核台 |

引擎侧坚持「**轻量**」原则：只关心「量规 + 数据包」这一最小输入单元，不关心数据包内部来源；任务管理、学生邀请等业务留在前端。

---

## 2. 目标架构总览

```
┌──────────────────────────────────────────────────────────────────────┐
│  接入层 Ingest（新增）                                                   │
│  多源文件(PDF/Word/Excel/PPT/JSON/MD/压缩包) ──► 第三方解析API ──► 统一文本 │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ DataPackage（统一文本 + 元数据）
┌───────────────────────────────▼──────────────────────────────────────┐
│  内环流水线 v2（简化后线性编排）                                          │
│                                                                        │
│   Chunk（共享，可选，仅长文档）                                           │
│        ├──────────────┬──────────────┐                                 │
│        ▼              ▼                                                │
│   Rater1 链          Rater2 链         （两条独立链，各自取证+评分）        │
│   {取证1 → 评分1}    {取证2 → 评分2}                                     │
│        └──────┬───────┘                                                │
│               ▼                                                        │
│         比较 Reconcile ──一致──► 直接决策                                 │
│               └────分歧────► Rater3 仲裁（看双链 + 量规 + 数据包）► 决策    │
│               ▼                                                        │
│           Feedback（每维度反馈 + 雷达图数据）                             │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ 单轮评价结果（含维度分 + 证据链 + 反馈）
┌───────────────────────────────▼──────────────────────────────────────┐
│  增值计算 Value-Added（🟡 依赖实体模型，一期暂缓）                         │
│  同一学生跨轮次结果 ──► 增益指标（一期限「同一量规」）                       │
└──────────────────────────────────────────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────────┐
│  人在回路 Human-in-loop（🟡 需要，但实现方式待讨论）                       │
│  教师审核修正 ──► ??? ──► 影响后续评价                                    │
└──────────────────────────────────────────────────────────────────────┘
```

**与现状的关键差异**：

| 维度 | 现状 | v2 目标 |
|------|------|---------|
| 输入 | 已是纯文本 `.md` | 多源文件 → 接入层 → 统一文本 |
| 取证-评分关系 | 共享证据 → Rater1/2 背靠背打分 | 各 Rater 独立完成「取证+评分」完整链路 |
| 仲裁输入 | 看双方 score | 看双方**完整链路**（取证+评分）+ 量规 + 数据包 |
| 编排 | 状态机 + RE_EXTRACT/RE_SCORE 回退循环 | 线性函数链 + 单仲裁分支 |
| Observer/CoveragePlanner | 独立阶段 | 并入 Rater 链内部 |
| debug 埋点 | 交织在主流程（占 runner 40%+ 行数） | 从主流程剥离 |
| 计算成本 | 每轮 1 次取证 + 2 次评分 | 每轮 2 次取证 + 2 次评分（翻倍，迭代文档已确认接受） |

---

## 3. 接入层设计（Ingest，新增）✅

### 3.1 职责与边界

- **输入**：任意格式原始文件字节流（PDF/Word/Excel/PPT/JSON/Markdown/图片），或含多名学生数据的压缩包。
- **输出**：`DataPackage`——统一后的文本（优先 Markdown）+ 结构元数据。
- **原则**：解析能力**不自研**，封装成熟第三方商用 API，按调用计费（迭代文档 §4）。引擎只做「多源 → 统一文本」的归一化与封装。

### 3.2 选型建议（🔬 调研结论）

综合国内可用性、格式覆盖、计费模式，**默认选型建议阿里云文档智能（Document Mind）文档解析大模型版**：

| 候选 | 格式覆盖 | 计费 | 适用场景 |
|------|---------|------|---------|
| **阿里云 Document Mind（大模型版）** ✅ 默认 | PDF/Word/PPT/Excel/图片 + MD/HTML/EPUB/TXT 等 → Markdown | 3000 页/月免费，超出按量付费 | 国内低延迟、格式全、与 deepseek 同区 |
| LlamaParse | 90+ 格式 → MD/JSON | 10000 credits/月免费，四档计费 | 若走 LlamaIndex 生态 |
| Reducto | 复杂版式、可审计（字段引用） | credit 制（北美 1000 credits≈$1） | 对抽取精度/合规要求极高时 |

> 选型**非最终结论**，最终以试评测后确定；因此接入层设计为**可插拔 provider**，切换不影响上层。

### 3.3 抽象接口

复用现有 `providers/` 的 provider 抽象思路，接入层独立成 `src/ingest/`：

```python
# src/ingest/base.py
@dataclass(frozen=True)
class ParseResult:
    text: str                      # 统一文本（优先 markdown）
    mime_type: str                 # 原始文件 MIME
    page_count: int | None         # 页数（计费/审计用）
    parser_name: str               # 解析器标识
    raw_metadata: dict             # 解析器原始返回（透传，不解释）

class ParserProvider(Protocol):
    def parse(self, content: bytes, filename: str) -> ParseResult: ...
```

```python
# src/ingest/aliyun_docmind.py   —— 默认适配器
class AliyunDocMindParser:
    def parse(self, content: bytes, filename: str) -> ParseResult: ...
```

### 3.4 DataPackage 归一化

```python
# src/ingest/package.py
@dataclass(frozen=True)
class DataPackage:
    package_id: str                # 唯一标识（贯穿引擎与前端）
    text: str                      # 供流水线消费的统一文本
    source_files: list[SourceFile] # 原始文件清单（多文件/压缩包场景）
    metadata: dict                 # 学生ID/任务ID/轮次序号/时间戳（前端透传）
```

- **压缩包 / 批量导入**：`package.py` 负责解压、逐文件解析、按约定合并为一个或多个 `DataPackage`（一名学生一包）。批量场景（一次上传多名学生）由前端拆分后逐包调用引擎，或引擎提供批量入口——🟡 批量入口形态待与前端对齐。
- **元数据透传**：学生ID/任务ID/轮次/时间戳等**引擎不解释、仅透传并回填到结果**，用于前端聚合与增值计算。

### 3.5 密钥管理

沿用现有 `.env` 约定，新增：

| 变量 | 说明 |
|------|------|
| `PARSER_API_KEY` | 解析服务 API Key |
| `PARSER_ENDPOINT` | 解析服务端点 |
| `PARSER_PROVIDER` | 解析器标识（默认 `aliyun_docmind`） |

---

## 4. 评价流水线 v2（独立双链路）✅

### 4.1 核心改动

现有：`共享 Extractor → Observer → Scorer×2 背靠背`。
v2：**每个 Rater 独立完成「取证 + 评分」完整链路**，互不共享证据集。

- **Rater 1**：独立取证 1 + 评分 1。
- **Rater 2**：独立取证 2 + 评分 2。
- **Rater 3（仲裁）**：先看 Rater1、Rater2 各自的**取证+评分链路**，按需结合最初的量规与数据包给出最终判断。

**收益**：消除共享证据带来的相关性偏差，两条链真正独立。
**代价**：计算成本翻倍（每轮 2 次完整取证+评分），迭代文档已确认接受。

### 4.2 阶段编排（线性）

```
DataPackage
  → chunk()          # 共享分块，仅长文档触发（token > 阈值）；两 Rater 共用
  → rate(rater_1)    # 单 Rater 完整链：取证 → 评分 → RaterChainResult
  → rate(rater_2)    # 同上，独立
  → reconcile()      # 比较两条链的分数
       ├ 一致 → 直接决策 FinalDimensionDecision
       └ 分歧 → adjudicate(rater_3) → 决策
  → feedback()       # 每维度反馈 + 雷达图数据
```

- 分块**共享**是有意为之：分块只是把长文切成可处理单元，不引入证据偏差；两 Rater 拿同一份 chunks 各自独立取证，独立性保持在「取证选哪些证据」层面。
- 中间的 `Observer`（证据聚合为 DimensionObservation）与 `CoveragePlanner`（生成扫描计划）**并入 `rate()` 内部**，不再单列为流水线阶段。

### 4.3 单 Rater 链契约

```python
# src/contracts/scoring.py（v2 新增）
@dataclass(frozen=True)
class RaterChainResult:
    rater_id: str
    dimension_scores: list[DimensionScore]  # 每维度：分数 + 证据span + rationale
    evidence_spans: list[EvidenceSpan]       # 本 Rater 独立取到的证据
    # 关键：仲裁需要看到完整链路，故证据与评分绑定在同一结构

@dataclass(frozen=True)
class DimensionScore:
    dimension_id: str
    score: int
    descriptor_refs: list[str]
    supporting_span_ids: list[str]
    rationale: str
    confidence: float
```

### 4.4 仲裁改动

- 仲裁触发规则仍由 `policies/adjudication`（配置）决定：分差 > 阈值 或 多维度同向偏移。
- **仲裁输入升级**：从「看两个分数」变为「看两条 `RaterChainResult` 完整链路 + 量规 + 数据包」。Rater3 的 prompt 需重写以承载双链对比。
- 仲裁结果即最终决策；**取消 RE_EXTRACT/RE_SCORE 回退循环**——Rater3 重评本身就是「重评」，无需独立回退机制。

---

## 5. 简化决策（全局重构）✅

### 5.1 状态机 → 线性函数链

**现状问题**：`pipeline/runner.py` 1334 行，把一条本质线性的流程包装成带 `RE_EXTRACT`/`RE_SCORE` 重入的 `while` 状态机。真正的路由逻辑（`_route_after_consistency_check` / `_route_after_adjudication`）不足 20 行，其余是状态流转样板。且 `orchestrator/graph.py`、`router.py` 已不存在（文档滞后），说明上轮已简化过一半。

**目标**：

- 主流程改为线性函数调用链（`chunk → rate → rate → reconcile →[adjudicate]→ feedback`），错误直接抛出/单阶段重试。
- 去掉 `RE_EXTRACT`/`RE_SCORE` 回退循环与 `CheckpointManager` 回退计数。
- `orchestrator/` 三个残余模块（`states.py` / `checkpoints.py` / `trace_store.py`）：状态枚举与回退删除；审计轨迹（trace）保留但下沉为轻量记录（见 §5.3）。
- 预期主流程从 1334 行瘦到 **200–300 行**。

### 5.2 中间阶段并入 Rater 链

| 现有阶段 | v2 去向 |
|---------|--------|
| `chunker.py` | ✅ 保留（共享前置） |
| CoveragePlanner（内联于 runner） | 并入 `rate()`，不单列 |
| `observer.py` | 并入 `rate()`（取证后直接喂评分，独立链无需跨 Rater 聚合） |
| `extractor.py` + `scorer.py` | 合并为 `agents/rater.py`（单链取证+评分） |
| `reconciliation.py` | 保留，拆出仲裁到 `agents/adjudicator.py` |
| `feedback.py` | ✅ 保留 |

### 5.3 debug 埋点剥离

**现状问题**：`_debug_node_start/finish/write_node_artifact/route_decision/fallback` 在每个阶段重复 3–5 次，与业务逻辑交织，占 `runner.py` 40%+ 行数。

**目标**：

- 审计需求（可重放、节点耗时）保留，但改为**轻量可选 trace**——主流程只在阶段边界记一条结构化事件，不逐字段写 artifact。
- `debug/bundle.py`（DebugBundleWriter）降级为可选调试工具，默认关闭，不侵入主流程签名。

---

## 6. 目标目录编排 ✅

```
MAS/
├── src/
│   ├── ingest/                   # 【新增】接入层
│   │   ├── base.py               # ParserProvider 协议 + ParseResult
│   │   ├── aliyun_docmind.py     # 阿里云文档智能适配器（默认）
│   │   ├── registry.py           # 解析器注册/工厂（按 PARSER_PROVIDER 构建）
│   │   └── package.py            # DataPackage 归一化（解压/多文件合并/元数据透传）
│   ├── config/                   # 【保留】bundle 编译层
│   │   ├── compiler.py           # ConfigCompiler
│   │   ├── resolver.py           # ConfigResolver（加载+校验）
│   │   ├── schema.py             # Pydantic 文件格式校验
│   │   └── freeze.py             # 内容哈希
│   ├── contracts/                # 【保留+v2调整】数据契约
│   │   ├── artifact_bundle.py
│   │   ├── request_models.py     # + DataPackage 输入边界
│   │   ├── evidence.py
│   │   ├── scoring.py            # + RaterChainResult / DimensionScore
│   │   └── trace.py              # 轻量化
│   ├── pipeline/                 # 【简化】编排（合并 orchestrator）
│   │   ├── runner.py             # 线性编排入口（~250 行）
│   │   └── validators.py
│   ├── agents/                   # 【v2重组】LLM 调用
│   │   ├── chunker.py            # 共享分块（保留）
│   │   ├── rater.py              # 【新增】单 Rater 完整链：取证+评分（合并 extractor+observer+scorer）
│   │   ├── adjudicator.py        # 【新增】Rater3 仲裁（看双链）
│   │   ├── feedback.py           # 反馈生成（保留）
│   │   └── prompt_builders.py    # Prompt 构建（保留）
│   ├── policies/                 # 【保留】纯计算策略（无 LLM）
│   │   ├── aggregation.py        # 聚合（auto_equal）
│   │   ├── adjudication.py       # 仲裁触发规则
│   │   ├── explanation.py
│   │   └── rubric_core.py
│   ├── providers/                # 【保留】LLM Provider 抽象
│   │   └── …（base/factory/openai_compatible/guards/…）
│   ├── evaluation/
│   │   └── runner.py             # run_single_eval()（单包评估入口）
│   ├── valueadd/                 # 【新增占位，🟡一期暂缓】增值计算
│   │   └── __init__.py
│   ├── humanloop/                # 【由 outer_loop 演进，🟡待重设计】人在回路
│   └── utils/                    # quote_matcher / dialogue_sources 等
├── configs/                      # YAML 配置（见 §7）
├── scripts/                      # CLI + Server 入口（见 §8）
├── frontend/                     # 前端审阅台（前端负责人维护）
├── artifacts/                    # 评估结果输出
└── data/                         # 训练样本 + 人工分数
```

**删除/降级**：

- `src/orchestrator/graph.py`、`router.py`：已不存在，文档同步删除引用。
- `src/orchestrator/`：`states`/`checkpoints` 删除，`trace_store` 合并入 `pipeline` 并轻量化。
- `src/debug/`：降级为可选调试工具，默认不参与主流程。
- `src/agents/extractor.py`、`observer.py`、`scorer.py`：合并进 `agents/rater.py`。
- `src/outer_loop/`：演进为 `src/humanloop/`，具体形态待 §11 定夺。

==【调整】'evaluation/runner.py  # run_single_eval()（单包评估入口）'  这个文件是否有存在的必要？建议换一个形式==

---

## 7. 配置存储结构 ✅

沿用现有「Bundle 入口 + 分层引用」模型，零硬编码原则不变。v2 的改动集中在 **prompts 与 model_config**。

==【调整】结构上 bundle.yaml 可以直接在 configs/ 根目录下, 另外请思考，这个 model_config.yaml 是否与.env 功能冲突？如果有，要怎么解决？==

### 7.1 目录结构

```
configs/
├── model_config.yaml                            # providers（各角色模型端点）+ runtime（并发/超时/重试）
├── adjudication.yaml                            # 仲裁触发规则：两个整数
├── tasks/
│   └── {task_name}/
│       └── dimension/
│           └── {dim_id}_rubric.yaml             # 二级指标量规（观测点 + 各档锚点）
└── prompts/
    ├── select.yaml                # 选段提示词
    ├── extraction.yaml            # 取证提示词
    ├── scoring.yaml               # 评分提示词
    ├── adjudication.yaml          # Rater3 仲裁提示词（双链对比）
    └── feedback.yaml              # 反馈生成提示词
```

> 取证与评分分两个模板（一条链内先取证后评分），但**语义上归属同一 Rater**；
> `adjudication.yaml` 承载双链对比的仲裁 prompt。

### 7.2 配置路径约定

没有 bundle 文件——路径全部由约定固定，不存在引用解析：

- 仲裁策略 → `{configs_root}/adjudication.yaml`
- 提示词　 → `{configs_root}/prompts/{stage}.yaml`（文件名即阶段名）
- 量规　　 → `{configs_root}/tasks/{task_id}/dimension/{dim_id}_rubric.yaml`

任务由调用现场经 `--task` 传入，不写在任何配置文件里：改一个 tracked 文件来切任务，
每次实验都会带一个脏 diff，多任务并行还会互相冲突。

### 7.3 model_config.yaml（v2）

```yaml
default:
  model: "deepseek-chat"
  api_base: "https://api.deepseek.com/v1"
  api_key_env: "LLM_API_KEY"
  params: {temperature: 0.0, max_tokens: 1536}

raters:                    # v2：每个 rater 承载「取证+评分」整链
  rater_1: {...}
  rater_2: {...}
  rater_3: {...}           # 仲裁

parser:                    # 【v2新增】接入层解析服务
  provider: "aliyun_docmind"
  api_key_env: "PARSER_API_KEY"
  endpoint_env: "PARSER_ENDPOINT"
```

---

## 8. 脚本入口 ✅

==【调整】执行脚本入口 收到一个文件里面去，或者你可以提出更通用的方案==

```
scripts/
├── __main__.py           # 统一入口调度（python -m scripts <cmd>）
├── mas.py                # 命令注册
├── eval.py              # 单包评估 CLI
└── server.py            # 前端开发服务器 + 人在回路 API
```

### 8.1 单包评估 CLI

```bash
# 评该任务下全部一级指标
python scripts/cli.py eval <file> --task experiment
# 只评一个一级指标；--configs 缺省为 configs/
python scripts/cli.py eval <file> --task experiment --dim a1
```

`--task` 无默认值：漏传即报错并列出可选任务，不沿用任何配置文件里的值。

内部执行流：

```python
# 1. 读文件 → 切分 → DataPackage
package, dropped = read_text_file(input_path, package_id=input_path.stem)
# 2. 按约定路径读配置 + 建 providers
engine = Engine.from_configs(configs_root, task_id, output_dir=output_dir)
# 3. 执行评价（双链 → 仲裁 → 反馈），产物按 {task}/{sample}/{dim}/ 落盘
results = engine.evaluate(package, dim=dim)
```

### 8.2 产物

==【调整】输出产物结构需要进一步讨论==

写入 `artifacts/{task}/{sample_name}/{dim}/`：

| 文件 | 说明 |
|------|------|
| `feedback.json` | 各维度分数 + 反馈 + 证据引用 + 雷达图数据 + `indicator_score` |
| `rater_chains.json` | 【v2】两条 Rater 链完整结果（取证+评分） |
| `adjudication_records.json` | 仲裁记录 |
| `run_trace.json` | 轻量审计轨迹 |

### 8.3 开发服务器

```bash
python scripts/server.py --port 8000
# 浏览器访问 http://127.0.0.1:8000/frontend/index.html
```

- 静态文件服务 + 人在回路回写 API（形态见 §11）。
- 生产 Web 接入由前端团队封装，引擎作为纯库被调用（`run_single_eval` 框架无关）。

---

## 9. 增值计算（🟡 一期暂缓细节）

- **最小前提**：同一学生至少两轮测量结果落在同一评价维度上。
- **一期范围**：仅支持「**同一量规、跨轮次**」的增值计算；「跨量规折算」列为二期问题。
- **架构归属**：`src/valueadd/`，形态（无状态子服务 vs. policy 模块）**依赖学生/轮次实体模型定稿后再设计**——见 §11。
- **触发方**：建议前端聚合多轮引擎结果后调用引擎的增值子服务（迭代文档 §2 倾向）。

本节仅占位，实体模型定稿前不展开实现细节。

---

## 10. 反馈与输出

- 每轮评价结束给出反馈，包含**雷达图数据**（供前端渲染）+ 文字改进建议。
- 反馈以文字为主、图形化为辅，服务学生理解与改进。
- `feedback.json` 结构在现有基础上补充雷达图所需的维度分数数组（前端可视化契约需与李领康对齐）。

---

## 11. 待决事项清单

动工前需拍板的开放问题，按优先级：

| # | 事项 | 影响范围 | 现状 |
|---|------|---------|------|
| 1 | **学生一级实体在引擎侧承载多少** | 增值计算形态、数据模型、是否有状态 | 🟡 未定，需专项讨论 |
| 2 | **人在回路的实现方式** | `humanloop/` 全部设计；是否保留 CorrectionAgent→改配置的机制，还是改为直接结果修正 | 🟡「人在回路需要，但怎么实现待讨论」 |
| 3 | **增值计算形态** | `valueadd/` 设计 | 🟡 依赖 #1 |
| 4 | **解析 API 最终选型** | `ingest/` 具体适配器 | 🔬 默认建议阿里云 Document Mind，待试评测确认 |
| 5 | **批量导入入口** | 一次上传多学生压缩包由引擎批量还是前端拆分 | 🟡 需与前端对齐 |
| 6 | **前端数据契约** | 学生ID/任务ID/轮次/时间戳贯穿规则、雷达图数据结构 | 🟡 需与李领康对齐（迭代文档 §2 列为需尽快对齐项） |
| 7 | **状态机保守 vs. 激进简化** | 是否保留回退/重试观察一段 | ✅ 已定激进简化，但保留轻量 trace |

---

## 12. 建议实施分期

在 §11 未决项拍板范围内，建议分期推进（互不阻塞）：

- **一期（引擎内核，可立即启动）**：流水线 v2 独立双链路 + 状态机线性化 + debug 剥离 + 中间阶段合并。不依赖任何待决项。
- **二期（接入层）**：`ingest/` 落地，先定抽象接口（#4 可后置具体 API），跑通「文件 → DataPackage → 评价」。
- **三期（增值 + 人在回路）**：待 #1/#2 拍板后，`valueadd/` 与 `humanloop/` 设计并落地。

---

## 附：术语对照

| 术语 | 含义 |
|------|------|
| DataPackage | 接入层归一化后的统一输入单元（一名学生一次提交） |
| RaterChainResult | v2 中单个 Rater 的完整链路结果（取证+评分绑定） |
| Bundle | 配置包入口 YAML，声明量规/策略/prompt/激活任务 |
| ResolvedArtifactBundle | Bundle 编译后的冻结不可变对象 |
| 增值（Value-Added） | 同一学生跨轮次能力增益 |
