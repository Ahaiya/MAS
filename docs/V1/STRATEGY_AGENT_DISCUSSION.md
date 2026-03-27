# Strategy Agent Discussion

## 1. 讨论前提

- 默认建立在**真实 LLM 调用**路径上讨论。
- 假定基础骨架问题已经修复，系统已经能稳定完成一条评价链路：
  - 预处理
  - 读取量规
  - 按量规抽取证据
  - 基于证据进行多 Agent 评分
  - 裁决并生成评语
- 新增 Agent 的目标不是“替代现有评分链路”，而是让系统输出**更接近人类评分**，并最终推动 QWK 提升。

## 2. 核心结论

- 这个新增 Agent 是**可行的**。
- 但它不应该主要作为“单篇作文运行中的自由决策者”。
- 更合理的定位是：
  - **内环**：现有单篇评价流水线，负责产出结构化中间态与最终结果。
  - **外环**：新增的策略优化 Agent，基于多样本结果做误差分析、策略提案和版本迭代。
- QWK 是**批量离线指标**，不是单篇运行时奖励，所以真正的“自学习/自优化”必须发生在外环。

## 3. 推荐的双环结构

### 3.1 内环：单篇评价链路

职责：

- 执行一篇文章的完整评价流程
- 保持 contracts、state machine、trace、policy 的稳定性
- 产出可审计中间态：
  - `CoveragePlan`
  - `EvidenceSpan`
  - `DimensionObservation`
  - `ScoreHypothesis`
  - `ConflictRecord`
  - `FinalDimensionDecision`

内环的目标：

- 稳定执行
- 充分留痕
- 为外环提供可分析材料

### 3.2 外环：策略优化 Agent

职责：

- 读取一批样本的运行结果与人工标签
- 分析系统误差模式
- 提出下一版策略
- 触发批量验证
- 决定策略是否晋级

外环的目标：

- 提升 human-system alignment
- 以 QWK 为北极星，但不唯 QWK 论

## 4. 为什么不能把“学习”直接塞进运行时编排器

- 单篇运行时拿不到真实标签，无法直接知道 QWK 是否变好。
- 若运行时直接“学”，会破坏 bundle 冻结、可回放和可审计边界。
- 若让编排器自由修改 prompt / 权重 / policy，很容易把 orchestrator 变成隐式 scorer。
- 运行时学习还会显著增加：
  - 不稳定性
  - 成本
  - 重现难度

因此：

- **运行时编排器可以更聪明**
- 但**学习与策略更新**应主要放在离线外环

## 5. 新增 Agent 的职责边界

### 5.1 应该做什么

- 观察一批样本上的系统表现
- 识别系统性误差模式
- 归因误差主要来自哪个阶段
- 提出策略调整建议
- 组织批量实验与对比评估

### 5.2 不应该做什么

- 不直接给单篇文章改最终分
- 不绕过现有 scorer / adjudicator / router
- 不在运行时直接修改 rubric / policy / aggregation 规则
- 不在没有 holdout 验证的情况下直接上线策略

## 6. 外环循环应该怎么建立

建议的基本循环：

1. `Collect`
   - 批量运行样本
   - 收集 `run_trace`、`hypotheses`、`feedback`、人工标签

2. `Measure`
   - 计算指标：
     - composite QWK
     - per-dimension QWK
     - inter-agent consistency
     - conflict rate
     - third-rater trigger rate
     - human review rate
     - cost / latency

3. `Diagnose`
   - 外环 Agent 分析主要误差模式
   - 判断问题更可能来自 extraction / observation / scoring / adjudication / calibration

4. `Propose`
   - 提出策略提案

5. `Evaluate`
   - 生成新版本策略或 bundle
   - 重新批量运行
   - 再次计算指标

6. `Promote or Reject`
   - 在 holdout 上提升稳定，才晋级
   - 否则回滚

## 7. “策略”具体指什么？



## 8. 外环 Agent 的核心能力

从讨论看，这个 Agent 未来最重要的能力不只是“编排”，而是：

- **误差归因能力**
- **策略搜索能力**
- **分数校准能力**

尤其是：

- 如果两位 scorer 一致地错，问题未必在 adjudication
- 如果 evidence coverage 很差，问题未必在 calibration
- 如果维度分基本都合理，但 composite 偏移，问题可能在 aggregation 或 calibration

因此它必须能回答：

- 错在哪里
- 为什么错
- 该改哪一层
- 改完怎么验证

## 9. 推荐的输出形态

这个 Agent 每一轮最好输出的是一份**策略提案**，而不是直接代码改动。

建议至少包括：

- 主要误差模式
- 误差归因
- 拟调整的策略项
- 每项策略的预期收益
- 每项策略的风险
- 建议验证的数据切片
- 是否建议进入下一轮批量实验

## 10. 必须提前定义清楚的问题？



## 11. 当前阶段的总体判断

当前最合理的方向不是：

- “做一个会自己学、会自己改整条流水线的大 Agent”

而是：

- “做一个面向批量样本、以 QWK 为北极星、以策略提案为输出的外环优化 Agent”

它的基本模式应当是：

**批量运行 -> 指标评估 -> 误差归因 -> 策略提案 -> 新版验证 -> 晋级/回滚**

## 12. 下一步可继续讨论的具体问题？
