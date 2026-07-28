# 04 — 双链比较 + Rater3 仲裁

**What to build:** 两条独立 Rater 链的结果被比较：一致则直接决策（source=consensus）；分歧则触发 Rater3 独立仲裁（source=adjudicated）。Rater3 看双链完整证据 + 量规 + 原文，但看不到双方分数（防锚定），输出格式与 Rater1/2 一致并强制引用证据编号。删除 average/highest 无 LLM 兜底——分歧一律走 Rater3。

**Blocked by:** 03（Rater 链产出 RaterChainResult）

**Status:** ready-for-agent

- [ ] 仲裁触发判断（纯函数）：任一二级指标分差>1，或 ≥2 个二级指标同向相邻漂移
- [ ] 一致时定出唯一 `final_score`（一致值），标记 `source=consensus`
- [ ] 分歧时调用新增 `src/agents/adjudicator.py`（Rater3）：输入双链证据 + 量规 + 原文，**不含双方分数**；输出 `DimensionScore` 格式、强制引用证据 `unit_ids`；`final_score` 取 Rater3 结果，标记 `source=adjudicated`
- [ ] rater_3 provider 缺失时直接报错（不静默降级）
- [ ] 删除 average/highest 兜底路径
- [ ] 新增 `adjudication.yaml` prompt（双链对比）；adjudication 触发规则策略保留
- [ ] 测试（注入 FakeProvider）：两链一致→consensus 不触发 Rater3；分差>1→触发 Rater3→adjudicated；同向漂移≥2→触发；缺 rater_3→报错；仲裁输出引用有效 unit_ids
