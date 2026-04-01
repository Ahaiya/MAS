# Phase 3.1 评估结论：Provider System Prompt 通道独立化

日期：2026-04-01

## 调研范围

- `src/agents/scorer.py`
- `src/agents/extractor.py`
- 关联调用链：
  - `src/agents/coverage.py`
  - `src/agents/chunker.py`
  - `src/agents/feedback.py`
  - `src/agents/prompt_builders.py`
  - `src/providers/prompt_loader.py`
  - `src/config/schema.py`（`PromptFileSchema`）

## 现状

1. 当前 prompt 文件结构仅包含单字段 `prompt_template`，未区分 `system_prompt` 与 `user_prompt`。
2. `LLMRequest` 支持 `system` 字段，但现有 agent 在调用时几乎都只传 `prompt`，未走独立 `system` 通道。
3. `scorer` 与 `extractor` 的提示词均由 `prompt_builders` 拼接成单字符串后直接下发。

## 改造影响评估

若将 system prompt 拆为独立可调优配置层，至少会影响以下模块：

1. `src/config/schema.py`：扩展 prompt YAML schema（新增 system 字段或双模板结构）
2. `src/providers/prompt_loader.py`：加载与渲染逻辑改造
3. `src/agents/scorer.py`：请求构造改造（`LLMRequest.system`）
4. `src/agents/extractor.py`：请求构造改造
5. `src/agents/coverage.py`：请求构造改造
6. `src/agents/chunker.py`：请求构造改造
7. `src/agents/feedback.py`：请求构造改造
8. 相关 prompt 配置文件与测试用例同步更新

结论：**影响范围 > 3 个 agent**。

## 执行决策

按 `outer_loop_plan.md` Task 3.1 规则：

- 该项需要单独立项，不在本轮（Phase 3+4 合并执行）中进行代码级重构。
- 本轮完成调研与范围评估，保留实现边界，避免引入高耦合变更风险。
