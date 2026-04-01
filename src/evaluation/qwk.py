"""评测兼容层：保留历史 `src.evaluation.qwk` 导入路径。"""

from src.outer_loop.metrics.qwk import QWKResult, qwk, qwk_for_dimension

__all__ = ["QWKResult", "qwk", "qwk_for_dimension"]
