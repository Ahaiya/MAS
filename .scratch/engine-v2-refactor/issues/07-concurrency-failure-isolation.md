# 07 — 二级指标级并发 + 失败隔离

**What to build:** 同一 sample 下多个二级指标并发评价，一次完整评价不必串行等待数分钟；单个二级指标失败只标记该维度失败并记录，不拖垮整个 sample。纯加速 + 健壮性，不改评价逻辑。

**Blocked by:** 06（串行 engine 已跑通）

**Status:** ready-for-agent

- [ ] 二级指标级并发：`ThreadPoolExecutor`（provider IO 密集，GIL 不碍事）
- [ ] `max_workers` 从 `model_config.yaml` 的 `concurrency` 段读取，默认 8
- [ ] 失败隔离：单个二级指标评价失败（LLM 报错/超时）仅标记该 dim 失败并记录，其余维度照常产出，sample 不崩
- [ ] 并发下 trace 的成本/耗时汇总正确（线程安全）
- [ ] 测试（注入 FakeProvider）：并发结果与串行一致；某个二级指标 provider 抛错时该 dim 标记失败、其余 dim 正常落盘
