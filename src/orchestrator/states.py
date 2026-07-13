"""流水线状态枚举与终止状态集合。

移除 StateGraph/router 后，仅保留状态标签和终止状态集合。"""

from __future__ import annotations

from enum import Enum
from typing import FrozenSet


class PipelineState(str, Enum):
    """评估流水线中的所有可能状态。"""

    INIT = "init"
    CONFIG_RESOLVED = "config_resolved"
    PREPROCESSED = "preprocessed"
    COVERAGE_PLANNED = "coverage_planned"
    EVIDENCE_EXTRACTED = "evidence_extracted"
    OBSERVATION_BUILT = "observation_built"
    SCORED = "scored"
    CONSISTENCY_CHECKED = "consistency_checked"
    ADJUDICATED = "adjudicated"
    FEEDBACK_RENDERED = "feedback_rendered"
    RE_EXTRACT = "re_extract"
    RE_SCORE = "re_score"
    VALIDATED = "validated"
    FAILED = "failed"
    HUMAN_REVIEW = "human_review"


TERMINAL_STATES: FrozenSet[PipelineState] = frozenset({
    PipelineState.VALIDATED,
    PipelineState.FAILED,
    PipelineState.HUMAN_REVIEW,
})
