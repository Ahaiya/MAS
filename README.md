# MAS

量规化的评价引擎。


## 快速开始

```bash
uv sync                       # 建 .venv，装依赖（含 dev 组）
cp .env.example .env          # 填入厂商密钥
uv run python scripts/cli.py config validate --task experiment
```

`config validate` 走一遍配置：仲裁策略、五套 prompt、该任务下每个二级指标的量规是否都存在且能解析。跑通了再评。

解析与评价是**两条命令**：解析一次付一次钱，评价可以在同一份材料上反复迭代。

```bash
# 1. 解析一次提交的全部材料（PDF / Word / PPT / 图片…），一次提交共享同一编号空间
uv run python scripts/cli.py parse 报告.pdf 答辩.pptx --task experiment --submission 2025213223

# 2. 评单个二级指标
uv run python scripts/cli.py eval --task experiment --submission 2025213223 --dim a1

# 不传 --dim：评该任务下全部二级指标
uv run python scripts/cli.py eval --task experiment --submission 2025213223
```

重跑 `parse` 时已解析过的文件直接跳过（不重复付费），要重解析加 `--force`。


## 配置

**`.env` 只装凭证值，其余一切都在 yaml**。任何一侧缺失都直接报错，不互相兜底。

| 文件 | 管什么 |
|---|---|
| `.env` | 只有密钥值，变量名按**厂商**取（`DEEPSEEK_API_KEY`）、不按角色取；解析服务用阿里云账号的 `ALIBABA_CLOUD_ACCESS_KEY_ID` / `_SECRET`（**两个值**，与百炼的 key 不是一套） |
| `configs/model_config.yaml` | 模型、api_base、密钥的**变量名**、温度；以及并发/超时/重试（`runtime` 段） |
| `configs/adjudication.yaml` | 仲裁触发条件：两个整数 |
| `configs/tasks/<task>/` | 一个任务一套量规，`dimension/<dim>_rubric.yaml` |
| `configs/prompts/` | select / extraction / scoring / adjudication / feedback 五套提示词，文件名即阶段名 |
| `configs/parse.yaml` | 解析层：端点、超时、轮询、三个增强开关（默认全开）。哪些版面块进单元**不是配置**——它决定编号身份而非内容质量 |

没有 bundle 文件——配置路径全部由约定固定。任务用 `--task` 在调用现场指定，不写在
配置里：改一个 tracked 文件来切任务，每次实验都会带一个脏 diff。


## 产物

解析结果是花钱买来的**输入**，落在 `packages/`；评价产出落在 `artifacts/`，两者分开——
前者删了要重新付费，后者觉得不对随时整个删掉重跑。

```
packages/{task}/{submission}/
├── raw/<源文件名>.json    # 解析服务的完整响应，原样保存（要坐标/层级/置信度就从它现推）
└── package.json          # 带编号单元的数据包，单元编号的权威来源

artifacts/{task}/{submission}/{dim}/
├── feedback.json         # 最终分 + 反馈；source 字段区分 consensus / adjudicated
├── rater_chains.json     # 双链各自的选段、证据、评分
└── run_trace.json        # 调用轨迹
```

`feedback.json` 的 `source` 字段告诉教师哪些分是双链一致得出的、哪些经过了仲裁——后者值得重点复核。

`packages/`、`artifacts/` 与 `data/` 都不入版本库（学生材料以学号命名，隐私敏感）。


