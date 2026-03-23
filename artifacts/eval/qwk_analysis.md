# QWK 分析报告

**数据来源**：30 次真实 LLM 评价（`artifacts/eval/`）× ASAP Set 8 人工评分（`data/training_set_8.tsv`）
**生成日期**：2026-03-19

---

## 一、核心矛盾：一个关键信号

| 指标 | 值 | 含义 |
|------|-----|------|
| MAS vs Human（macro QWK） | **0.4768** | 中等，偏低 |
| Agent vs Agent（macro QWK） | **0.8505** | 近乎完美 |

Agent 之间高度一致，但集体偏离了人工评分标准。不是噪声问题，而是**系统性偏差**。

---

## 二、人工双评分员一致性（基准上限）

全量 723 篇 ASAP Set 8，rater1 vs rater2：

| 维度 | 人-人 QWK | R1 均值 | R2 均值 | 均值差 | 精确一致率 | 邻近一致率 (±1) |
|---|---|---|---|---|---|---|
| Ideas & Content | 0.5310 | 3.76 | 3.83 | +0.07 | 57.4% | 97.9% |
| Organization | 0.5416 | 3.72 | 3.77 | +0.05 | 61.1% | 97.7% |
| Voice | **0.4668** | 3.94 | 4.02 | +0.08 | 59.6% | 97.9% |
| Word Choice | 0.4816 | 3.86 | 3.89 | +0.03 | 64.6% | 98.1% |
| Sentence Fluency | 0.5074 | 3.73 | 3.78 | +0.05 | 59.8% | 97.4% |
| Conventions | 0.5465 | 3.56 | 3.59 | +0.02 | 59.8% | 98.8% |
| **Macro-avg** | **0.5125** | | | | | |

人工评分员之间邻近一致率（±1 分以内）均达 **97–99%**，精确一致率约 57–65%。这是 MAS 可参照的现实上限。

---

## 三、MAS vs 人工：逐维度对比（30 篇）

| 维度 | 人-人 QWK（上限） | MAS-人 QWK | 差距 | 人类均值 | MAS 均值 | 偏差方向 |
|---|---|---|---|---|---|---|
| Ideas & Content | 0.531 | 0.365 | -0.166 | 3.63 | 3.83 | 高估 +0.20 |
| Organization | 0.542 | 0.552 | **+0.010** | 3.67 | 3.50 | 低估 -0.17 |
| Voice | 0.467 | 0.280 | -0.187 | 3.83 | 4.10 | 高估 +0.27 |
| Word Choice | 0.482 | 0.601 | **+0.119** | 3.63 | 3.37 | 低估 -0.27 |
| Sentence Fluency | 0.507 | 0.687 | **+0.180** | 3.47 | 3.37 | 低估 -0.10 |
| Conventions | 0.547 | 0.376 | -0.171 | 3.43 | 2.97 | 低估 -0.47 |
| **Macro** | **0.513** | **0.477** | **-0.036** | | | |

Organization、Word Choice、Sentence Fluency 三个维度已**超过人工评分员之间的一致性**。问题集中在 Voice 和 Conventions。

---

## 四、根本原因分析

### 原因一：Voice Anchor 结构性通胀（最严重）

`configs/prompts/scoring.yaml` 中 anchor 示例的 Voice 分数系统性偏高：

| Anchor 整体质量 | Anchor 中的 Voice 分数 |
|---|---|
| Score 5 anchor（"The Jump"） | Voice **6** |
| Score 4 anchor（"Student Council"） | Voice **5** |
| Score 3 anchor（"A New Truck"） | Voice **4** |
| Score 2 anchor（"A Job Makes You Pay"） | Voice **4** |

尽管 prompt 中有免责声明"Do NOT treat this as a general rule"，LLM 从 few-shot 示例中学习的权重远高于文字警告。模型实际学到的规则是：**3 分质量的文章 Voice 给 4 分**。

这直接导致 Voice QWK 只有 **0.28**，是所有维度中最低的。在早期 10 篇报告中 Voice 偏差高达 +1.0（mas_mean=4.6 vs human_mean=3.6），即使后来增加了 calibration guardrails，问题仍未根本解决（30 篇时仍 +0.27）。

### 原因二：Prompt 的反保守偏向指令

`scoring.yaml` 中明确写道：

```
- A score of 4 means "clear, coherent, and functional" — NOT "excellent."
- If the essay is functional and communicates clearly with only minor weaknesses, lean toward 4.
- If you are deciding between two adjacent scores, lean higher.
```

这对所有维度产生系统性上偏，导致 Ideas & Content（+0.20）、Voice（+0.27）均值高于人工。

### 原因三：Conventions 系统性低估（-0.47）

方向相反的偏差，MAS 给 Conventions 的分数普遍低于人工。人工的 Conventions 一致性是所有维度中最好的（QWK=0.547），但 MAS 只达到 0.376。

可能原因：系统看到大量 `@CAPS1`、`@PERSON1` 等匿名化 token，尽管 prompt 明确说不惩罚，LLM 底层可能仍将这些 token 的存在与"非标准写作"隐性关联，在 Conventions 上偏严。

### 原因四：Agent 回音壁效应

Agent-vs-agent QWK 高达 **0.8505**，而 MAS-vs-human 只有 **0.4768**。Agent 之间互相强化了相同的偏差，没有外部人工反馈来校正。一致性高不等于准确性高。

### 原因五：置信度固定为 0.85

所有 30 篇反馈的 `confidence` 字段均精确等于 **0.85**，无任何方差。说明模型在输出置信度时没有真正校准不确定性，是 LLM 对该字段语义理解不准确的体现。

---

## 五、各维度问题汇总

| 维度 | MAS-人 QWK | 偏差方向 | 主要原因 |
|---|---|---|---|
| Voice | **0.28** | 高估 +0.27 | Anchor 结构性通胀 |
| Ideas & Content | 0.37 | 高估 +0.20 | "lean toward 4" 指令 |
| Conventions | 0.38 | 低估 -0.47 | 匿名化 token 干扰 |
| Organization | 0.55 | 低估 -0.17 | 接近正常 |
| Word Choice | 0.60 | 低估 -0.27 | 接近正常 |
| Sentence Fluency | **0.69** | 低估 -0.10 | 最佳维度 |

---

## 六、改善优先级建议

| 优先级 | 操作 | 预期收益 |
|---|---|---|
| 最高 | 修复 Voice anchor：将 4 个 anchor 示例中的 Voice 分数降至与整体质量对应的水平（5→4→3→3） | Voice QWK 可能提升至 0.40+ |
| 高 | Voice guardrails 中将 Score 3 改为更强的默认描述（"most typical essays **should** receive 3"） | 减少高估偏差 |
| 中 | Conventions 增加更强的 token 豁免指令，或增加 Conventions 专项 anchor | 缩小 -0.47 偏差 |
| 低 | 扩大样本至 ≥100 篇 | n=30 时 QWK 统计波动约 ±0.1，结论可信度有限 |

---

## 七、总分 QWK（6 维度之和，量程 6–36）

| 对比 | N | QWK | 均值 A | 均值 B | 均值差 | 精确一致 | ±2 分内 |
|---|---|---|---|---|---|---|---|
| 人-人（全量 723 篇） | 723 | 0.6311 | 22.58 | 22.88 | +0.30 | 23.0% | 63.4% |
| MAS-人（30 篇） | 30 | **0.6952** | 21.67 | 21.13 | -0.53 | 30.0% | 70.0% |

**MAS 总分 QWK（0.6952）已超过人-人上限（0.6311）**，但这是**维度偏差相互抵消**的结果：Voice 高估（+0.27）与 Conventions 低估（-0.47）在加总时互相抵消，使总分"凑巧"居中。总分 QWK 好看不代表维度评分准确。

### 逐篇总分对比（30 篇）

| 文章 | r1 | r2 | MAS | MAS-r1 | 说明 |
|---|---|---|---|---|---|
| 20728 | 29 | 19 | 23 | -6 | 人-人本身相差 10 分，高争议文章 |
| 20743 | 27 | 24 | 21 | -6 | MAS 偏低 |
| 20718 | 18 | 25 | 25 | +7 | MAS 与 r2 一致，r1 是低估方 |
| 20737 | 25 | 24 | 20 | -5 | MAS 偏低 |
| 其余 26 篇 | — | — | — | ±4 以内 | |

20728（r1=29，r2=19）和 20718（r1=18，r2=25）本身人工差距就很大，说明部分"大偏差"源自人工评分的内在不一致，而非 MAS 的问题。

---

## 附：早期 10 篇 vs 全量 30 篇对比

| 维度 | 10 篇 macro QWK | 30 篇 macro QWK | 变化 |
|---|---|---|---|
| Voice | 0.1667 | 0.2800 | +0.113 |
| Macro-avg | 0.3902 | 0.4768 | +0.087 |

Voice 校准 guardrails 有效果，但尚未根本解决 Anchor 通胀问题。
