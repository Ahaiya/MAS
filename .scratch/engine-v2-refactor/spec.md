# 评价引擎 v2 重构（一期：引擎内核）

Status: ready-for-agent

> 本 spec 是 `docs/REFACTOR_DESIGN.md` 经 grilling 逐项拍板后的落地依据。凡与原设计文档冲突处，**以本 spec 为准**（grilling 推翻了原文若干处基于错误前提的设计）。
> 决策全量副本见 memory `refactor-v2-decisions.md`。

---

## Problem Statement

现有评价引擎能跑通「一个量规 + 一个数据包 → 双评 + 仲裁 → 评分与反馈」，但作为一个要交付使用的系统，它有几处从数据上被证实的结构性缺陷：

1. **核心编排臃肿失控**：`pipeline/runner.py` 1334 行，把一条本质线性的流程包装成带回退重入的状态机，真正的路由逻辑不足 20 行，其余是状态流转与 debug 埋点样板（埋点占 40%+ 行数）。
2. **证据可解释性有静默漏洞**：让 LLM 自由复述原文、再用编辑距离往回猜位置。历史产物统计 8569 条证据中 **14.7% 定位失败（unmatched）**，却被静默降级为 GLOBAL 照常进入评分——教师无法核验、前端无法高亮，可解释性在此断裂。
3. **退化死代码**：`observer.py` 的"覆盖率审计"依赖的 facets 实际是 compiler 自动合成的 `[dimension_id]`（量规里根本没有 required_facets 字段），每次运行必然是 1/1 覆盖，125 行等价于 `bool(spans)`。
4. **分块路径与真实数据脱节**：`chunker.py` 502 行的"语义分块"阈值设为 4000 token，而真实样本中位数约 21700 token，语义分块路径在生产数据上几乎跑不到；实际走的是「机械硬切 + LLM 起标题」，标题对取证质量无实质贡献。
5. **双评的独立性存疑**：两个 Rater 共享同一份证据背靠背打分，历史数据显示 91.8% 结论一致——很可能是共享证据造成的伪独立，而非真实一致。

用户要的是：重构代码库、删除冗余、尽可能精简，同时补齐"独立双链路评价 + 可审计证据链"，使之成为一个可用的系统。

## Solution

把引擎重构为一条**线性函数链**，并落地**独立双链路评价流水线 v2**：

- **确定性单元切分**替代 LLM 分块：数据包按内容类型（散文/代码/表格/标题/图片）切成带**全局连续编号**的单元，零 LLM。
- **编号锚点**替代模糊匹配：模型引用证据时返回单元编号（`unit_ids: [12,13]`）而非复述原文，定位 100% 确定、越界即拒，彻底消除 14.7% 的定位失败。
- **独立双链路**：每个 Rater 独立完成「选段 → 取证 → 评分」完整链路（两趟取证真正实现"先全局后细节"），互不共享证据，独立性落在"看哪里"这个最关键的分歧点上。
- **升级仲裁**：分歧一律走 Rater3；Rater3 看双链完整证据 + 量规 + 原文，但**不看双方分数**（防锚定），输出格式与 Rater1/2 一致并强制引用证据编号，保证最终分永远可核验。
- **门面 API**：对外抽象收敛为「量规 + 数据包 → 评价」，即 `Engine.from_bundle(path).evaluate(package, dim)`。
- **大幅删除**：净删约 4500+ 行（chunker/quote_matcher/observer/debug/orchestrator/outer_loop/旧 runner），主编排 1334 行 → 250-350 行。

系统抽象保持轻量：引擎只认「量规 + 数据包」最小输入单元，不关心数据包来源；接入层（多源文件解析）、增值计算、人在回路留接口不实现（二/三期）。

## User Stories

1. 作为引擎调用方，我想用 `Engine.from_bundle(path).evaluate(package, dim)` 发起一次评价，以便不必手工装配十余个参数。
2. 作为引擎调用方，我想只传「量规 bundle + 数据包文件」两个最小输入，以便调用契约与系统的核心抽象一致。
3. 作为命令行用户，我想 `python scripts/cli.py eval <file> --dim a4` 评单个一级指标，以便调试特定维度。
4. 作为命令行用户，我想不传 `--dim` 时缺省评当前任务下的所有一级指标，以便一次得到一份完整评价。
5. 作为命令行用户，我想 CLI 参数只保留位置文件、`--bundle`、`--dim`、`--output-dir`，以便界面干净、没有失效开关。
6. 作为运维者，我想模型/参数只从 `configs/model_config.yaml` 读取、缺失即报错，以便杜绝静默降级到单评委却以为跑了双评。
7. 作为运维者，我想密钥值只放 `.env`、模型选择只放 yaml，以便职责清晰不冲突。
8. 作为运维者，我想 Rater3（仲裁）provider 缺失时直接报错，以便分歧场景不会无声地走错误兜底。
9. 作为数据处理者，我想数据包被切成带全局连续编号的单元，以便证据可以用编号精确定位。
10. 作为数据处理者，我想散文按句、代码块整块、表格按行、标题成单元、图片用其描述，以便每种内容都有合理的可引用最小单元。
11. 作为数据处理者，我想多文件共享同一编号空间且单元带 `source_file`，以便一个学生的多份材料在同一次评价中被统一引用。
12. 作为数据处理者，我想切分是零 LLM 的确定性过程，以便切分结果可复现、无成本。
13. 作为评价对象，我想短文档（未超上下文）不被切分而整篇进入取证，以便不引入无谓的切分误差。
14. 作为评价对象，我想只有超上下文时才按预算丢弃单元、且丢弃被显式记录，以便关键证据不会被无声丢掉。
15. 作为评委链，我想第一趟先看「单元号 + 每段前若干字节选」选出相关单元，以便模拟人类"先全局扫描"。
16. 作为评委链，我想第二趟只把选中单元全文喂入取证，以便"再细节精读"。
17. 作为评委链，我想取证与评分是两次独立 LLM 调用，以便证据先于分数生成、抵抗事后合理化。
18. 作为评委链，我想每个 Rater 独立决定"看哪些单元"，以便两条链在证据选择层面真正独立。
19. 作为仲裁者（Rater3），我想在分差>1 或多维度同向漂移时被触发，以便只在真正分歧时介入。
20. 作为仲裁者，我想看到双链完整证据 + 量规 + 原文但看不到双方分数，以便独立判断不被锚定。
21. 作为仲裁者，我想我的输出格式与 Rater1/2 相同并强制引用证据编号，以便被仲裁的分数同样可核验。
22. 作为评分聚合者，我想每个二级指标只有唯一最终分（一致值或 Rater3），一级指标按 auto_equal 等权，以便聚合逻辑无分支歧义。
23. 作为前端开发者，我想 `feedback.json` 里证据是 `unit_ids` 而非复述原文，以便靠编号 + `source_file` 精确高亮。
24. 作为前端开发者，我想 `feedback.json` 含一级指标分 + 雷达图数据（各二级指标分数数组），以便直接渲染可视化。
25. 作为教师审核者，我想每个二级指标分带 `source: consensus|adjudicated` 标记，以便识别哪些分经过仲裁、需重点复核。
26. 作为审计者，我想完整双链证据落在独立的 `rater_chains.json`，以便深挖"为什么给这个分"而不污染给学生看的精简反馈。
27. 作为前端/教师，我想切分后带编号的单元落在 `package.json`，以便把 `unit_ids` 解读回原文。
28. 作为系统运维者，我想 `run_trace.json` 只记成本与性能（token、耗时、被仲裁的维度），以便复盘开销而不与决策数据重复。
29. 作为性能敏感用户，我想同一样本下多个二级指标并发评价，以便一次完整评价不必串行等待数分钟。
30. 作为运维者，我想并发上限 `max_workers` 从 `model_config.yaml` 配置（默认 8），以便按 LLM 服务档位调限流、不触发 429。
31. 作为运维者，我想单个二级指标评价失败只标记该维度失败并记录、不拖垮整个样本，以便其余维度照常产出。
32. 作为开发者，我想产物按 `artifacts/{task}/{submission}/{dim}/` 三层组织，以便同一学号跨任务的评价不互相混淆。
33. 作为维护者，我想主编排从状态机瘦身为线性函数链（chunk 已删 → segment → rate → rate → reconcile →[adjudicate]→ feedback），以便流程一眼可读。
34. 作为维护者，我想 debug 埋点整体删除、trace 用收集器模式（阶段函数返回结果时附带 trace，runner 只收集），以便埋点不侵入业务逻辑。
35. 作为维护者，我想删除 observer/chunker/quote_matcher/orchestrator/outer_loop/debug 等冗余模块，以便代码库精简到工程规范。
36. 作为后续（二期）开发者，我想 `DataPackage` 契约已定型且流水线只认它，以便接入层落地时不改动上游流水线。
37. 作为测试者，我想通过注入 FakeProvider 在 `Engine.evaluate` 最高层跑完整评价，以便一个接缝覆盖整条流水线。

## Implementation Decisions

### 门面与入口
- 新增 `src/engine.py`（顶层门面）：`Engine.from_bundle(bundle_path)` 构造（编译 bundle、从 model_config 建 providers、加载 prompts），`engine.evaluate(package, dim=None)` 执行。`Engine.from_bundle` 支持注入 providers（测试用）。
- `pipeline/` 目录整体消失；`pipeline/runner.py`(1334) 与 `evaluation/runner.py` 被 `engine.py` 取代。
- CLI 收敛为单文件 `scripts/cli.py`（typer），调用形式 `python scripts/cli.py eval <file> --dim a4`，文件头自注入 `sys.path`（不用 `-m`、不装 console_scripts）。删 `scripts/__main__.py`、`scripts/mas.py`。`config validate` 命令并入 `cli.py`。`server.py` 独立保留（三期）。
- CLI 参数：位置 `INPUT_FILE` + `--bundle`（默认 `configs/bundle.yaml`）+ `--dim`（缺省评所有一级指标）+ `--output-dir`。删 `--input/-i`、`--verbose`、`--debug-bundle`、`--model-config`（固定读 `configs/model_config.yaml`）。不加 `--batch`（批量归前端）。

### 数据契约（`src/contracts/`）
- 新增 `DataPackage` 与 `Unit`。来自 grilling 的定型结构：
  ```python
  @dataclass(frozen=True)
  class Unit:
      id: int                    # 全局连续编号
      kind: str                  # prose | code | table_row | heading | image
      text: str
      source_file: str
      char_range: tuple[int, int]
      speaker: str | None        # 对话轮次归属，无则 None
  
  @dataclass(frozen=True)
  class DataPackage:
      package_id: str
      units: list[Unit]
      metadata: dict             # 前端透传（学生ID/任务ID/轮次/时间戳），引擎不解释
  ```
- `scoring.py` 调整：新增 `RaterChainResult`（单 Rater 完整链：选段 + 证据 + 分数 + rationale 绑定同一结构）、`DimensionScore`（含 `dimension_id/score/supporting_unit_ids/rationale/confidence`）。证据引用改为 `unit_ids` 编号。
- `trace.py` 轻量化。
- **删除契约**：`CoveragePlan`、`DimensionObservation`、`FacetFinding`、`ObservationConfidence`。
- 一期用 `read_text_file()` 直接从 .md/.txt 构造 `DataPackage`；不建 `ingest/` 实现。

### 单元切分（`src/segment.py`，单文件 ~150 行）
- 内容类型感知的确定性切分，替代 `chunker.py`(502) + `quote_matcher.py`(198)：
  - 散文段落 → 按句切（。？！；+ 换行）
  - 代码块（``` 围栏）→ 整块 1 单元
  - 表格（`|`）→ 每行 1 单元
  - 标题（`#`）→ 1 单元（携带层级作定位上下文）
  - 图片（`![alt](src)`）→ 1 单元，text 用解析 API 返回的 caption/描述
  - 对话轮次 → 单元携带 `speaker`（复用 `utils/dialogue_sources.py`）
- 编号全局连续、跨多文件共享空间、单元带 `source_file`。
- 输入前提：真实数据包是解析 API 输出的规范 Markdown（历史训练数据格式不可靠，仅用于确认会出现哪些内容种类）。
- 短文档（未超上下文安全余量）不切分、整篇进取证；`token_threshold` 语义改为"上下文安全余量"（如 48000），不再是现值 4000。

### 评价链（`src/agents/`）
- `rater.py`（新）：单 Rater 完整链 `select → extract → score`，合并原 extractor + observer + scorer。
  - `select`：看「单元号 + 每段前若干字节选」，选出该二级指标的相关单元号（每 Rater 独立）。
  - `extract`：选中单元全文 → 证据（返回 `unit_ids`）。
  - `score`：证据 + 锚点 → `DimensionScore`。
  - 一个 Rater 三趟共用同一个 provider（`raters.rater_N`），不拆分。
- `adjudicator.py`（新）：Rater3 仲裁。输入双链证据 + 量规 + 原文，不含双方分数；输出格式同 Rater1/2、强制引用证据编号。
- `feedback.py`：保留，更名对齐 prompt。
- `prompt_builders.py`：保留调整。
- **删除**：`extractor.py`、`observer.py`、`scorer.py`（合并入 rater.py）。

### 策略（`src/policies/`，纯计算无 LLM）
- 仲裁触发规则不变：任一二级指标分差>1，或 ≥2 个二级指标同向相邻漂移。删除 `average`/`highest` 无 LLM 兜底——分歧一律触发 Rater3。
- 聚合单一路径：删 with/without variant；每个二级指标定出唯一 `final_score`（一致值或 Rater3），一级指标 = auto_equal 等权平均。

### 编排与埋点
- 主流程线性函数链：`segment → rate(r1) → rate(r2) → reconcile →[adjudicate]→ feedback`。错误直接抛出/单阶段处理，无状态机回退重入。
- 删除 `orchestrator/`（states/checkpoints/trace_store 回放）、`debug/bundle.py`。
- trace 埋点用**收集器模式**：每个阶段函数返回结果时附带 `StageTrace`，runner 只收集进列表，不手动插桩。trace 只记运行级（run_id/bundle_ref/dim/total_tokens/total_ms/adjudicated_dims）+ 阶段级（stage/rater/llm_calls/tokens/ms）。删除 replay_metadata/input_ref/output_ref 回放机制。
- log 保持现状：`LoggingProvider` 逐调用打印够用，仅删除其中对 `debug_writer` 的调用。

### 并发与错误处理
- 二级指标级并发：`ThreadPoolExecutor`（provider IO 密集，GIL 不碍事）。`max_workers` 从 `model_config.yaml` 的 `concurrency` 段读取，默认 8。
- 失败隔离：单个二级指标失败仅标记该 dim 失败并记录，不崩整份提交，其余照常产出。
- provider 缺失（尤其 rater_3）直接报错，不静默降级。

### 配置（`configs/`）
- bundle 移到 `configs/bundle.yaml`，`bundle_id` 改 `default`。
- prompts 重列：删 `chunking.yaml`；新增 `select.yaml`、`adjudication.yaml`；`evidence_extraction.yaml → extraction.yaml`（按 unit_ids）；`explanation.yaml → feedback.yaml`。
- 删 chunking policy；aggregation policy 删 with/without variant；adjudication policy 保留。
- `model_config.yaml`：`default` + `raters.{rater_1,rater_2,rater_3}` + `concurrency.max_workers`；一期不加 `parser` 段。

### 产物
- 三层 `artifacts/{task}/{submission}/{dim}/`（task 不能省——同一学号会跨 task 出现）。
- 每 dim 目录出 3 文件：`feedback.json`（精简，给前端/学生：一级指标分 + 雷达数据 + 各二级指标 final_score/source/证据 unit_ids/文字反馈）、`rater_chains.json`（完整双链 + 仲裁记录，审计用）、`run_trace.json`（成本/性能）。
- `package.json`（带编号单元的数据包）由 parse 落在 `packages/{task}/{submission}/`，不进 artifacts/。

### 术语约定
- 一个 `rubric.yaml` = 一个**一级指标**（如 A4），内含多个**二级指标**（A4-1/2/3，代码里叫 `dimension`）。
- `--dim a4` 选一个一级指标。`task` = 评价场景/量规集（maker_hackathon 等），其 `active_task_id` 在 bundle 里可切。
- `submission`（提交）= 一名学生交上来的一批材料（学号命名）；一期引擎不把学号当学生实体（跨轮次追踪是三期）。

## Testing Decisions

- **好测试的判据**：只断言外部可观察行为，不断言实现细节。对引擎，外部行为 = 给定量规 + 数据包 + 脚本化 LLM 响应，产出的 feedback / rater_chains / trace 的结构与值。不断言中间调用了哪个内部函数。
- **主接缝 = `BaseProvider`**：新建 `FakeProvider`（当前代码库无任何 fake/stub，`grep` 零命中），按调用顺序返回预设 `LLMResponse`。这是唯一新增接缝，建在最高点（LLM 边界）。
- **在最高层测**：通过 `Engine.from_bundle(..., providers=<fakes>)` 注入假 provider，调 `engine.evaluate()` 跑完整评价，一个接缝覆盖 `select → extract → score → reconcile → adjudicate → feedback` 全链。
  - 覆盖场景：两链一致（不触发仲裁，source=consensus）；两链分歧（触发 Rater3，source=adjudicated）；单维度失败隔离（其余维度照常产出）；缺 rater_3 provider 报错；证据 `unit_ids` 正确回指原文。
- **纯函数直测**（非接缝，直接调用输入→输出）：
  - `segment()`：各 kind 切分正确、编号全局连续、跨文件共享编号、超预算丢弃被记录、短文档不切分。
  - 仲裁触发判断：分差>1 触发、同向漂移≥2 触发、一致不触发。
  - 聚合：auto_equal 等权、单一路径无分支。
  - 编号 → 字符偏移映射。
- **prior art**：`tests/unit/agents/test_explanation_prompt_scale.py` 展示了本仓库用真实 `RubricSnapshot` / `PromptLoader` 构造输入、断言 prompt 输出的纯函数测试风格，可作为 segment/policy 纯函数测试的范式参照。
- 遵循用户全局规则：TDD（先写测试红 → 实现绿 → 重构），目标覆盖 80%+。

## Out of Scope

- **接入层（ingest/）实现**：多源文件（PDF/Word/Excel/PPT/压缩包）解析、解析 API 适配器、provider 注册工厂。一期只定 `DataPackage` 契约，用 `read_text_file()` 读 .md/.txt。（二期）
- **增值计算（valueadd/）**：跨轮次能力增益，依赖学生一级实体模型。（三期）
- **人在回路（humanloop/）**：教师审核修正回写、CorrectionAgent 机制。一期 `feedback.json` 的 `source` 字段是唯一相关钩子。（三期）
- **学生一级实体 / 时间轴**：引擎一期不把学号当学生实体，不做跨提交追踪。
- **批量入口**：一次上传多学生由前端拆分后逐包调引擎。
- **VLM 图片通道**：图片一期用解析 API 返回的描述，不自建多模态 provider。
- **PDF 转换垃圾清洗**：一期原样切成 table_row 单元，让模型自行跳过；清洗规则等真跑出问题再针对性加。
- **前端可视化契约细节**：雷达图数据结构需与前端负责人（李领康）对齐，本 spec 只保证 feedback.json 含各二级指标分数数组。

## Further Notes

- **grilling 挖出的、与原设计文档冲突的事实**（已在本 spec 修正）：
  - observer 覆盖率审计是退化死代码（facets 恒为 `[dim_id]`）。
  - chunker "语义分块"在真实样本上几乎跑不到（阈值 4000 vs 中位数 21700 token）。
  - 证据定位有 14.7% 静默失败却照常进评分——最大可解释性漏洞。
  - "先全局后细节"原设计意图从未真正实现（现状只有一趟取证）。
- **成本变化**（已确认接受）：每个二级指标从现状 3 次 LLM（1 取证 + 2 评分）→ 6 次（2 选段 + 2 取证 + 2 评分），一次完整 maker 评价（4 个一级指标 ≈ 12 个二级指标）≈ 76 次 LLM。并发抵消串行延迟。
- **数据问题**（非本 spec 决策，供处理）：`data/training/AI_coding/` 与 `data/training/trae_coding/` 行数/代码块/标题数完全一致，疑似同一批数据的重复副本。
- **建议实施起点**：从 `segment.py` + `DataPackage` 契约开始（数据流最上游、无 LLM 依赖、可独立 TDD），逐层向下。
- 决策全量副本见 memory `refactor-v2-decisions.md`（23 条）。
