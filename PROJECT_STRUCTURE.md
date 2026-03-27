# MAS 项目目录结构说明

## 目录（文件夹）

| 名称 | 作用 |
|------|------|
| `src/` | 核心源代码，包含所有 Agent、流水线、策略、Provider、配置加载器等模块 |
| `tests/` | 测试代码，分为 unit / integration / e2e 三层 |
| `scripts/` | 独立可执行脚本，如 `eval.py`（单篇评估）、`compute_qwk.py`（批量 QWK 计算）等 |
| `configs/` | 系统配置唯一来源，包含量规、策略（聚合/裁决）、bundle 等 YAML 文件 |
| `data/` | 原始数据，包含 ASAP Set 8 的 TSV 训练集和样本文本文件 |
| `docs/` | 项目文档，包含架构说明、量规指南、研究笔记、Zen 原则等 |
| `artifacts/` | 运行产物输出目录，保存每次评估的 `feedback.json`、`hypotheses.json`、`run_trace.json`、`report.md` |
| `api/` | 后端 API 服务代码（供前端调用） |
| `frontend/` | 前端展示界面代码，用于可视化评分结果 |
| `.claude/` | Claude Code 本地配置，含 memory 文件（跨会话记忆） |
| `.git/` | Git 版本控制元数据 |
| `.idea/` | JetBrains IDE（PyCharm）项目配置 |
| `.vscode/` | VS Code 编辑器配置 |
| `.pytest_cache/` | pytest 缓存目录，存储测试运行缓存 |
| `__pycache__/` | Python 字节码缓存 |

---

## 文件

| 名称 | 作用 |
|------|------|
| `CLAUDE.md` | 项目宪法级说明文档，告知 Claude Code 项目结构、工作模式和不可逾越的边界 |
| `README.md` | 项目整体介绍，面向新用户的入门说明 |
| `PIPELINE_STAGES.md` | 流水线各阶段详细说明文档 |
| `pyproject.toml` | Python 项目元数据与依赖配置（PEP 517/518 标准） |
| `pytest.ini` | pytest 配置，定义测试路径、标记（markers）等 |
| `conftest.py` | pytest 全局 fixtures，供所有测试层共用 |
| `.env` | 本地环境变量（API Key、模型名等），不提交到 Git |
| `.env.example` | `.env` 的模板文件，示范所有需要配置的变量 |
| `.python-version` | 指定 Python 版本（供 pyenv 等工具读取） |
| `.coverage` | pytest-cov 生成的测试覆盖率数据文件 |
| `test_deepseek.py` | 根目录临时测试脚本，验证 DeepSeek API 连通性 |
| `test_bailian.py` | 根目录临时测试脚本，验证百炼（阿里云）API 连通性 |
| `.DS_Store` | macOS Finder 生成的元数据文件，无实际用途 |

---

## scripts/ 目录

存放独立可执行的命令行工具脚本，不属于 `src/` 核心逻辑。

| 文件 | 作用 |
|------|------|
| `eval.py` | **主评估入口**。统一支持单篇（`--essay-id`）和批量（`--limit` / `--essay-ids`）两种模式，自动判断并调用流水线，输出详细报告和人工评分对比 |
| `compute_qwk.py` | **批量 QWK 计算**。读取 `artifacts/eval/*/feedback.json` 与 TSV 人工分配对，计算三组指标：加权总分 QWK、MAS vs 人工 QWK（各维度）、Agent 间一致性 QWK |
| `run_baseline.py` | **基线运行脚本**。执行完整多智能体评估流水线，支持 `--provider mock`（确定性，无 LLM 调用）用于基线验证 |
| `accept_baseline.py` | **基线接受脚本**。对 manifest 中所有样本跑完整基线流水线，验证每个运行目录，并可选检查 golden snapshot，全部通过则退出码 0 |
| `compare_snapshot.py` | **快照对比工具**。将当前运行目录与 golden snapshot 对比，检测状态、节点序列、维度得分、裁决冲突数等维度是否退化 |
| `replay_run.py` | **回放工具**。从 golden snapshot 或运行产物读取配置（bundle_id、provider 等），以相同参数重新跑流水线，配合 `compare_snapshot.py` 做回归检测 |
| `validate_config.py` | **配置校验工具**。将 bundle YAML 经过完整 resolver/compiler 流程，报告版本闭包、schema 校验和 freeze hash |
| `validate_run.py` | **运行目录校验工具**。检查运行产物是否合法：必需文件存在、run_trace schema、所有节点状态为 success、feedback 结构完整等 |

---

## src/ 目录

核心源代码，按功能模块划分为 7 个子包。

### src/agents/ — Agent 工作者

流水线各阶段的执行单元，分为**真实 LLM 版**和**确定性 Mock 版**两套。

| 文件 | 作用 |
|------|------|
| `preprocess.py` | 预处理器。将 EvaluationRequest 规范化为 NormalizedDocument，做文本分句和 ID hash 生成，不调用 LLM |
| `config_resolver.py` | 配置解析器。调用 ConfigCompiler 将 bundle YAML 编译为冻结的 ResolvedArtifactBundle，不调用 LLM |
| `coverage.py` | 覆盖规划器。为每个量规维度生成 CoveragePlan，委托 rubric_core 做零硬编码的维度遍历 |
| `extractor.py` | 证据提取器（真实 LLM）。调用 Provider 从文本中提取 EvidenceSpan，输出有依据的评分证据 |
| `observer.py` | 观察构建器。将 EvidenceSpan 按 facet 分组，组装为 DimensionObservation，不调用 LLM |
| `scorer.py` | 评分器（真实 LLM）。调用 Provider 对每个维度生成 ScoreHypothesis，含 canonical_score |
| `consistency_checker.py` | 一致性检验器（真实版）。评估所有裁决触发器（score_distance + pattern_match/Cusp Rule） |
| `adjudicator.py` | 裁决器（真实版）。有冲突时优先取 rater_3 权威分，无冲突时直接产出 FinalDimensionDecision |
| `feedback.py` | 反馈组装器。统一输出格式，汇总各维度结果与 composite 总分，支持真实和 Mock 两种路径 |
| `prompt_builders.py` | Prompt 构建器。将类型化 Contract 对象映射为 Jinja2 模板上下文，渲染出各 Agent 所用 Prompt |
| `deterministic_extractor.py` | 确定性证据提取器（Mock）。通过 hash 生成固定 EvidenceSpan，不调用 LLM |
| `deterministic_scorer.py` | 确定性评分器（Mock）。通过 hash 映射到合法分值，不调用 LLM |
| `deterministic_consistency_checker.py` | 确定性一致性检验器（Mock）。仅评估首个 score_distance 触发器 |
| `deterministic_adjudicator.py` | 确定性裁决器（Mock）。按 hypothesis_id 字典序选最小值作为裁决结果 |

---

### src/contracts/ — 数据契约

Agent 间传递的不可变数据类型，是系统"零硬编码"原则的执行边界。

| 文件 | 作用 |
|------|------|
| `request_models.py` | 请求规范化契约：EvaluationRequest → NormalizedRequest → NormalizedDocument → CoveragePlan |
| `evidence.py` | 证据与观察契约：TextUnit / EvidenceSpan / DimensionObservation |
| `scoring.py` | 评分与裁决契约：ScoreHypothesis / ConflictRecord / AdjudicationRecord / FinalDimensionDecision |
| `score_representation.py` | 分数表示契约。严格区分 canonical_score（用于计算）与 display_annotation（仅展示） |
| `artifact_bundle.py` | 配置产物契约：ArtifactBundle / ResolvedArtifactBundle，运行时配置的唯一合法载体 |
| `trace.py` | 追踪与回放契约：NodeTrace / RunTrace / CheckpointRef，构成完整审计链 |

---

### src/config/ — 配置编译系统

将 YAML 配置文件编译为运行时可用的冻结配置对象。

| 文件 | 作用 |
|------|------|
| `loader.py` | 基础配置加载器，读取 YAML 文件为 ArtifactBundle dict |
| `schema.py` | Pydantic v2 schema 定义，校验所有 YAML 配置文件结构（Rubric、Adjudication 等） |
| `resolver.py` | 配置解析器，加载 bundle 中所有 artifact 引用，验证 schema 并计算 content hash |
| `compiler.py` | 配置编译器（单一入口），协调 loader → resolver → freeze，产出冻结的 ResolvedArtifactBundle |
| `freeze.py` | 内容 hash 工具，基于 SHA-256 计算单文件和 bundle 整体的确定性 hash，保障回放安全 |

---

### src/orchestrator/ — 状态机编排

管理流水线状态转换、路由决策和运行时追踪。

| 文件 | 作用 |
|------|------|
| `states.py` | 定义 PipelineState 枚举和合法转换矩阵 TRANSITIONS，以及终止状态集合 |
| `graph.py` | StateGraph，运行时状态机，强制执行合法转换，记录转换历史，提供 force_fail 接口 |
| `router.py` | Router，纯函数，读取 Contract 字段值（如 ConflictRecord.recommended_path）决定下一状态，不内联任何业务规则 |
| `checkpoints.py` | CheckpointManager，管理节点快照引用，追踪 re_extract / re_score 回退重试次数，强制最大重试限制 |
| `trace_store.py` | TraceStore，累积 NodeTrace 记录，run 结束后产出不可变的 RunTrace 契约对象 |

---

### src/pipeline/ — 流水线执行器

系统唯一的编排入口，驱动状态机调用各 Agent。

| 文件 | 作用 |
|------|------|
| `runner.py` | **PipelineRunner**（核心）。按状态机推进各 Agent，含全部修正记录，返回 (RunTrace, feedback) 元组 |
| `validators.py` | 流水线验证器，各阶段前后执行不变量检查，基于 Contract 字段，无硬编码维度名 |
| `export.py` | 流水线输出组装，将 FinalDimensionDecision[] 和可选 CompositeDecision 合并为标准输出 dict |

---

### src/policies/ — 策略层

从配置中读取规则，执行裁决判断、聚合计算、解释渲染和量规遍历。

| 文件 | 作用 |
|------|------|
| `rubric_core.py` | 量规核心策略。提供维度遍历、分值范围读取、描述符查找等工具函数，所有值来自 RubricSnapshot |
| `adjudication.py` | 裁决策略。评估 score_distance 和 pattern_match（Cusp Rule）两类触发器，产出 ConflictRecord |
| `aggregation.py` | 聚合策略。实现 average_per_trait_then_weighted_sum 等加权总分公式，产出 CompositeDecision |
| `explanation.py` | 解释策略。渲染各维度解释文本，校验 descriptor_ref → evidence_span → canonical_score 引用链 |

---

### src/providers/ — Provider 层

LLM 调用的传输抽象层，屏蔽底层 API 差异。

| 文件 | 作用 |
|------|------|
| `base.py` | 抽象接口定义：LLMRequest / LLMResponse / TokenUsage / ProviderCapability / BaseProvider / 错误体系 |
| `openai_compatible.py` | OpenAI 兼容 Provider，支持 OpenAI、DeepSeek、本地模型（LM Studio / Ollama）等 |
| `mock.py` | Mock Provider，通过 prompt hash 确定性生成响应，不发起任何网络请求，用于测试 |
| `guards.py` | GuardedProvider，装饰器 Provider，添加自动重试、超时保护，ParseError 不重试 |
| `logging_provider.py` | LoggingProvider，透明装饰器，打印每次 LLM 调用的 model / token / 耗时信息 |
| `factory.py` | Provider 工厂，根据 ProviderEntryConfig 和环境变量构造 BaseProvider 实例 |
| `registry.py` | Provider 注册表，维护 name → class 映射，解耦 Agent 与具体 Provider 实现 |
| `switch.py` | Provider 开关，`create_provider(name)` / `create_provider_from_env()` 工厂函数 |
| `prompt_loader.py` | Prompt 模板加载器，加载 Jinja2 YAML 模板并渲染，严格模式下未定义变量即报错 |
| `structured_output.py` | 结构化输出规范化，将 Provider 原始文本解析为 dict，可选 JSON schema 校验 |

---

### src/evaluation/ — 评估工具

纯计算模块，不调用 LLM，不读取配置。

| 文件 | 作用 |
|------|------|
| `qwk.py` | QWK 计算核心，`qwk(y_true, y_pred, min, max) -> float`，纯数值计算 |
| `consistency.py` | Agent 间一致性指标，计算多个 ScoreHypothesis 在同一维度的分歧程度 |
| `export.py` | QWK 导出工具，将 feedback.json / run_trace.json 转换为 QWK 计算所需的平坦结构 |
