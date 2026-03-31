# 内环改造会话交接文档

> 本文件供新 CC 会话开始时阅读，读完即可无缝继续工作。
> 最后更新：2026-03-30
>
> 权威设计文档：`docs/V1/MAS_V1_DESIGN.md`
> 执行清单：`docs/V1/plan.md`

---

## 一、系统定位（必读）

MAS 是一个基于量规（Rubric）的多智能体文本自动评价系统，**最终目标是评价学生解决复杂工程问题的能力**。

输入文本包括：
- 学生与 AI 的长对话记录（8000-20000 词）
- 工程问题研究报告（含章节、代码、图表引用）

当前验证集是 ASAP Set 8（短文章，6 个写作维度），但所有架构决策必须在长文档视角下成立。

**四条全局设计原则**（写入了 `MAS_V1_DESIGN.md` 第零章）：

1. **"可回放性" = 推理链可解释、文本可溯**，不是 bit-for-bit 技术确定性。
   Preprocess/Coverage 可使用轻量 LLM，无需强制确定性。

2. **所有设计决策默认外环存在**。落盘数据要服务外环 Diagnose/Measure。

3. **Preprocess + Coverage 的存在意义 = 让 Extractor 抽得准、抽得快**。
   两者共同解决 Extractor 面对长文档的上下文管理问题。

4. **configs/artifacts 铁律（最重要）**：
   - 外环可修改的参数 → **必须**在 `configs/` 中有对应表示（禁止硬编码魔法数字）
   - 外环用于诊断的数据 → **必须**保存在 `artifacts/` 中（禁止运行时丢弃）
   - **内环建立机制，外环调节参数**，两者通过 configs/artifacts 解耦
   - 这条原则约束所有后续阶段：新增可调量 → 必须进 configs/；新增诊断数据 → 必须进 artifacts/

---

## 二、当前代码状态

### 阶段完成状态

| 阶段 | 内容 | 状态 |
|------|------|------|
| 前置修复 | 数据落盘、minimum_evidence_units 修正、中文分句 | 已完成 |
| A | 合约层扩展（TextUnit/NormalizedDocument/CoveragePlan/DimensionObservation 新字段） | 已完成 |
| B | Prompt 模板设计（chunking.yaml / dimension_relevance.yaml） | 已完成 |
| C | Chunker Agent 实现（LLM 语义分块 + 层次化长文档处理 + 规则降级） | 已完成 |
| D | Coverage Agent LLM 化（并行维度预筛 + full_scan 降级） | 已完成 |
| E | Runner 整合（chunker + coverage 接入主流水线） | 已完成 |
| F | Bundle 配置（asap_set8_chunking.yaml + bundle 更新 + eval.py 适配） | 已完成 |
| G | 测试补充（1019 passed, 11 skipped） | 已完成 |
| H | 外环度量指标（coverage_recall_rate / precision_rate / chunk_boundary_quality） | 已完成 |
| **I** | Extraction Agent 改造 | **待执行** |
| **J** | Observer 改造 | 已规划 |
| **K** | Scorer Agent 改造 | 已规划 |

**当前测试状态**：1019 passed, 11 skipped（全部通过）

### artifacts 结构（当前已落盘的文件）

每次评估在 `artifacts/eval/{essay_id}/` 下产出：

| 文件 | 内容 | 外环用途 |
|------|------|---------|
| `run_trace.json` | 执行轨迹、各节点耗时 | 诊断失败/性能 |
| `feedback.json` | 各维度分数 + composite 总分 | QWK 计算 |
| `hypotheses.json` | rater_1/rater_2 原始分 + rationale 全文 | Diagnose 核心材料 |
| `evidence_spans.json` | EvidenceSpan 全字段（text_quote/facet_ids/unit_id） | 证据质量分析 |
| `observations.json` | DimensionObservation + FacetFinding（含 coverage_miss） | facet 覆盖分析 |
| `coverage_metrics.json` | recall_rate / precision_rate / boundary_quality（阶段 H 新增） | 外环 Measure |
| `report.md` | 人类可读报告 | — |

---

## 三、下一步工作：Extraction Agent 改造（阶段 I）

### 背景与动机

**前置假设**：阶段 A-H 均已完成。Preprocess 产出语义 chunk（含 chunk_title/chunk_method），
Coverage 产出过滤后的 CoveragePlan（target_unit_ids 仅含相关 chunk，relevance_scores 已填充）。

**当前问题**：
- `extractor.py` 忽略 `target_unit_ids`，仍把全文传给 LLM
- `unit_id` 始终为 None（应由 quote 匹配回填）
- LLM 直接输出 offset（不可靠，应由代码回填）
- 无 per-dimension prompt override 机制
- `support_type`（supporting/counter/neutral）未落到 EvidenceSpan

**改造目标**：
1. Extractor 只将 target_unit_ids 对应的 chunk 传给 LLM（消费 Coverage 成果）
2. LLM 不再输出 offset，只输出 quote 文本 → 由 quote_matcher 回填 offset 和 unit_id
3. Prompt 传入完整分档描述符和 facet 语义描述
4. 支持 per-dimension prompt override（外环可为单个维度定制不同指令）
5. EvidenceSpan 新增 `support_type` 字段（供 Observer 正反分流使用）

### 关键设计决策

**quote_matcher 三级匹配策略**：
- Level 1 — exact：`normalized_text.find(quote)`，精确子串匹配
- Level 2 — normalized：双方空白符归一化后再匹配
- Level 3 — fuzzy：滑动窗口 + SequenceMatcher，阈值 ≥ 0.85
- 匹配失败 → scope=GLOBAL（保留 quote 文本，offset 为 None）

**LLM 输出格式（新）**：
```json
{
  "evidence_spans": [
    {
      "quote": "exact text from the document",
      "chunk_id": "c2",
      "facets": ["clarity_focus", "main_idea_salience"],
      "support_type": "supporting|counter|neutral"
    }
  ]
}
```

**per-dimension override 机制**：
- override 目录：`configs/prompts/evidence_extraction_overrides/{dimension_id}.yaml`
- 加载优先级：存在 override 时用 override，不存在时用全局模板
- 本阶段只建立机制和目录结构，不创建具体 override 文件

### 阶段 I 执行顺序

```
I1（quote_matcher 工具）‖ I2（prompt 模板重写）
  ↓
I3（prompt_builders 更新，依赖 I2）
  ↓
I4（extractor.py 重写，依赖 I1 + I3）
  ↓
I5 ‖ I6（runner 适配 + observer 确认，可并行）
  ↓
I7（测试）→ I8（外环接口确认）
```

---

## 四、后续已规划阶段概要

### 阶段 J：Observer 改造

**前置假设**：阶段 I 已完成，EvidenceSpan 携带 `support_type` 字段。

**改造目标**（纯确定性逻辑，无 LLM）：
- J1：按 support_type 正反分流 → FacetFinding.supporting_span_ids / counter_span_ids 正确填充
- J2：coverage_miss_span_ids 计算（span.unit_id 不在 target_unit_ids 中则记录）
- J3：uncertainty_notes 丰富化（无证据 facet 生成提示）
- J4：EvidenceSpan 合约扩展（新增 `support_type: str = "supporting"` 字段，向下兼容）
- J5：测试

**执行顺序**：`J4 → J1 → J2‖J3 → J5`

### 阶段 K：Scorer Agent 改造

**前置假设**：阶段 I 和 J 均已完成，Observer 产出按 facet 组织、正反分流的 DimensionObservation。

**改造目标**：
- K1：ASAP 硬编码内容外部化 → `configs/prompts/scoring_context.yaml`（锚定样例、校准提示、脱敏说明）
- K2：Scoring prompt 模板重写（移除 essay_text，改为 facet_evidence 结构化输入）
- K3：`build_scoring_prompt()` 重写（移除 document 参数，构造 facet_evidence 上下文）
- K4：`scorer.py` 重写（evidence_ids 使用 LLM 返回值 + 有效性校验 + fallback）
- K5：Runner 适配（移除 document 参数，传入 scoring_context + override_template）
- K6：deterministic_scorer 确认（预计无需改动）
- K7：测试
- K8：外环旋钮总结

**核心设计决策（P8 已决策）**：
- Scorer **不再看原文**，只接收 Observer 产出的结构化证据
- 若 Extractor 漏了关键证据 → RE_EXTRACT 回退机制解决，Scorer 不承担搜证职责
- per-dimension override 目录：`configs/prompts/scoring_overrides/{dimension_id}.yaml`

**执行顺序**：`K1 → K2 → K3 → K4 → K5‖K6 → K7 → K8`

---

## 五、关键约束与边界

**不能破坏的东西**：
- mock 模式下的回归测试（1019 passed）
- `src/contracts/` 中所有 dataclass 的 `from_dict()` 严格 schema 检查（新字段必须有默认值）
- `PipelineRunner` 返回值类型 `(RunTrace, Dict)` 不变
- ASAP Set 8 的 adjudication policy 和 aggregation weights 固定不可调

**不需要的东西**：
- bit-for-bit 技术确定性（replay = 推理链可溯）
- 外部 NLP 库（spaCy/stanza 等）
- 向量数据库（当前用 LLM 直接做相关性判断）

---

## 六、重要配置路径速查

| 配置 | 文件 |
|------|------|
| Bundle 主配置 | `configs/bundles/asap_set8_baseline.bundle.yaml` |
| 量规（6 维度/facets）| `configs/rubrics/asap_set8_baseline.yaml` |
| 裁决策略（阈值/Cusp Rule）| `configs/policies/adjudication/asap_set8_default.yaml` |
| 聚合总分公式 | `configs/policies/aggregation/asap_set8_composite.yaml` |
| 分块策略（外环调节入口） | `configs/policies/chunking/asap_set8_chunking.yaml` |
| 分块 Prompt 模板 | `configs/prompts/chunking.yaml` |
| 维度预筛 Prompt 模板 | `configs/prompts/dimension_relevance.yaml` |
| 现有抽取 Prompt 模板 | `configs/prompts/evidence_extraction.yaml`（阶段 I 重写） |
| 现有评分 Prompt 模板 | `configs/prompts/scoring.yaml`（阶段 K 重写） |
| ASAP 评分上下文（阶段 K 新建） | `configs/prompts/scoring_context.yaml` |
| Extraction per-dimension override | `configs/prompts/evidence_extraction_overrides/` |
| Scoring per-dimension override | `configs/prompts/scoring_overrides/` |
| Provider 环境变量 | `.env`（LLM_API_KEY, RATER_1/2/3_API_KEY 等） |

---

## 七、运行验证命令

```bash
# 全套 mock 回归测试（不需要 API Key）
python -m pytest tests/unit/ tests/integration/ --override-ini="addopts=" -q

# 单篇真实评估（需要 .env 配置）
python scripts/eval.py --essay-id 20716

# 批量 QWK 计算
python scripts/compute_qwk.py

# 覆盖度指标计算（阶段 H 新增）
python scripts/compute_coverage_metrics.py --essay-id 20716
```

---

## 八、关键设计推演记录

本文件记录的设计决策均已写入 `MAS_V1_DESIGN.md`，此处为快速参考：

1. **两次 LLM 调用方案**（已实现，阶段 C+D）：
   - Call 1：Haiku 结构分块（常规文档直接全文，长文档层次化处理）
   - Call 2 × 6 并行：每维度独立预筛相关 chunk

2. **quote_matcher 设计**（阶段 I 实现）：
   - LLM 只输出 quote 文本，不输出 offset
   - 代码通过三级匹配（exact → normalized → fuzzy）回填 offset 和 unit_id
   - 匹配结果记录到 extraction_note，供外环统计 quote 质量

3. **Scorer 不看原文**（P8 已决策，阶段 K 实现）：
   - Scorer 接收 Observer 产出的结构化证据（facet_findings + quote + 正反标记）
   - 缺失证据 → RE_EXTRACT 机制，而非 Scorer 自行补充

4. **evidence_ids 使用 LLM 返回值**（阶段 K 实现）：
   - 旧代码：忽略 LLM 返回，直接用 `observation.supporting_span_ids`
   - 新代码：优先用 LLM 返回的 evidence_ids（需有效性校验），全部无效时 fallback

5. **ASAP 专属内容配置化**（阶段 K 实现）：
   - 锚定样例、校准提示、脱敏说明 → `configs/prompts/scoring_context.yaml`
   - Voice 校准规则 → `configs/prompts/scoring_overrides/voice.yaml`
   - 换量规时只替换 scoring_context 文件即可

6. **外环三项质量指标**（阶段 H 已实现）：
   - `coverage_recall_rate`：span 落在 target_unit_ids 内的比例
   - `coverage_precision_rate`：target_unit_ids 中实际贡献 span 的比例
   - `chunk_boundary_quality`：span.text_quote 跨块比率

---

> 最后更新：2026-03-30
> 当前进度：阶段 A-H 已完成，阶段 I 待执行，J/K 已规划
> 下一步：按 plan.md 阶段 I 执行顺序（I1‖I2 → I3 → I4 → I5‖I6 → I7 → I8）
