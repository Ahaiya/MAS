## 1. Core Objective & Context

MVP 成功标准：

- 给定学生非结构化文本输入，系统能在 `mock` 模式下完整跑通 MAS baseline 主链路：`request -> config resolve -> preprocess -> coverage/extract -> observe -> score -> consistency/adjudicate -> feedback -> validate`。
- 系统输出结构化维度级评价结果，至少包含 `FinalDimensionDecision[]`、证据绑定反馈、可选 `CompositeDecision`、完整 trace 与 replay metadata。
- 系统在 `mock` 模式下具备确定性、可重复、可审计、可回退的运行结果。
- 系统保留清晰的真实 LLM 接入扩展点，但在 `mock` baseline 稳定前不接入真实 provider。
- 系统输出可扩展到后续 QWK、人机一致性、inter-agent consistency、回放与版本对比评测。

必须先阅读的文件清单：

- `docs/Zen.md`
- `docs/research.md`
- `docs/architecture.md`
- `docs/Rubric_Guidelines.md`
- `docs/Adjudication_Rules.md`
- `docs/Example.md`
- `data/training_set_8.tsv`
- 后续生成的 `configs/` 配置工件与 `data/samples/` 学生样例

执行前提：

- `Zen.md` 为最高优先级；若与 `research.md`、`architecture.md`、任何实现便利冲突，一律以 `Zen.md` 为准。
- baseline 先于优化；先把可运行、可验证、可回退的主链路跑通，再做 prompt、策略或效果迭代。
- 任一阶段测试不过，不得进入下一阶段。
- `Example.md` 只定义解释输出形态，不得反向当作 rubric 或 adjudication 规则来源。

## 2. Execution Rules

1. 严格按 Phase 顺序执行；未满足当前 Phase 退出条件，不得启动后续 Phase。
2. 每完成一个任务，立即运行该任务对应的验收命令；不要累计到 Phase 末尾一起验证。
3. 每完成一个 Phase，必须运行该阶段的集成验证与回归验证。
4. `mock` pipeline 未稳定前，禁止接入真实 LLM provider。
5. 接入真实 provider 后，`mock` baseline、`mock` fixtures、`mock` tests 必须继续通过且保持确定性。
6. 任一实现若开始依赖硬编码的 trait 名称、分档范围、composite 公式、adjudication 阈值、展示标记或 prompt 文本，立即回退当前 Phase。
7. 任一实现若开始绕过 `src/contracts/` 私造隐式字段，或绕过 `src/orchestrator/` 做自由串聊式 agent 编排，立即回退当前 Phase。
8. 连续测试失败时，不允许在当前实现上补丁式修修补补；必须回退到当前 Phase 起点，按 contract 与测试重新实现。
9. 运行时只读取 `configs/` 工件，不直接把 `Rubric_Guidelines.md`、`Adjudication_Rules.md`、`Example.md` 当作运行时配置源。
10. baseline 主路径之外的 UI、监控面板、复杂缓存、多模型路由优化、数据库持久化、异步队列不进入本计划主链路。

## 3. Phase 0: Repository & Environment Setup

- [x] 初始化目录结构与包占位文件  
  输入: 仓库根目录、`Zen.md`、`research.md`、`architecture.md`  
  输出: `configs/`、`data/samples/`、`scripts/`、`src/contracts/`、`src/config/`、`src/orchestrator/`、`src/agents/`、`src/policies/`、`src/providers/`、`src/pipeline/`、`src/evaluation/`、`tests/unit/`、`tests/integration/`、`tests/e2e/`，以及必要的 `__init__.py`  
  验收: `test -d configs && test -d src/contracts && test -d src/orchestrator && test -d tests/e2e`  
  依赖: 无

- [x] 初始化 Python 项目元数据与开发依赖模板  
  输入: 目录骨架、测试与脚本运行需求  
  输出: `pyproject.toml`、`pytest.ini`、可选 `.python-version`  
  验收: `python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"`  
  依赖: 目录结构已创建

- [x] 创建 `.env.example` 并显式隔离 provider 配置  
  输入: provider 抽象需求、`Zen.md` 的 LLM-agnostic 约束  
  输出: `.env.example`，至少包含 `LLM_PROVIDER`、`LLM_MODEL`、`LLM_API_KEY`、`LLM_API_BASE`、`LLM_TIMEOUT_SECONDS`  
  验收: `test -f .env.example`  
  依赖: Python 项目元数据已创建

- [x] 创建配置加载入口与空实现 CLI 骨架  
  输入: `configs/` 目录、未来 bundle 加载需求  
  输出: `src/config/loader.py`、`src/config/__init__.py`、`scripts/validate_config.py` 占位实现  
  验收: `python scripts/validate_config.py --help`  
  依赖: 目录结构、项目元数据

- [x] 初始化测试目录与 smoke test  
  输入: 包结构、CLI 骨架  
  输出: `tests/unit/test_smoke.py`，至少覆盖包导入、脚本入口与基础目录存在性  
  验收: `python -m pytest tests/unit/test_smoke.py`  
  依赖: 项目元数据、配置加载入口

- [x] 创建基础运行脚本入口  
  输入: baseline 主链路目标、CLI 参数需求  
  输出: `scripts/run_baseline.py`，支持 `--bundle`、`--provider`、`--input-file`、`--output-dir` 参数；`scripts/extract_sample.py`，支持从 TSV 提取样例  
  验收: `python scripts/run_baseline.py --help && python scripts/extract_sample.py --help`  
  依赖: 项目元数据、测试骨架

- [x] 从 `data/training_set_8.tsv` 提取 baseline 样例与 manifest  
  输入: `data/training_set_8.tsv`、样例 essay id `20716` 与 `20717`  
  输出: `data/samples/sample_20716.txt`、`data/samples/sample_20717.txt`、`data/samples/baseline_manifest.yaml`  
  验收: `python scripts/extract_sample.py --essay-id 20716 --source data/training_set_8.tsv --output data/samples/sample_20716.txt && python scripts/extract_sample.py --essay-id 20717 --source data/training_set_8.tsv --output data/samples/sample_20717.txt`  
  依赖: 脚本入口已创建

阶段退出条件：目录结构、CLI 骨架、样例提取脚本、smoke test 全部通过；仓库已具备后续 contract 与 pipeline 开发所需的最小执行环境。  
失败回退：回退到仅保留目录结构、`pyproject.toml`、`.env.example`、空脚本入口与 smoke test 通过的状态。

## 4. Phase 1: Constitutional Contracts & Config Compiler

- [x] 将规则源文件转写为运行时配置工件  
  输入: `Rubric_Guidelines.md`、`Adjudication_Rules.md`、`Example.md`  
  输出: `configs/rubrics/asap_set8_baseline.yaml`、`configs/policies/adjudication/asap_set8_default.yaml`、`configs/policies/aggregation/asap_set8_composite.yaml`、`configs/policies/explanation/evidence_grounded_v1.yaml`、`configs/prompts/` 占位模板、`configs/bundles/asap_set8_baseline.bundle.yaml`  
  验收: `test -f configs/rubrics/asap_set8_baseline.yaml && test -f configs/policies/adjudication/asap_set8_default.yaml && test -f configs/bundles/asap_set8_baseline.bundle.yaml`  
  依赖: Phase 0 完成

- [x] 定义 `ArtifactBundle` 与 `ResolvedArtifactBundle` schema  
  输入: `Zen.md`、`research.md`、`architecture.md`、配置工件  
  输出: `src/contracts/artifact_bundle.py`，至少覆盖 bundle id/version、rubric/adjudication/aggregation/explanation/prompt refs、freeze hash、source refs  
  验收: `python -m pytest tests/unit/contracts/test_artifact_bundle.py`  
  依赖: 配置工件已创建

- [x] 定义 Rubric / Policy / Explanation / Prompt 配置 schema  
  输入: `Rubric_Guidelines.md`、`Adjudication_Rules.md`、`Example.md`  
  输出: `src/config/schema.py`，覆盖 Rubric Core、Adjudication Policy、Aggregation Policy、Explanation Policy、Prompt Template schema  
  验收: `python -m pytest tests/unit/config/test_schema_validation.py`  
  依赖: `ArtifactBundle` schema

- [x] 实现配置编译、解析、冻结与版本闭包校验  
  输入: `configs/` 工件、配置 schema  
  输出: `src/config/compiler.py`、`src/config/resolver.py`、`src/config/freeze.py`、`scripts/validate_config.py`  
  验收: `python -m pytest tests/unit/config/test_config_compiler.py && python scripts/validate_config.py --bundle configs/bundles/asap_set8_baseline.bundle.yaml`  
  依赖: 配置 schema 已定义

- [x] 显式定义 canonical score 与 display annotation 分离 contract  
  输入: `Rubric_Guidelines.md` 的整数评分、`Example.md` 的 `4-` / `3-` 展示标记  
  输出: `src/contracts/score_representation.py`，至少包含 `canonical_score`、`display_score`、`display_annotation`、`scale_ref`  
  验收: `python -m pytest tests/unit/contracts/test_score_representation.py`  
  依赖: 配置 schema 已定义

- [x] 编写零硬编码防线测试  
  输入: 主配置工件、变体配置夹具需求  
  输出: `tests/fixtures/configs/alt_bundle.yaml`、`tests/unit/config/test_no_business_facts_in_code.py`，验证维度数、trait 名称、分档范围、composite 公式、display annotation 均来自配置  
  验收: `python -m pytest tests/unit/config/test_no_business_facts_in_code.py`  
  依赖: 配置编译器、score 表示 contract

阶段退出条件：`ArtifactBundle`、配置 schema、配置编译器、冻结逻辑、score 表示 contract 与零硬编码测试全部通过；运行时已不依赖 Markdown 规则源。  
失败回退：回退到仅保留 `configs/` 工件与通过的 schema/编译器测试状态，删除未通过的编译与导出实现。

## 5. Phase 2: Intermediate Data Contracts

- [ ] 定义请求归一化与文本切分 contract  
  输入: `architecture.md` 的 orchestrator-worker 流程、样例文本  
  输出: `src/contracts/request_models.py`，至少包含 `EvaluationRequest`、`NormalizedRequest`、`NormalizedDocument`、`TextUnit`、`CoveragePlan`  
  验收: `python -m pytest tests/unit/contracts/test_request_models.py`  
  依赖: Phase 1 完成

- [ ] 定义证据与观察 contract  
  输入: `research.md`、`architecture.md`、Rubric Core 配置  
  输出: `src/contracts/evidence.py`，至少包含 `EvidenceSpan`、`DimensionObservation` 及 facet-level evidence refs  
  验收: `python -m pytest tests/unit/contracts/test_evidence_and_observation.py`  
  依赖: 请求归一化 contract

- [ ] 定义评分与冲突 contract  
  输入: Rubric Core、Adjudication Policy、Aggregation Policy  
  输出: `src/contracts/scoring.py`，至少包含 `ScoreHypothesis`、`ConflictRecord`、`AdjudicationRecord`、`FinalDimensionDecision`、可选 `CompositeDecision`  
  验收: `python -m pytest tests/unit/contracts/test_scoring_and_adjudication.py`  
  依赖: 证据与观察 contract、score 表示 contract

- [ ] 定义 trace / replay metadata contract  
  输入: `Zen.md` 的审计与回放要求、`architecture.md` 的 checkpoint 需求  
  输出: `src/contracts/trace.py`，至少包含 `RunTrace`、`NodeTrace`、`CheckpointRef`、bundle version、node input/output refs、fallback history  
  验收: `python -m pytest tests/unit/contracts/test_trace_models.py`  
  依赖: 请求、证据、评分 contract

- [ ] 为全部 contract 增加序列化、反序列化与禁用隐式字段测试  
  输入: 所有 contract 模型  
  输出: `tests/unit/contracts/test_roundtrip_serialization.py`、`tests/unit/contracts/test_no_extra_fields.py`  
  验收: `python -m pytest tests/unit/contracts/test_roundtrip_serialization.py tests/unit/contracts/test_no_extra_fields.py`  
  依赖: 全部 contract 已定义

阶段退出条件：所有关键 contract 均有 schema-level unit tests；所有对象可稳定序列化/反序列化；后续 Phase 只能依赖这些 contract。  
失败回退：回退到仅保留通过测试的 contract 文件，删除任何绕过 contract 的临时字段与私有数据形状。

## 6. Phase 3: Mocked Orchestrator-StateGraph Baseline

- [ ] 定义状态机与节点路由骨架  
  输入: `architecture.md` 的 Mermaid 运行流、Phase 2 contract  
  输出: `src/orchestrator/states.py`、`src/orchestrator/graph.py`、`src/orchestrator/router.py`，至少覆盖 `CONFIG_RESOLVED -> PREPROCESSED -> COVERAGE_PLANNED -> EVIDENCE_EXTRACTED -> OBSERVATION_BUILT -> SCORED -> CONSISTENCY_CHECKED -> ADJUDICATED | RE_EXTRACT | RE_SCORE | HUMAN_REVIEW -> FEEDBACK_RENDERED -> VALIDATED`  
  验收: `python -m pytest tests/unit/orchestrator/test_state_graph.py`  
  依赖: Phase 2 完成

- [ ] 实现 checkpoint、fallback 与 revert hooks  
  输入: 状态机骨架、trace contract  
  输出: `src/orchestrator/checkpoints.py`、`src/orchestrator/trace_store.py`，支持节点级 snapshot、重跑入口与失败回退标记  
  验收: `python -m pytest tests/unit/orchestrator/test_checkpoints.py`  
  依赖: 状态机骨架、trace contract

- [ ] 实现 deterministic mock workers  
  输入: Phase 1 配置、Phase 2 contract、状态机节点定义  
  输出: `src/agents/mock_config_resolver.py`、`src/agents/mock_preprocess.py`、`src/agents/mock_coverage.py`、`src/agents/mock_extractor.py`、`src/agents/mock_observer.py`、`src/agents/mock_scorer.py`、`src/agents/mock_consistency_checker.py`、`src/agents/mock_adjudicator.py`、`src/agents/mock_feedback.py`  
  验收: `python -m pytest tests/unit/agents/test_mock_workers.py`  
  依赖: 状态机骨架、配置编译器、所有 contract

- [ ] 实现 pipeline runner 与 CLI 主入口  
  输入: 状态机骨架、mock workers、样例 manifest  
  输出: `src/pipeline/runner.py`、`src/pipeline/validators.py`、`scripts/run_baseline.py` 的可运行 mock 模式  
  验收: `python -m pytest tests/integration/test_mock_pipeline.py`  
  依赖: mock workers、checkpoint hooks

- [ ] 覆盖 normal path 与 fallback path  
  输入: `data/samples/sample_20716.txt`、故意制造 coverage 缺口/评分冲突的测试夹具  
  输出: `tests/e2e/test_mock_baseline_normal_path.py`、`tests/integration/test_mock_fallback_paths.py`  
  验收: `python -m pytest tests/e2e/test_mock_baseline_normal_path.py tests/integration/test_mock_fallback_paths.py`  
  依赖: pipeline runner

阶段退出条件：`mock` 模式下主链路可运行，至少覆盖 normal path、`RE_EXTRACT`、`RE_SCORE`、`VALIDATED` 终态；尚未接入真实 provider。  
失败回退：回退到仅保留状态机骨架、checkpoint hooks 与通过测试的 deterministic mock workers 状态。

## 7. Phase 4: Policy-Aware MAS Wiring

- [ ] 让 Coverage / Observe / Score 流程按 Rubric Core 配置驱动遍历  
  输入: `ResolvedArtifactBundle`、Rubric Core 配置、Phase 3 mock skeleton  
  输出: `src/policies/rubric_core.py`、对 `src/agents/` 的配置驱动改造，禁止在流程中写死当前六维与 1-6 分档  
  验收: `python -m pytest tests/unit/policies/test_rubric_core_traversal.py`  
  依赖: Phase 3 完成

- [ ] 实现 adjudication trigger evaluation  
  输入: `configs/policies/adjudication/asap_set8_default.yaml`、`ScoreHypothesis[]`、`ConflictRecord` contract  
  输出: `src/policies/adjudication.py`、`src/agents/consistency_checker.py`，覆盖 non-adjacent rule 与 cusp rule 的配置化执行  
  验收: `python -m pytest tests/unit/policies/test_adjudication_triggers.py`  
  依赖: Rubric Core 遍历逻辑、评分 contract

- [ ] 实现 aggregation policy 与可选 `CompositeDecision`  
  输入: Aggregation Policy 配置、`FinalDimensionDecision[]`、`AdjudicationRecord[]`  
  输出: `src/policies/aggregation.py`、`src/pipeline/export.py`，显式区分 trait-level 输出与 composite 输出  
  验收: `python -m pytest tests/unit/policies/test_aggregation_policy.py`  
  依赖: adjudication trigger evaluation

- [ ] 实现 explanation policy enforcement 与 citation renderer  
  输入: Explanation Policy 配置、`EvidenceSpan[]`、`FinalDimensionDecision[]`、`Example.md` 的表现形态约束  
  输出: `src/policies/explanation.py`、`src/agents/feedback.py`，强制 descriptor ref、evidence id、score 引用链闭合  
  验收: `python -m pytest tests/unit/policies/test_explanation_policy.py`  
  依赖: aggregation policy、证据与最终决策 contract

- [ ] 导出 canonical vs display 字段并添加 policy-aware 集成测试  
  输入: score 表示 contract、全部 policy 实现、alt config fixture  
  输出: `tests/integration/test_policy_aware_pipeline.py`、`tests/integration/test_config_variants.py`  
  验收: `python -m pytest tests/integration/test_policy_aware_pipeline.py tests/integration/test_config_variants.py`  
  依赖: Rubric / Adjudication / Aggregation / Explanation policy 均已实现

阶段退出条件：系统逻辑已由 policy/config 驱动；变更 policy 时优先改 `configs/` 与编译器，而不是改 agent 主逻辑；所有集成测试通过。  
失败回退：回退到 Phase 3 的 mock skeleton 与通过测试的 contract，不保留破坏配置边界的条件分支实现。

## 8. Phase 5: Real Provider Adapter & Prompt Wiring

- [ ] 定义 provider interface、adapter registry 与 capability 边界  
  输入: `Zen.md` 的 LLM-agnostic 约束、Phase 4 policy-aware pipeline  
  输出: `src/providers/base.py`、`src/providers/registry.py`，明确 provider 只负责调用能力、结构化输出和错误处理，不承载 rubric/adjudication 语义  
  验收: `python -m pytest tests/unit/providers/test_provider_interface.py`  
  依赖: Phase 4 完成

- [ ] 实现 `MockProvider` 与至少一个真实 provider adapter  
  输入: provider interface、`.env.example`、prompt 与结构化输出需求  
  输出: `src/providers/mock.py`、`src/providers/openai_compatible.py` 或等价真实 adapter、provider switch 逻辑  
  验收: `python -m pytest tests/integration/test_provider_switch.py`  
  依赖: provider interface

- [ ] 实现 prompt template loading 与 node prompt wiring  
  输入: `configs/prompts/`、各 worker 所需输入 contract  
  输出: `src/providers/prompt_loader.py`、`src/agents/prompt_builders.py`、Jinja2 风格 prompt 模板文件  
  验收: `python -m pytest tests/unit/providers/test_prompt_loading.py`  
  依赖: provider interface、配置编译器

- [ ] 实现 structured output normalization、retry、timeout 与 parse-failure guardrails  
  输入: provider 返回体、Phase 2 contract schema  
  输出: `src/providers/structured_output.py`、`src/providers/guards.py`  
  验收: `python -m pytest tests/unit/providers/test_structured_output_normalization.py`  
  依赖: 真实 provider adapter、prompt wiring

- [ ] 打通至少一条真实 provider smoke path  
  输入: 有效的 provider 凭据、`data/samples/sample_20716.txt`、baseline bundle  
  输出: `tests/e2e/test_real_provider_smoke.py`、真实调用产生的运行工件  
  验收: `python -m pytest tests/e2e/test_real_provider_smoke.py -m real && python scripts/run_baseline.py --bundle configs/bundles/asap_set8_baseline.bundle.yaml --provider real --input-file data/samples/sample_20716.txt --output-dir artifacts/provider_smoke/sample_20716_real`  
  依赖: structured output guardrails

- [ ] 证明真实 provider 接入没有破坏 `mock` baseline  
  输入: 全量 `mock` tests、provider switch 逻辑  
  输出: 保持不变的 `mock` fixtures 与 `mock` snapshots  
  验收: `python -m pytest tests/unit tests/integration tests/e2e/test_mock_baseline_normal_path.py tests/integration/test_mock_fallback_paths.py -q`  
  依赖: 真实 provider smoke path

阶段退出条件：真实 provider 至少成功跑通一次 smoke path；全部 `mock` tests 继续通过；provider 层与 policy 层边界清晰。  
失败回退：撤销真实 provider 相关改动，回退到仅保留 `MockProvider` 与通过测试的 provider interface 状态。

## 9. Phase 6: End-to-End Baseline Validation

- [ ] 用普通路径样例完整跑通 baseline  
  输入: `data/samples/sample_20716.txt`、baseline bundle、`mock` provider  
  输出: `artifacts/baseline/20716/mock/` 下的结构化结果、trace、intermediate states、feedback  
  验收: `python scripts/run_baseline.py --bundle configs/bundles/asap_set8_baseline.bundle.yaml --provider mock --input-file data/samples/sample_20716.txt --output-dir artifacts/baseline/20716/mock`  
  依赖: Phase 5 完成

- [ ] 用冲突样例触发 adjudication 路径  
  输入: `data/samples/sample_20717.txt`、baseline bundle、`mock` provider  
  输出: `artifacts/baseline/20717/mock/` 下含 `ConflictRecord`、`AdjudicationRecord` 的结构化结果  
  验收: `python scripts/run_baseline.py --bundle configs/bundles/asap_set8_baseline.bundle.yaml --provider mock --input-file data/samples/sample_20717.txt --output-dir artifacts/baseline/20717/mock`  
  依赖: 普通路径样例已通过

- [ ] 校验输出 schema、trace closure 与 terminal validation  
  输入: Phase 6 运行产物、全部 contract schema  
  输出: `scripts/validate_run.py`、验证通过的 run 报告  
  验收: `python scripts/validate_run.py --run-dir artifacts/baseline/20716/mock && python scripts/validate_run.py --run-dir artifacts/baseline/20717/mock --require-adjudication`  
  依赖: 两条 baseline 运行产物

- [ ] 导出 baseline golden snapshots  
  输入: 已验证运行产物  
  输出: `tests/golden/sample_20716.mock.json`、`tests/golden/sample_20717.mock.json`、`tests/e2e/test_baseline_snapshots.py`  
  验收: `python -m pytest tests/e2e/test_baseline_snapshots.py`  
  依赖: 运行产物校验通过

- [ ] 汇总单一 baseline 验收入口  
  输入: baseline manifest、run validator、snapshot tests  
  输出: `scripts/accept_baseline.py`  
  验收: `python scripts/accept_baseline.py --bundle configs/bundles/asap_set8_baseline.bundle.yaml --manifest data/samples/baseline_manifest.yaml --provider mock`  
  依赖: golden snapshots、run validator

阶段退出条件：至少一条 normal path 与一条 adjudication path 在 `mock` 模式下完整通过并生成 golden snapshots；存在单一 baseline 验收命令。  
失败回退：回退到 Phase 5 的 provider 与 pipeline 边界，删除不稳定的快照与未通过的导出实现。

## 10. Phase 7: Evaluation Harness & Iteration Guardrails

- [ ] 创建 regression test entry 与 replay script  
  输入: golden snapshots、trace metadata、baseline manifest  
  输出: `scripts/replay_run.py`、`scripts/compare_snapshot.py`、`tests/integration/test_replay_and_regression.py`  
  验收: `python -m pytest tests/integration/test_replay_and_regression.py`  
  依赖: Phase 6 完成

- [ ] 导出 QWK-ready 字段与评分评测接口  
  输入: `FinalDimensionDecision[]`、可选 `CompositeDecision`、样例标签映射  
  输出: `src/evaluation/qwk.py`、`src/evaluation/export.py`、`tests/unit/evaluation/test_qwk_export.py`  
  验收: `python -m pytest tests/unit/evaluation/test_qwk_export.py`  
  依赖: baseline 输出结构已稳定

- [ ] 增加 inter-agent consistency metrics hooks  
  输入: `ScoreHypothesis[]`、`ConflictRecord[]`、`AdjudicationRecord[]`  
  输出: `src/evaluation/consistency.py`、`tests/unit/evaluation/test_consistency_hooks.py`  
  验收: `python -m pytest tests/unit/evaluation/test_consistency_hooks.py`  
  依赖: QWK-ready 导出、评分与冲突 contract

- [ ] 固化未来迭代边界与 baseline 回归护栏  
  输入: golden snapshots、统一验收入口、零硬编码规则  
  输出: `tests/integration/test_iteration_guardrails.py`、`docs/iteration_guardrails.md` 或等价执行说明  
  验收: `python -m pytest tests/integration/test_iteration_guardrails.py`  
  依赖: regression/replay、snapshot 比对、QWK/consistency hooks

- [ ] 将统一验收入口升级为 baseline 回归入口  
  输入: baseline manifest、snapshot 比对、可选真实 provider smoke  
  输出: 支持回归模式的 `scripts/accept_baseline.py`  
  验收: `python scripts/accept_baseline.py --bundle configs/bundles/asap_set8_baseline.bundle.yaml --manifest data/samples/baseline_manifest.yaml --provider mock --check-snapshots`  
  依赖: replay、compare snapshot、iteration guardrails

阶段退出条件：后续 prompt/agent 优化已被 baseline snapshot、replay、QWK-ready export 与 consistency hooks 保护；若优化回归，能被统一入口立即拦截。  
失败回退：保留 Phase 6 稳定 baseline，不接受任何破坏 snapshot 或 replay 的新优化实现。

## 11. Integration & Baseline Testing

- [ ] 使用样例学生文本，完整跑通 `mock` 模式 MAS  
  输入: `data/samples/baseline_manifest.yaml`、baseline bundle、`MockProvider`  
  输出: manifest 内全部样例的运行工件  
  验收: `python scripts/accept_baseline.py --bundle configs/bundles/asap_set8_baseline.bundle.yaml --manifest data/samples/baseline_manifest.yaml --provider mock`  
  依赖: Phase 6 完成

- [ ] 验证输出结构满足 schema  
  输入: `artifacts/baseline/` 运行工件、全部 contract schema  
  输出: schema validation 报告  
  验收: `python scripts/validate_run.py --run-dir artifacts/baseline/20716/mock && python scripts/validate_run.py --run-dir artifacts/baseline/20717/mock --require-adjudication`  
  依赖: baseline 运行工件已生成

- [ ] 验证 evidence / descriptor / score 引用链闭合  
  输入: 运行工件中的 `EvidenceSpan[]`、`FinalDimensionDecision[]`、feedback 输出  
  输出: 引用链闭合校验结果  
  验收: `python -m pytest tests/integration/test_policy_aware_pipeline.py -k citation`  
  依赖: Explanation Policy 已实现、运行工件已生成

- [ ] 验证 adjudication / aggregation 路径至少可被触发一次  
  输入: `sample_20717` 运行工件、adjudication/aggregation policy 配置  
  输出: 含触发记录的校验结果  
  验收: `python -m pytest tests/e2e/test_baseline_snapshots.py -k adjudication && python scripts/validate_run.py --run-dir artifacts/baseline/20717/mock --require-adjudication`  
  依赖: adjudication path 已打通

- [ ] 验证 `mock` 与 `real` provider 模式都可执行  
  输入: baseline bundle、样例文本、provider 配置  
  输出: `mock` 与 `real` 两种运行工件  
  验收: `python scripts/accept_baseline.py --bundle configs/bundles/asap_set8_baseline.bundle.yaml --manifest data/samples/baseline_manifest.yaml --provider mock && python -m pytest tests/e2e/test_real_provider_smoke.py -m real`  
  依赖: Phase 5 与 Phase 6 完成

- [ ] 形成统一 baseline 验收命令  
  输入: manifest、snapshot、run validator、可选 real smoke  
  输出: 团队统一使用的单一命令  
  验收: `python scripts/accept_baseline.py --bundle configs/bundles/asap_set8_baseline.bundle.yaml --manifest data/samples/baseline_manifest.yaml --provider mock --check-snapshots`  
  依赖: 所有 integration 与 baseline tests 已就位

## 12. Definition of Done

- [ ] 主链路在 `mock` 模式下可运行，并覆盖 normal path、adjudication path、fallback path、terminal validation。
- [ ] `mock` baseline 可稳定复现，golden snapshots 与 replay 结果一致。
- [ ] `python -m pytest tests/unit tests/integration tests/e2e -q` 通过；必要的 real provider smoke test 至少成功一次。
- [ ] `ArtifactBundle`、`EvidenceSpan`、`DimensionObservation`、`ScoreHypothesis`、`ConflictRecord`、`AdjudicationRecord`、`FinalDimensionDecision`、可选 `CompositeDecision` 与 trace metadata 全部落地并受 contract tests 保护。
- [ ] 配置驱动成立；代码中不存在对当前六维 trait、固定 1-6 分档、当前 composite 公式、当前 adjudication 阈值、当前 display annotation 的硬编码依赖。
- [ ] `mock` / `real` provider 边界清晰；provider 层不承载 rubric 语义、adjudication 逻辑或 explanation policy。
- [ ] 输出结构稳定，已具备 QWK-ready export fields、inter-agent consistency hooks、replay metadata 与 snapshot regression 护栏。
- [ ] 后续 prompt、agent、provider 优化必须在不破坏 baseline 宪法、snapshot 与统一验收命令的前提下进行。

## 13. Rollback Strategy

- [ ] 当前 Phase 连续测试失败时，回退到当前 Phase 起点，不在失败实现上继续补丁式修补。
- [ ] 当前实现导致任一前序已通过测试失效时，撤销当前 Phase 的全部实现，恢复到前序 Phase 最近一次全绿状态。
- [ ] 当前实现开始侵蚀配置边界、状态机边界、contract 边界或 provider/policy 边界时，立即回退，不讨论“先跑通再说”。
- [ ] 不允许跨 Phase 打补丁；每次修复都必须在当前 Phase 边界内完成并重新通过该 Phase 验收。
- [ ] 已通过验收的前序产物必须保留，包括 `configs/` 工件、contract tests、golden snapshots、replay harness 与统一验收入口。
- [ ] 回退后必须先恢复对应 Phase 的最小可运行状态，再重新实现；不得带着未验证的临时字段、临时脚本或临时 prompt 进入下一轮。
