# 06 — Engine 门面 + 线性编排（串行）

**What to build:** `Engine.from_bundle(path).evaluate(package, dim)` 把全链串起来跑通一次完整评价——串行版本。这是把 02-05 各段接成一条线性函数链的集成票，也是"可用系统"的门面。评单个一级指标或缺省评全部一级指标。

**Blocked by:** 05（聚合/反馈/产物就位）

**Status:** ready-for-agent

- [ ] 新增 `src/engine.py`（顶层门面）：`Engine.from_bundle(bundle_path)` 编译 bundle、从 model_config 建 providers、加载 prompts；支持注入 providers（测试用）
- [ ] `engine.evaluate(package, dim=None)`：串行线性链 `segment → rate(r1) → rate(r2) → reconcile →[adjudicate]→ feedback`；`dim` 指定单个一级指标，缺省评当前任务下全部一级指标
- [ ] model_config 为模型/参数唯一来源，缺失即报错（删除三条兜底路径）；密钥值只从 .env 读
- [ ] bundle 迁移到 `configs/bundle.yaml`，`bundle_id` 改 `default`
- [ ] trace 用**收集器模式**：各阶段函数返回结果时附带 StageTrace，engine 只收集，写 `run_trace.json`（仅成本/性能，不含决策）
- [ ] 错误直接抛出/单阶段处理，无状态机回退重入
- [ ] 测试（注入 FakeProvider）在最高层 `engine.evaluate` 跑完整评价：单 dim 与全 dim；一致场景与分歧场景端到端产出正确；缺 provider 报错；一个接缝覆盖 segment→rate→reconcile→adjudicate→feedback 全链
