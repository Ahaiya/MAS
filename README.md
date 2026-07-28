# MAS

量规化的评价引擎。


## 快速开始

```bash
uv sync                       # 建 .venv，装依赖（含 dev 组）
cp .env.example .env          # 填入厂商密钥
uv run python scripts/cli.py config validate
```

`config validate` 校验 bundle 的引用闭包——所有量规、prompt、policy 文件是否都存在且能解析。跑通了再评。

```bash
# 评单个一级指标
uv run python scripts/cli.py eval <file> --dim a1

# 缺省评当前任务下全部一级指标
uv run python scripts/cli.py eval <file>
```


## 配置

**`.env` 只装凭证值，其余一切都在 yaml**。任何一侧缺失都直接报错，不互相兜底。

| 文件 | 管什么 |
|---|---|
| `.env` | 只有密钥值，变量名按**厂商**取（`DEEPSEEK_API_KEY`），不按角色取 |
| `configs/model_config.yaml` | 模型、api_base、密钥的**变量名**、温度；以及并发/超时/重试（`runtime` 段） |
| `configs/bundle.yaml` | 选定当前任务（`active_task_id`），并指向 policy 与 prompt |
| `configs/tasks/<task>/` | 一个任务一套量规。`task_context.yaml` + `dimension/<dim>_rubric.yaml` |
| `configs/prompts/` | select / extraction / scoring / adjudication / feedback 五套提示词 |
| `configs/policies/` | 仲裁触发条件等策略 |


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


