# MAS CLI 使用说明

`scripts/` 目录现在只保留项目的主入口脚本。推荐统一从下面这个入口使用整个系统：

```bash
python -m scripts --help
```

当前命令分组：

```bash
python -m scripts eval ...
python -m scripts outer-loop ...
python -m scripts task ...
python -m scripts metrics ...
python -m scripts config ...
```

---

## 1. 目录职责

- `python -m scripts eval`
  - 内环单样本评分入口。
  - 读取一份学生材料，执行完整评估流水线，输出评分与调试产物。
- `python -m scripts outer-loop ...`
  - 外环调优入口。
  - 围绕配置、训练集和 probe 结果进行多轮优化。
- `python -m scripts task ...`
  - 任务配置草案入口。
  - 用于生成、查看、修订、确认任务级配置。
- `python -m scripts metrics ...`
  - 评估结果统计与诊断入口。
  - 当前包括 `qwk` 和 `coverage` 两类指标。
- `python -m scripts config validate`
  - bundle 配置校验入口。

工具型 CLI 实现已经迁到 `src/utils/`：

- [`src/utils/compute_qwk.py`](/Users/ahai/Code/MAS/src/utils/compute_qwk.py)
- [`src/utils/compute_coverage_metrics.py`](/Users/ahai/Code/MAS/src/utils/compute_coverage_metrics.py)
- [`src/utils/validate_config.py`](/Users/ahai/Code/MAS/src/utils/validate_config.py)

---

## 2. 内环评分：`eval`

### 2.1 最常用写法

```bash
python -m scripts eval "data/training/maker_hackathon/sample.md"
```

这条命令会：

1. 读取输入材料。
2. 加载当前任务背景对应的 bundle。
3. 选择 bundle 默认维度，或由 `--dim` 指定一个二级指标维度 rubric。
4. 执行完整评估流水线。
5. 输出 `run_trace.json`、`feedback.json`、`hypotheses.json` 等文件。

### 2.2 输出目录默认规则

- 默认输出目录为：

```bash
artifacts/{task_name}/{dim}
```

### 2.3 常见示例

最短可用写法：

```bash
python -m scripts eval "data/training/maker_hackathon/sample.md"
```

显式切换评价维度：

```bash
python -m scripts eval "data/training/maker_hackathon/sample.md" --dim b1
```

显式指定输出目录：

```bash
python -m scripts eval "data/training/maker_hackathon/sample.md" \
  --output-dir artifacts/maker_hackathon/A4
```

关闭详细日志：

```bash
python -m scripts eval "data/training/maker_hackathon/sample.md" --no-verbose
```

关闭 debug bundle：

```bash
python -m scripts eval "data/training/maker_hackathon/sample.md" --no-debug-bundle
```

使用兼容参数名：

```bash
python -m scripts eval --input "data/training/maker_hackathon/sample.md"
```

### 2.4 参数说明

统一 CLI 入口 `python -m scripts eval` 当前支持这些参数：

- `INPUT_FILE`
  - 待评估的工程材料文件。
  - 支持作为位置参数直接传入。
- `--input`, `-i`
  - 与位置参数等价的兼容写法。
  - 不要同时传两个不同路径。
- `--bundle`, `-b`
  - 指定要使用的 bundle。
  - 默认值：`configs/bundles/engineering_eval_baseline.bundle.yaml`
- `--dim`
  - 指定本次评分使用的二级指标维度配置。
  - 例如：`A4`、`B1`、`C2`、`F2`
  - 不传时使用 bundle 中声明的默认维度。
- `--output-dir`, `-o`
  - 指定产出目录。
  - 不传时自动写入 `artifacts/{task_name}/{dim}`
- `--verbose / --no-verbose`
  - 是否打印详细执行过程、节点时间线、各维度反馈和 LLM 统计。
  - 默认开启。
- `--debug-bundle / --no-debug-bundle`
  - 是否输出调试 bundle，包括 node artifacts、prompt/response、viewer 等。
  - 默认开启。

### 2.5 何时直接用 `python scripts/eval.py`

如果你只需要标准用法，优先用：

```bash
python -m scripts eval ...
```

如果你需要显式覆盖 `model_config`，可以直接调用脚本入口：

```bash
python scripts/eval.py "data/training/maker_hackathon/sample.md" \
  --model-config configs/model_config.yaml \
  --dim A4
```

`scripts/eval.py` 比统一 CLI 多一个参数：

- `--model-config`, `-m`
  - 指定模型分配配置文件。
  - 默认值：`configs/model_config.yaml`

### 2.6 内环评分的主要输出文件

默认会在样本目录下看到这些文件：

- `run_trace.json`
  - 整条流水线的执行轨迹。
- `feedback.json`
  - 最终维度评分、聚合分和反馈文本。
- `hypotheses.json`
  - 各评审 Agent 的原始打分假设。
- `evidence_spans.json`
  - 证据抽取结果。
- `observations.json`
  - 观测点整理结果。
- `conflicts.json`
  - 分歧检测结果。
- `adjudication_records.json`
  - 冲突重评分与裁决记录。
- `_debug/{run_id}/...`
  - 仅在 `--debug-bundle` 打开时生成。

---

## 3. 外环调优：`outer-loop`

外环命令用于对配置进行多轮优化，而不是评一篇单独样本。

### 3.1 运行外环优化

```bash
python -m scripts outer-loop run
```

常见写法：

```bash
python -m scripts outer-loop run --max-iterations 10
```

```bash
python -m scripts outer-loop run \
  --bundle configs/bundles/engineering_eval_baseline.bundle.yaml \
  --training-set data/1组—虚拟故居重建计划.md
```

参数说明：

- `--max-iterations`
  - 最大迭代轮数。
  - 默认值：`5`
- `--bundle`
  - 外环要调优的 bundle。
  - 默认值：`configs/bundles/engineering_eval_baseline.bundle.yaml`
- `--training-set`
  - 外环使用的训练材料。
  - 默认值：`data/1组—虚拟故居重建计划.md`
  - 当前实现兼容单个 `.md` 文件，也保留了 legacy TSV 场景。

### 3.2 查看外环状态

```bash
python -m scripts outer-loop status
```

用途：

- 查看实验日志路径。
- 查看累计迭代数。
- 查看最新一轮的 `iteration`、`changed_unit`、`verdict`、`qwk_composite`。

### 3.3 回滚到某一轮快照

```bash
python -m scripts outer-loop rollback --iter-id 003
```

参数说明：

- `--iter-id`
  - 要回滚到的迭代编号。

### 3.4 手动跑 probe

```bash
python -m scripts outer-loop probe --name qwk_probe
```

也可以显式指定产物目录和训练集：

```bash
python -m scripts outer-loop probe \
  --name qwk_probe \
  --artifacts-dir artifacts/eval \
  --training-set data/training_set_8.tsv
```

参数说明：

- `--name`
  - probe 名称，必填。
- `--artifacts-dir`
  - probe 要读取的评估产物目录。
  - 默认值：`artifacts/eval`
- `--training-set`
  - probe 需要对照的训练集或标签源。

### 3.5 外环 Provider 相关环境变量

`outer-loop` 与 `task draft/revise` 会使用单独的 Provider 配置，来源是环境变量：

- `OUTER_LOOP_API_KEY`
  - 外环调用所需的 API Key。
- `OUTER_LOOP_MODEL`
  - 可选，显式指定模型。
- `OUTER_LOOP_API_BASE`
  - 可选，指定兼容接口地址。
- `OUTER_LOOP_TEMPERATURE`
  - 可选，覆盖 temperature。
- `OUTER_LOOP_MAX_TOKENS`
  - 可选，覆盖 max tokens。

这些变量会在 [outer_loop.py](/Users/ahai/Code/MAS/scripts/outer_loop.py) 中读取。

---

## 4. 任务配置工作流：`task`

这组命令主要服务于“新任务配置”的搭建与冻结。

### 4.1 生成任务草案

```bash
python -m scripts task draft \
  --task-id a1 \
  --task-brief "评价学生的技术方案说明质量"
```

或者从文件读取任务说明：

```bash
python -m scripts task draft \
  --task-id a1 \
  --task-brief-file docs/task_brief_a1.md
```

参数说明：

- `--task-id`
  - 任务 ID，必填。
- `--task-brief`
  - 直接传入任务说明文本。
- `--task-brief-file`
  - 从文件读取任务说明。
- `--bundle`
  - 使用哪个 bundle 作为基础配置。

### 4.2 查看当前草案

```bash
python -m scripts task show --task-id a1
```

### 4.3 根据教师指令修订草案

```bash
python -m scripts task revise \
  --task-id a1 \
  --instruction "把评价对象明确限定为学生本人提交的文字材料"
```

参数说明：

- `--task-id`
  - 任务 ID，必填。
- `--instruction`
  - 单条修订指令，必填。
- `--bundle`
  - 基础 bundle。

### 4.4 确认并冻结任务

```bash
python -m scripts task confirm --task-id a1
```

这个命令会：

1. 冻结当前草案。
2. 生成任务文件。
3. 把 bundle 重新绑定到当前 active task。

---

## 5. 指标与诊断：`metrics`

### 5.1 QWK 统计

```bash
python -m scripts metrics qwk \
  --eval-dir artifacts/eval \
  --source data/training_set_8.tsv \
  --rater average \
  --output results/qwk_report.json
```

参数说明：

- `--eval-dir`, `-e`
  - 每篇样本产物目录的根目录。
  - 默认值：`artifacts/eval`
- `--source`, `-s`
  - 人工评分来源文件，通常是 TSV。
  - 默认值：`data/training_set_8.tsv`
- `--rater`, `-r`
  - 选择人工真值来源：`rater1`、`rater2`、`average`
  - 默认值：`rater1`
- `--output`, `-o`
  - 可选，把完整 JSON 报告写到指定路径。

输出内容包括：

- composite QWK
- 各维度 MAS vs human QWK
- `rater_1` vs `rater_2` 的 agent-to-agent QWK
- 每篇样本的一致性摘要

### 5.2 Coverage 诊断

```bash
python -m scripts metrics coverage --eval-dir artifacts/eval
```

只计算单篇：

```bash
python -m scripts metrics coverage \
  --eval-dir artifacts/eval \
  --essay-id 6
```

只打印，不写文件：

```bash
python -m scripts metrics coverage --eval-dir artifacts/eval --no-write
```

参数说明：

- `--eval-dir`, `-e`
  - 样本产物目录根路径。
- `--essay-id`
  - 只计算某一篇样本。
- `--write / --no-write`
  - 是否把结果写成 `coverage_metrics.json`
  - 默认开启。

### 5.3 关于 `eval-dir` 的一个重要区别

这里需要特别注意：

- `python -m scripts eval ...` 的默认输出目录是 `artifacts/{task_name}/{dim}`
- `python -m scripts metrics qwk ...` 和 `python -m scripts metrics coverage ...` 的默认 `--eval-dir` 仍然是 `artifacts/eval`

所以如果你要直接分析 `eval` 产物，通常需要显式传：

```bash
--eval-dir artifacts/{task_name}/{dim}
```

例如：

```bash
python -m scripts metrics coverage --eval-dir artifacts/maker_hackathon/A4 --essay-id 6
```

---

## 6. 配置校验：`config validate`

校验 bundle 是否能被完整解析、schema 是否通过、freeze hash 是否正常：

```bash
python -m scripts config validate \
  --bundle configs/bundles/engineering_eval_baseline.bundle.yaml
```

显示更详细信息：

```bash
python -m scripts config validate \
  --bundle configs/bundles/engineering_eval_baseline.bundle.yaml \
  --verbose
```

参数说明：

- `--bundle`, `-b`
  - 要校验的 bundle 文件，必填。
- `--verbose`, `-v`
  - 输出更详细的 bundle 统计信息。

---

## 7. 推荐使用路径

### 7.1 只做单样本评分

```bash
python -m scripts eval "data/training/maker_hackathon/sample.md"
```

### 7.2 先做单样本评分，再看 coverage

```bash
python -m scripts eval "data/training/maker_hackathon/sample.md" --dim A4
python -m scripts metrics coverage --eval-dir artifacts/maker_hackathon/A4 --essay-id 7
```

### 7.3 跑外环优化

```bash
python -m scripts outer-loop run --max-iterations 5
python -m scripts outer-loop status
```

### 7.4 创建一个新任务

```bash
python -m scripts task draft --task-id a1 --task-brief "评价学生的技术方案分析"
python -m scripts task revise --task-id a1 --instruction "要求只评价学生本人输入"
python -m scripts task confirm --task-id a1
```

---

## 8. 兼容入口

以下脚本入口仍然存在，但更建议统一使用 `python -m scripts ...`：

- `python scripts/eval.py ...`
- `python scripts/outer_loop.py ...`
- `python scripts/mas.py ...`

如果你只记一个入口，记这个就够了：

```bash
python -m scripts --help
```


