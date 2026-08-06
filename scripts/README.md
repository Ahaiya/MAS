# MAS Scripts

命令行入口是单文件 `scripts/cli.py`，直接跑即可——文件头自注入 `sys.path`，不用
`python -m`，也不需要 `pip install` 装 console_scripts：

```bash
uv run scripts/cli.py --help
```

`uv run` 会先同步项目环境再执行。注意不是 `uv python`——那是管理解释器版本的
（`install` / `list` / `pin` / `find`），传脚本路径会报 unrecognized subcommand。
不用 uv 的话就走 venv 里的解释器：`.venv/bin/python scripts/cli.py --help`。

## 解析一次提交

解析与评价是两条命令：解析一次付一次钱，评价可以反复迭代。

```bash
uv run scripts/cli.py parse 报告.pdf 答辩.pptx --task experiment --submission 2025213184
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `FILES...`（位置） | 必填 | 这次提交的**全部**源文件（PDF/Word/PPT/图片…） |
| `--task` | 无，必填 | 任务 id，对应 `configs/tasks/<task>/` |
| `--submission` | 无，必填 | 提交标识（学号） |
| `--configs` | `configs` | 配置根目录（解析配置读 `<configs>/parse.yaml`） |
| `--packages` | `packages` | 解析结果根目录 |
| `--force` | 关 | 已有解析原件也重新解析（会重新付费） |

任一文件解析失败则整个提交失败、不产出数据包；已成功文件的原件仍然落盘，重跑时
直接跳过、不重复付费。

## 评价一份材料

```bash
# 评单个二级指标
uv run scripts/cli.py eval --task experiment --submission 2025213184 --dim a1

# 不传 --dim：评该任务下全部二级指标
uv run scripts/cli.py eval --task experiment --submission 2025213184
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--task` | 无，必填 | 任务 id，对应 `configs/tasks/<task>/`；漏传即报错并列出可选任务 |
| `--submission` | 无，必填 | 提交标识（学号）；按约定去 `packages/<task>/<submission>/package.json` 找包 |
| `--configs` | `configs` | 配置根目录 |
| `--dim` | 全部二级指标 | 二级指标（如 `a1`） |
| `--packages` | `packages` | 解析结果根目录 |
| `--output-dir` | `artifacts` | 产物落盘根目录 |

`eval` 不吃文件路径，也**不会**在包缺失时替你跑一次 parse——那会把网络/配额失败
请回评价链路，并藏起这一步要花钱的事实。

模型与参数固定从 `configs/model_config.yaml` 读取（含超时/重试/并发），密钥值只从
`.env` 读且按厂商命名——因此没有 `--model-config` 开关。旧的 `--input/-i`、
`--verbose`、`--debug-bundle`、`--bundle` 也一并删除。

执行流：读数据包（parse 的产出）→ 双链独立评价（select → extract → score，两个 Rater
各跑一遍）→ 分歧时 Rater3 仲裁 → 生成反馈。同一份提交下各观测点并发评价，上限
由 `configs/model_config.yaml` 的 `runtime.max_workers` 控制（默认 8）。

解析结果（花钱买来的**输入**）与评价产物分开落盘：

```text
packages/{task}/{submission}/raw/<源文件名>.json    # 解析服务的完整响应，原样保存
packages/{task}/{submission}/package.json           # 带编号单元的数据包
artifacts/{task}/{submission}/{dim}/feedback.json      # 给前端/学生：分数 + 雷达 + 证据编号 + 文字反馈
artifacts/{task}/{submission}/{dim}/rater_chains.json  # 审计：双链完整证据 + 仲裁记录
artifacts/{task}/{submission}/{dim}/run_trace.json     # 成本/性能，含失败被隔离的维度
```

## 配置校验

```bash
uv run scripts/cli.py config validate --task experiment
uv run scripts/cli.py config validate --task experiment --configs configs
```

走一遍配置：仲裁策略、五套 prompt、以及该任务下每个二级指标的量规
都能加载。刻意不构建 provider——配置是否自洽与密钥是否就位是两件事，因此没有 `.env`
也能在 CI 里跑。

