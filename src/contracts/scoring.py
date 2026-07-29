"""
scoring schema：独立双链路评价的数据结构。

  Unit[]             -> DimensionScore    （单 Rater / Rater3 对一个观测点的评分）
  DimensionScore     -> RaterChainResult  （单 Rater 完整链：选段 + 证据 + 分数）
  RaterChainResult[] -> FinalDecision     （两链比较：一致 or Rater3 仲裁后）

证据引用一律为 unit_ids（对 DataPackage.units 的编号引用），不复述原文。

设计不变式：所有模型均为冻结（不可变）的 dataclass。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List


# ── ScoreSource ──────────────────────────────────────────────────────────────


class ScoreSource(str, Enum):
    """FinalDecision.final_score 的来源。

        CONSENSUS   – 双链结果一致，直接取一致值。
        ADJUDICATED – 双链分歧，经 Rater3 仲裁产出。"""

    CONSENSUS = "consensus"
    ADJUDICATED = "adjudicated"


# ── DimensionScore ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DimensionScore:
    """单次评分（Rater 的 score 阶段，或 Rater3 仲裁）对一个观测点的评分结果。

        分值就是量规尺度上的整数，尺度定义留在量规配置里，不随每个分数复制一份。

        属性：
            score: 该观测点的整数分。
            supporting_unit_ids: 支持该评分的 Unit 编号引用。
            rationale: 评分理由（自由文本，用于审计追踪）。
            confidence: 评分者的置信度，范围 [0.0, 1.0]。"""

    score: int
    supporting_unit_ids: List[int]
    rationale: str
    confidence: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0.0, 1.0], got {self.confidence}.")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "supporting_unit_ids": list(self.supporting_unit_ids),
            "rationale": self.rationale,
            "confidence": self.confidence,
        }


# ── RaterChainResult ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class RaterChainResult:
    """单个 Rater 对一个观测点的完整链：选段 → 取证 → 评分。

        选段、证据引用与最终分数/rationale 绑定在同一结构里，构成可独立审计的一条链。

        属性：
            rater_id: 标识生成该链的评分代理（例如 "rater_1"）。
            dimension_id: 来自评分量规配置的不透明观测点标识符。
            selected_unit_ids: select 阶段选出的相关 Unit 编号。
            evidence_unit_ids: extract 阶段引用为证据的 Unit 编号。
            score: score 阶段产出的 DimensionScore（含最终 rationale/confidence）。"""

    rater_id: str
    dimension_id: str
    selected_unit_ids: List[int]
    evidence_unit_ids: List[int]
    score: DimensionScore

    def to_dict(self) -> Dict[str, Any]:
        """产物里把 DimensionScore 摊平进这条链，不再多一层嵌套。"""
        return {
            "rater_id": self.rater_id,
            "dimension_id": self.dimension_id,
            "selected_unit_ids": list(self.selected_unit_ids),
            "evidence_unit_ids": list(self.evidence_unit_ids),
            **self.score.to_dict(),
        }


# ── FinalDecision ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FinalDecision:
    """一个观测点的最终权威评分决定（双链一致，或经 Rater3 仲裁）。

        属性：
            dimension_id: 来自评分量规配置的不透明观测点标识符。
            final_score: 该观测点的权威整数分。
            source: 该分数的来源 —— consensus（双链一致）或 adjudicated（Rater3 仲裁）。
            unit_ids: 支持最终决定的 Unit 编号引用。"""

    dimension_id: str
    final_score: int
    source: ScoreSource
    unit_ids: List[int]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension_id": self.dimension_id,
            "final_score": self.final_score,
            "source": self.source.value,
            "unit_ids": list(self.unit_ids),
        }
