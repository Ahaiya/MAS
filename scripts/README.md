# MAS Scripts

当前统一入口：

```bash
python -m scripts ...
```

## 评估单个 Markdown 文件

```bash
python -m scripts eval data/training/maker_hackathon/sample.md --dim a4
```

常用参数：

```bash
python -m scripts eval \
  --input data/training/maker_hackathon/sample.md \
  --bundle configs/bundles/engineering_eval_baseline.bundle.yaml \
  --dim a4 \
  --model-config configs/model_config.yaml
```

`eval` 会读取 UTF-8 Markdown 文本，解析 bundle，加载模型配置，然后调用内环流水线完成证据提取、评分、裁决和反馈生成。运行前会自动检查 `experiments/pending_corrections.json`；如果教师修正队列中有待处理意见，会先由 `CorrectionAgent` 更新当前任务的 `task_context.yaml`，再重新加载 bundle 继续评分。

## 配置校验

```bash
python -m scripts config validate \
  --bundle configs/bundles/engineering_eval_baseline.bundle.yaml
```

## 当前保留的外环能力

系统只保留第一个人工反馈闭环：

- 前端提交教师修正到 `POST /api/corrections`
- 修正事件写入 `experiments/pending_corrections.json`
- 下一次 `eval` 启动前调用 `src.outer_loop.correction_agent.check_and_apply_corrections`
- `CorrectionAgent` 读取当前任务的 `task_context.yaml`，根据修正意见生成完整 YAML
- `ConfigPatcher` 只允许写入白名单配置文件，并在写入前创建快照

自动实验优化闭环已经移除，因此不再提供 `outer-loop run/status/rollback/probe`、`task draft/revise/confirm`、`metrics qwk/coverage` 等命令。

## 前端审核台

审核台需要使用仓库自带服务器启动，因为它同时负责静态文件和 `POST /api/corrections`：

```bash
cd /Users/ahai/Code/MAS
python scripts/server.py
```

然后访问：

```text
http://127.0.0.1:8000/frontend/index.html
```

不要用 `python3 -m http.server` 代替。它没有 `/api/corrections` POST 接口，点击 `Release` 时会返回 501。
