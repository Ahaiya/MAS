"""本包收纳评价流水线各阶段的 Agent Worker。

MAS 评价流水线的 Agent worker 模块。

每个模块都暴露一个单一的 `run()` 函数，该函数是一个纯粹的、无状态的 worker。
所有 Agent 都仅通过来自 src/contracts/ 的 Phase 2 合约进行通信。"""
