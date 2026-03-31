"""
本包收纳评价流水线各阶段的 Agent Worker。

Agent worker modules for the MAS evaluation pipeline.

Each module exposes a single `run()` function that is a pure, stateless worker.
All agents communicate exclusively via Phase 2 contracts from src/contracts/.
"""
