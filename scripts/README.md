# MAS Scripts

命令行入口是单文件 `scripts/cli.py`，直接跑即可——文件头自注入 `sys.path`，不用
`python -m`，也不需要 `pip install` 装 console_scripts：

```bash
uv run scripts/cli.py --help
```

`uv run` 会先同步项目环境再执行。注意不是 `uv python`——那是管理解释器版本的
（`install` / `list` / `pin` / `find`），传脚本路径会报 unrecognized subcommand。
不用 uv 的话就走 venv 里的解释器：`.venv/bin/python scripts/cli.py --help`。

## 评价一份材料

```bash
# 评单个一级指标
uv run scripts/cli.py eval data/experiment/2025213184.md --dim a1

# 不传 --dim：评当前任务下全部一级指标
uv run scripts/cli.py eval data/experiment/2025213184.md
```

全部参数只有四个：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `INPUT_FILE`（位置） | 必填 | 待评价的 `.md` / `.txt` 材料 |
| `--bundle` | `configs/bundle.yaml` | 量规 bundle |
| `--dim` | 全部一级指标 | 一级指标（如 `a1`） |
| `--output-dir` | `artifacts` | 产物落盘根目录 |

模型与参数固定从 `configs/model_config.yaml` 读取（含超时/重试/并发），密钥值只从
`.env` 读且按厂商命名——因此没有 `--model-config` 开关。旧的 `--input/-i`、
`--verbose`、`--debug-bundle` 也一并删除。

执行流：切分（零 LLM，确定性）→ 双链独立评价（select → extract → score，两个 Rater
各跑一遍）→ 分歧时 Rater3 仲裁 → 生成反馈。同一 sample 下各二级指标并发评价，上限
由 `configs/model_config.yaml` 的 `runtime.max_workers` 控制（默认 8）。

产物按三层落盘：

```text
artifacts/{task}/{sample}/package.json        # 切分后的带编号单元
artifacts/{task}/{sample}/{dim}/feedback.json      # 给前端/学生：分数 + 雷达 + 证据编号 + 文字反馈
artifacts/{task}/{sample}/{dim}/rater_chains.json  # 审计：双链完整证据 + 仲裁记录
artifacts/{task}/{sample}/{dim}/run_trace.json     # 成本/性能，含失败被隔离的维度
```

## 配置校验

```bash
uv run scripts/cli.py config validate
uv run scripts/cli.py config validate --bundle configs/bundle.yaml
```

走一遍 bundle 声明的全部引用：policies、prompts、以及当前任务下每个一级指标的量规
都能加载。刻意不构建 provider——配置是否自洽与密钥是否就位是两件事，因此没有 `.env`
也能在 CI 里跑。

## 前端审核台

审核台需要用仓库自带服务器启动，因为它同时负责静态文件和 `POST /api/corrections`：

```bash
uv run scripts/server.py
```

然后访问 `http://127.0.0.1:8000/frontend/index.html`。

不要用 `python3 -m http.server` 代替——它没有 `/api/corrections` POST 接口，点击
`Release` 时会返回 501。

`server.py` 目前只做静态文件 + `POST /api/corrections`（把教师修正写进
`experiments/pending_corrections.json`）。消费这个队列的 `CorrectionAgent` 已随 v1
外环一并删除——人在回路是三期的事，届时重新接线。
