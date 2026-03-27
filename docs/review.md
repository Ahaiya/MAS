# MAS Review Log

本文件用于记录代码审查过程中的修正、澄清说明、设计决策补充，以及需要明确的解释性内容。

---

## 格式规范

每条记录包含以下字段：

- **日期**：记录时间
- **类型**：`修正` / `澄清` / `待确认` / `决策`
- **涉及模块**：相关文件或组件
- **内容**：具体说明

---

## 记录

<!-- 新记录从此处往下追加 -->

---

### [2026-03-26] 路径评估：R1+R2 评分完成后的全路径核查

**类型**：修正
**涉及模块**：`src/pipeline/runner.py`、`src/agents/adjudicator.py`、`src/policies/aggregation.py`

---

### [2026-03-26] 裁决机制与总分规则全面核查

**类型**：修正
**涉及模块**：`configs/policies/aggregation/asap_set8_composite.yaml`

#### 触发机制核查结论

| 规则 | 结论 |
|------|------|
| 标准规则（6 维含 V/W，`> 1`） | ✓ 正确 |
| Cusp Rule（I/O/S/C，4-4-4-4 vs 3-4-4-4，排除 V/W） | ✓ 正确 |
| Resolution = 全文级（R3 对所有维度重评） | ✓ 正确 |

#### 问题 5：`with_resolution` 变体错误使用 `direct_weighted_sum`（已修正）

- `with_resolution` 配置声明了 `source_raters: ["rater_3"]`，但 `aggregation_method: "direct_weighted_sum"` 不读取该字段，只使用 `FinalDimensionDecision.final_score`。`adjudicator` 对无冲突维度取 `rater_1` 分数，导致有 R3 时 composite 公式混用 R1 和 R3，不符合 ASAP Set 8 规定。
- **修正方案**：将 `with_resolution` 的 `aggregation_method` 改为 `average_per_trait_then_weighted_sum`，`source_raters: ["rater_3"]` 将生效，直接从 `hypotheses` 列表按 `rater_id` 查找 R3 分数，与该维度是否冲突无关，正确实现 `2*I_R3 + 2*O_R3 + 2*S_R3 + 4*C_R3`。

---

### [2026-03-26] 路径评估：R1+R2 评分完成后的全路径核查

**类型**：修正
**涉及模块**：`src/pipeline/runner.py`、`src/agents/adjudicator.py`、`src/policies/aggregation.py`

#### 问题 3：`compute_composite` 从未被 runner 调用（已修正）

- 原因：`src/policies/aggregation.py` 中 `compute_composite()` 实现完整，但 `runner.py` 从未引用（Grep 确认，仅在测试文件中调用），流水线输出无 composite 总分。
- **修正方案**：在 `runner.py` feedback 阶段前插入 `compute_composite()` 调用；`adj_records` 提升为 carry-forward 变量（无裁决时为 `[]`，供 `without_resolution` 变体判断）；composite 结果以 `feedback["composite"]` 写入 feedback_dict，实模式与 mock 模式均生效。

#### 问题 4：B-异常路径（is_resolved=False）导致 FAILED（已修正）

- 原因：`adjudicator` 兜底逻辑沿用 `conflict.recommended_path = THIRD_RATER`，路由器将其映射至 `ADJUDICATED`，而 `ADJUDICATED → ADJUDICATED` 不在状态机合法转换矩阵中，触发 `IllegalTransitionError` 后强制 FAILED。
- **修正方案**：`adjudicator.py` 兜底路径的 `resolution_path` 改为 `ResolutionPath.HUMAN_REVIEW`，路由器将未解决记录正确升级至 `HUMAN_REVIEW` 终止状态，不再触发非法转换。

---

### [2026-03-26] 总分计算链路核查

**类型**：澄清 + 修正
**涉及模块**：`src/pipeline/runner.py`、`src/agents/deterministic_consistency_checker.py`、`src/agents/consistency_checker.py`、`src/agents/adjudicator.py`、`src/policies/aggregation.py`、`configs/policies/aggregation/asap_set8_composite.yaml`、`configs/policies/adjudication/asap_set8_default.yaml`

#### 公式对比

ASAP Set 8 官方规则（`docs/Adjudication_Rules.md`）：

```
无裁决：(I_R1+I_R2) + (O_R1+O_R2) + (S_R1+S_R2) + 2*(C_R1+C_R2)
有裁决：2*I_R3 + 2*O_R3 + 2*S_R3 + 4*C_R3
```

代码实现（`aggregation.py` + config，weights I/O/S=2, C=4）：

```
无裁决：avg(I)*2 + avg(O)*2 + avg(S)*2 + avg(C)*4  → 数学等价，正确
有裁决：I_final*2 + O_final*2 + S_final*2 + C_final*4  → 结构正确，但见问题 2
```

#### 问题 1：Cusp Rule 从未被执行（已修正）

- `runner.py` 原来始终调用 `deterministic_consistency_checker.run()`，该模块自注释明确只处理 `score_distance` 一种触发器。
- 实现了完整 Cusp Rule 的 `src/agents/consistency_checker.py` 存在，但原先未被 runner 引用。
- **修正方案**：在 runner.py 一致性检查调用点加入 `_is_real()` 分支：真实 LLM 模式调用 `consistency_checker.run()`（支持全量触发器），Mock 模式保留 `deterministic_consistency_checker.run()`。同步在 runner.py 文件头写入中文说明。

#### 问题 2：Rater 3 从未真正评分（已修正）

- 原因：`rater_labels` 只含 `["rater_1", "rater_2"]`，`resolution_rater_label: "rater_3"` 未被 runner 调用；冲突发生时 `adjudicator` 从 R1/R2 中字典序选一个作为裁决结果，不符合 Set 8 规定。
- **修正方案**：
  1. 新建 `src/agents/adjudicator.py`：实现 `use_rater_3_as_authoritative` 裁决策略——有冲突维度优先取 rater_3 hypothesis，不存在时兜底并标记 `is_resolved=False`；无冲突维度直接取 rater_id 字典序最小者。
  2. `runner.py` ADJUDICATED 分支（真实 LLM 模式）：冲突检测后，先读取 `resolution_rater_label` 从 policy 配置中获取标签，调用 `scorer` 对**全部维度**触发 rater_3 重评（ASAP Set 8 的"resolution read"是全文级），将 R3 hypotheses 追加到 hypotheses 列表，再交由 `adjudicator` 裁决。deterministic 模式保留原有 `deterministic_adjudicator` 路径不变。
