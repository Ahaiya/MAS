"""评测兼容层：保留历史 `src.evaluation.consistency` 导入路径。"""

from src.outer_loop.metrics.consistency import (
    ConsistencyReport,
    DimensionConsistency,
    compute_consistency,
    extract_hypotheses_from_trace,
)

__all__ = [
    "ConsistencyReport",
    "DimensionConsistency",
    "compute_consistency",
    "extract_hypotheses_from_trace",
]
