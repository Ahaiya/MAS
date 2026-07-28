# 05 — 聚合 + 反馈 + 产物落盘

**What to build:** 各二级指标的最终分聚合为一级指标分，生成文字反馈与雷达数据，并把一次评价的产物写到磁盘。这一票让"一个一级指标评完 → 磁盘上有可供前端/教师/审计消费的完整产物"成立。

**Blocked by:** 04（每个二级指标有唯一 final_score + source）

**Status:** ready-for-agent

- [ ] 聚合单一路径（删 with/without variant）：一级指标分 = auto_equal 等权平均各二级指标 final_score
- [ ] `feedback.py` 生成每二级指标文字反馈 + 雷达图数据（各二级指标分数数组）；新增 `feedback.yaml` prompt（更名对齐旧 explanation）
- [ ] 写 `feedback.json`（精简：一级指标分 + 雷达 + 各二级指标 final_score/source/证据 unit_ids/文字反馈——证据存 unit_ids 不存复述原文）
- [ ] 写 `rater_chains.json`（完整双链证据 + 仲裁记录，审计用）
- [ ] 写 `package.json`（切分后带编号单元）到 sample 层，供把 unit_ids 解读回原文
- [ ] 产物目录三层 `artifacts/{task}/{sample}/{dim}/`（package.json 在 sample 层，其余在 dim 层）
- [ ] 测试（注入 FakeProvider）：consensus 与 adjudicated 两种 source 都能正确落盘；feedback.json 的 unit_ids 能经 package.json 回指原文；一级指标分等于二级指标等权平均
