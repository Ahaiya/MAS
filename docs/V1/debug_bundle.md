# Debug Bundle 使用说明

`debug bundle` 是开发排障模式下的单次运行调试包，用来查看：

- 节点级执行过程
- 节点输入输出快照
- 真实 LLM 请求 / 响应明细
- 本地静态 viewer

它只面向开发排查，不是生产监控能力。

## 1. 启用方式

单篇评估时加上 `--debug-bundle`：

```bash
python scripts/eval.py --essay-id 20716 --debug-bundle
```

如果你只想验证节点级数据流、不想真的调用 LLM，可以配合 `--mock-provider`：

```bash
python scripts/eval.py --essay-id 20716 --mock-provider --debug-bundle
```

批量模式也支持，但会生成很多调试文件：

```bash
python scripts/eval.py --limit 3 --debug-bundle
```

## 2. 输出位置

正常评估产物仍然写到：

```text
artifacts/eval/<essay_id>/
```

调试包会额外写到：

```text
artifacts/eval/<essay_id>/_debug/<run_id>/
```

运行结束后，CLI 会打印两条路径：

- `调试包`
- `Viewer`

## 3. 调试包内容

调试包目录下主要有这些文件：

- `manifest.json`
  - bundle 元数据、provider 绑定、viewer 入口
- `events.jsonl`
  - 顺序事件流，包含 `node_started`、`node_finished`、`llm_call_started`、`llm_call_finished` 等
- `summary.json`
  - viewer 读取的聚合摘要
- `run_trace.json`
- `feedback.json`
- `hypotheses.json`
- `evidence_spans.json`
- `observations.json`
- `node_artifacts/`
  - 各节点的输入输出快照
- `llm_calls/`
  - 每次 LLM 调用的元信息
- `llm_calls/blobs/`
  - prompt、schema、response、structured output 原文
- `viewer/index.html`
  - 本地静态调试页面

## 4. 打开 Viewer

推荐从调试包目录启动一个本地静态服务器：

```bash
cd artifacts/eval/20716/_debug/run-xxxxxxxxxxxx
python -m http.server 8000
```

然后打开：

```text
http://localhost:8000/viewer/
```

不建议直接双击 `viewer/index.html` 用 `file://` 打开，因为浏览器可能拦截本地 `fetch`。

## 5. Viewer 里怎么看

建议按这个顺序排查：

1. 先看顶部 summary
   - 确认 run_id、节点数、LLM 调用数、总 token
2. 再看 `Nodes`
   - 找出最可疑的节点
3. 点开节点后看 `Artifacts`
   - 先确认输入对象是不是你预期的
   - 再确认输出对象在哪一步开始偏离
4. 再看 `LLM Calls`
   - 点开具体调用，查看完整 prompt / response / structured output

## 6. 常见排查路径

### coverage 选错 chunk

看：

- `node_coverage`
- 对应的 `llm_calls`
- `node_artifacts/node_coverage/output_coverage_plans.json`

### extractor 抽不到证据

看：

- `node_extractor/input_coverage_plans.json`
- `node_extractor/output_spans_by_dimension.json`
- 对应 extraction call 的 prompt 和 response

### scorer 某个维度分数异常

看：

- `node_scorer/output_hypotheses.json`
- 对应 call 的 `dimension_id` / `rater_id`
- prompt 里的 facet evidence 和 response 里的 `proposed_score`

### feedback 文案异常

看：

- `node_feedback/input_decisions.json`
- feedback call 的 prompt / response

## 7. 重要说明

- `--mock-provider --debug-bundle`
  - 主要用于看节点级数据流，不会有真实 API 行为
- 真实 provider 模式下
  - 会保存完整 prompt / response，只适合本地调试
- 调试包文件会比较大
  - 尤其是多篇批量运行时
