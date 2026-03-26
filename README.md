# MAS — 多智能体文本自动评价系统

基于量规（Rubric）的多智能体评分系统，通过多个 LLM 评审员协作，对文本进行可解释的自动评分。

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

复制示例配置并填入 API Key：

```bash
cp .env.example .env
```

编辑 `.env`，至少填写以下字段：

```ini
LLM_PROVIDER=deepseek          # 支持 openai / deepseek / anthropic 等
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-xxx
LLM_API_BASE=https://api.deepseek.com/v1
```

---

## 脚本使用

### 评估入口（单篇 / 批量统一）

`scripts/eval.py` 是唯一的评估入口，通过参数自动判断模式：
- 提供 `--essay-id` → **单篇模式**，输出完整报告 + 人工评分对比 + 评审员假设分数
- 否则 → **批量模式**，默认跳过已有结果（幂等），结束后打印 LLM 调用汇总

**常用选项：**

| 选项 | 说明 | 默认值 |
|------|------|--------|
| `--essay-id` / `-e` | 单篇模式：指定 essay_id | — |
| `--essay-ids` | 批量模式：逗号分隔的 ID 列表 | — |
| `--limit` / `-n` | 批量模式：最多处理篇数（0 = 全部） | `0` |
| `--source` / `-s` | TSV 数据文件路径 | `data/training_set_8.tsv` |
| `--bundle` / `-b` | 配置 bundle 文件路径 | `configs/bundles/asap_set8_baseline.bundle.yaml` |
| `--output-dir` / `-o` | 产出目录 | `artifacts/eval` |
| `--force` | 批量模式：覆盖已有结果重新评估 | `false` |
| `--delay` | 批量模式：每篇间隔秒数（降低限速风险） | `5.0` |
| `--no-verbose` | 关闭详细内部信息 | 默认开启 |

**单篇示例：**

```bash
# 评估样本 20722（默认输出到 artifacts/eval/20722）
python scripts/eval.py --essay-id 20722

# 写入自定义目录
python scripts/eval.py --essay-id 20722 --output-dir artifacts/my_run
```

**批量示例：**

```bash
# 评估前 10 篇
python scripts/eval.py --limit 10

# 只评估指定 ID
python scripts/eval.py --essay-ids 20716,20717,20718

# 强制重新评估全部
python scripts/eval.py --force

# 减小请求间隔
python scripts/eval.py --limit 20 --delay 2
```

**产出文件（每篇）：**

```
artifacts/eval/{essay_id}/
  feedback.json      # 各维度评分与反馈文本
  run_trace.json     # 完整执行轨迹（含每节点状态、耗时、输入输出引用）
  hypotheses.json    # 各评审员原始假设分数
  report.md          # 格式化 Markdown 报告（单篇模式生成）
```

**详细输出内容（默认开启）：**
- 每个流水线节点的状态、耗时、输入/输出引用
- 两位评审员对每个维度的原始打分及分歧标记
- 本次 LLM 调用次数、Token 用量、耗时（按角色分组）
- 综合评分表（含人工评分对比与偏差）
- 各维度详细反馈文本

---

### 计算 QWK 指标

批量评估完成后，计算 MAS vs 人工评分的 QWK，以及评审员间 QWK（inter-agent consistency）。

```bash
python scripts/compute_qwk.py
```

**常用选项：**

| 选项 | 说明 | 默认值 |
|------|------|--------|
| `--eval-dir` / `-e` | 批量评估产出目录 | `artifacts/eval` |
| `--source` / `-s` | TSV 数据文件（人工评分） | `data/training_set_8.tsv` |
| `--rater` / `-r` | 对比基准：`rater1` / `rater2` / `average` | `rater1` |
| `--output` / `-o` | 写入 JSON 报告的路径（可选） | — |

**示例：**

```bash
# 以 rater1 为基准
python scripts/compute_qwk.py

# 以两位人工评审均值为基准，并输出 JSON 报告
python scripts/compute_qwk.py --rater average --output artifacts/qwk_report.json
```

**输出内容：**
- 每个维度的 QWK（MAS vs 人工）及均值偏差
- 每个维度的评审员间 QWK（rater_1 vs rater_2）及邻近一致率
- 整体 Macro-avg QWK

---

### 验证配置 bundle

修改配置后，用此脚本检查 bundle 合法性、schema 一致性与版本哈希。

```bash
python scripts/validate_config.py \
  --bundle configs/bundles/asap_set8_baseline.bundle.yaml
```

加 `--verbose` 可查看维度数、量规版本等详情。

---

## 修改配置

所有配置均在 `configs/` 目录，**不需要修改源代码**。

### 目录结构

```
configs/
  bundles/
    asap_set8_baseline.bundle.yaml   # 主配置入口，整合所有子配置
  rubrics/
    asap_set8_baseline.yaml          # 量规定义（维度、分档描述）
  policies/
    adjudication/asap_set8_default.yaml   # 裁决策略（何时触发第三方裁决）
    aggregation/asap_set8_composite.yaml  # 聚合策略（如何合并多维分数）
    explanation/evidence_grounded_v1.yaml # 解释策略（反馈生成规则）
  prompts/
    evidence_extraction.yaml         # 证据抽取 prompt 模板
    scoring.yaml                     # 打分 prompt 模板
    explanation.yaml                 # 反馈生成 prompt 模板
```

### 常见修改场景

#### 更换/新增评审员的 LLM

在 `configs/bundles/asap_set8_baseline.bundle.yaml` 的 `provider_config` 部分修改：

```yaml
provider_config:
  rater_1:
    model: "deepseek-chat"
    api_base: "https://api.deepseek.com/v1"
    api_key_env: "RATER_1_API_KEY"   # 对应 .env 中的变量名
  rater_2:
    model: "qwen-plus"
    api_base: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key_env: "RATER_2_API_KEY"
```

然后在 `.env` 中填入对应的 API Key：

```ini
RATER_1_API_KEY=sk-xxx
RATER_2_API_KEY=sk-yyy
```

> 支持的千问模型名：`qwen-turbo` / `qwen-plus` / `qwen-max`

#### 调整裁决阈值

编辑 `configs/policies/adjudication/asap_set8_default.yaml`，修改触发裁决的分差阈值。

#### 修改评分维度或分档描述

编辑 `configs/rubrics/asap_set8_baseline.yaml`，修改维度定义、分档描述或满分范围。

#### 修改 prompt 模板

编辑 `configs/prompts/` 下对应的 YAML 文件。修改后建议重新运行 `validate_config.py` 验证。

---

## 产出文件说明

| 文件 | 说明 |
|------|------|
| `feedback.json` | 各维度最终评分、裁决结果、反馈文本 |
| `run_trace.json` | 完整执行轨迹，含每个节点的状态、耗时、输入输出引用 |
| `hypotheses.json` | 每位评审员对每个维度的原始打分假设 |
| `report.md` | 人类可读的 Markdown 评价报告（单篇评估时生成） |

---

## 架构文档

| 文件 | 内容 |
|------|------|
| `docs/Zen.md` | 项目设计原则，架构决策依据 |
| `docs/architecture.md` | Agent 角色、状态机、数据流 |
| `docs/research.md` | 数据契约、配置 schema、评测框架 |
| `docs/Rubric_Guidelines.md` | 量规维度与分档定义说明 |
| `docs/Adjudication_Rules.md` | 双评审、裁决与聚合规则说明 |
