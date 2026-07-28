# MAS

量规化的评价引擎。


## 快速开始

```bash
uv sync                       # 建 .venv，装依赖（含 dev 组）
cp .env.example .env          # 填入厂商密钥
uv run python scripts/cli.py config validate --task experiment
```

`config validate` 走一遍配置：仲裁策略、五套 prompt、该任务下每个二级指标的量规是否都存在且能解析。跑通了再评。

```bash
# 评单个二级指标
uv run python scripts/cli.py eval <file> --task experiment --dim a1

# 不传 --dim：评该任务下全部二级指标
uv run python scripts/cli.py eval <file> --task experiment
```


## 配置

**`.env` 只装凭证值，其余一切都在 yaml**。任何一侧缺失都直接报错，不互相兜底。

| 文件 | 管什么 |
|---|---|
| `.env` | 只有密钥值，变量名按**厂商**取（`DEEPSEEK_API_KEY`），不按角色取 |
| `configs/model_config.yaml` | 模型、api_base、密钥的**变量名**、温度；以及并发/超时/重试（`runtime` 段） |
| `configs/adjudication.yaml` | 仲裁触发条件：两个整数 |
| `configs/tasks/<task>/` | 一个任务一套量规，`dimension/<dim>_rubric.yaml` |
| `configs/prompts/` | select / extraction / scoring / adjudication / feedback 五套提示词，文件名即阶段名 |

没有 bundle 文件——配置路径全部由约定固定。任务用 `--task` 在调用现场指定，不写在
配置里：改一个 tracked 文件来切任务，每次实验都会带一个脏 diff。


## 产物

按 `{task}/{sample}/{dim}/` 落盘到 `artifacts/`：

```
artifacts/{task}/{ID}/
├── package.json          # 切分后的数据包，单元编号的权威来源
├── {dim}/
│   ├── feedback.json     # 最终分 + 反馈；source 字段区分 consensus / adjudicated
│   ├── rater_chains.json # 双链各自的选段、证据、评分
│   └── run_trace.json    # 调用轨迹
└── {dim}/ ...
```

`feedback.json` 的 `source` 字段告诉教师哪些分是双链一致得出的、哪些经过了仲裁——后者值得重点复核。

`artifacts/` 与 `data/` 都不入版本库（学生材料以学号命名，隐私敏感）。


