"""
评分与裁决契约，定义假设、冲突、裁决与最终决定等核心结构。

评分与裁决契约

定义多评分者评分、冲突检测、裁决与最终决定阶段的中间数据结构：

  DimensionObservation[]  -> ScoreHypothesis[]  （每个评分者每个维度一个）
  ScoreHypothesis[]       -> ConflictRecord[]   （一致性检查器输出）
  ConflictRecord[]        -> AdjudicationRecord[] （裁决器输出）
  AdjudicationRecord[]    -> FinalDimensionDecision[] （每个维度一个）
  FinalDimensionDecision[] -> CompositeDecision （可选，聚合策略）

设计不变式：
- 所有模型均为 frozen（不可变）dataclass。
- dimension_id、descriptor_refs、rater_id、policy_rule_id 均为源自评分量规或策略配置产物的不透明字符串。此处不出现硬编码的特质名称或裁决阈值。
- ScoreHypothesis.score 使用 ScoreRepresentation —— 实际有效范围由评分量规配置量表强制执行，而非本契约。
- confidence 为 [0.0, 1.0] 范围内的浮点数。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from src.contracts.score_representation import ScoreRepresentation


# ── ConflictType ───────────────────────────────────────────────────────────────


class ConflictType(str, Enum):
    """Consistency Checker 检测到的评分冲突类型。
    
        NON_ADJACENT   – 评分之间的差距超过策略允许的相邻范围。
        CUSP           – 至少有一个评分位于策略定义的等级边界上。
        ADJACENT_DRIFT – 多个相邻分歧向同一方向漂移。
        OTHER          – 其他策略定义的冲突类型。"""

    NON_ADJACENT = "non_adjacent"
    CUSP = "cusp"
    ADJACENT_DRIFT = "adjacent_drift"
    OTHER = "other"


# ── ResolutionPath ─────────────────────────────────────────────────────────────


class ResolutionPath(str, Enum):
    """针对 ConflictRecord 建议或实际采取的解决路径。
    
        THIRD_RATER    – 引入第三次评分以打破平局。
        RE_EXTRACT     – 重新评分前重新运行证据提取。
        RE_SCORE       – 不重新提取证据，直接重新评分。
        POLICY_AVERAGE – 通过策略定义的平均公式解决。
        HUMAN_REVIEW   – 升级至人工审核（无法自动解决）。"""

    THIRD_RATER = "third_rater"
    RE_EXTRACT = "re_extract"
    RE_SCORE = "re_score"
    POLICY_AVERAGE = "policy_average"
    HUMAN_REVIEW = "human_review"


# ── ScoreHypothesis ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScoreHypothesis:
    """单个评分者针对某一维度提出的评分建议。
    
        评分子智能体为每个（评分者，维度）对生成一个 ScoreHypothesis。
        每个假设必须引用能够证明所建议评分合理性的描述符等级和证据片段——缺乏支持的假设无效。
    
        属性：
            hypothesis_id: 本次运行中该评分建议的唯一 ID。
            observation_id: 该假设所基于的 DimensionObservation。
            dimension_id: 来自评分量规配置的不透明维度标识符。
            rater_id: 标识生成该假设的评分代理。
            score: 包含 canonical_score 和 scale_ref 的 ScoreRepresentation。
            descriptor_refs: 用于证明评分合理性的评分量规描述符等级引用。
            evidence_span_ids: 作为理由引用的 EvidenceSpan ID。
            rationale: 自由文本评分理由（用于审计追踪）。
            confidence: 评分者的置信度，范围 [0.0, 1.0]。"""

    hypothesis_id: str
    observation_id: str
    dimension_id: str
    rater_id: str
    score: ScoreRepresentation
    descriptor_refs: List[str]
    evidence_span_ids: List[str]
    rationale: str
    confidence: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"ScoreHypothesis '{self.hypothesis_id}': confidence must be in "
                f"[0.0, 1.0], got {self.confidence}."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "observation_id": self.observation_id,
            "dimension_id": self.dimension_id,
            "rater_id": self.rater_id,
            "score": self.score.to_dict(),
            "descriptor_refs": list(self.descriptor_refs),
            "evidence_span_ids": list(self.evidence_span_ids),
            "rationale": self.rationale,
            "confidence": self.confidence,
        }



# ── ConflictRecord ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConflictRecord:
    """在两个或多个 ScoreHypothesis 之间检测到的评分分歧。
    
        当满足裁决策略触发条件（例如非相邻评分、边界条件）时，由一致性检查器生成。recommended_path 字段建议编排器应采用哪种 ResolutionPath。
    
        属性：
            conflict_id: 本次运行中该冲突的唯一 ID。
            dimension_id: 来自评分量规配置的不透明维度标识符。
            hypothesis_ids: 存在冲突的 ScoreHypothesis 的 ID。
            conflict_type: 冲突类别（来自策略定义）。
            trigger_rule_id: 触发该记录的裁决策略规则。
            conflict_detail: 冲突的可读描述。
            recommended_path: 策略评估建议的解决路径。"""

    conflict_id: str
    dimension_id: str
    hypothesis_ids: List[str]
    conflict_type: ConflictType
    trigger_rule_id: str
    conflict_detail: str
    recommended_path: ResolutionPath

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "dimension_id": self.dimension_id,
            "hypothesis_ids": list(self.hypothesis_ids),
            "conflict_type": self.conflict_type.value,
            "trigger_rule_id": self.trigger_rule_id,
            "conflict_detail": self.conflict_detail,
            "recommended_path": self.recommended_path.value,
        }



# ── AdjudicationRecord ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AdjudicationRecord:
    """将裁决策略应用于 ConflictRecord 的结果。
    
        由裁决器生成。如果冲突无法自动解决（is_resolved=False），则 resolved_score 为 None，编排器将路由至人工审核。
    
        属性：
            adjudication_id: 本次裁决尝试的唯一 ID。
            conflict_id: 触发本次裁决的 ConflictRecord。
            dimension_id: 来自评分量规配置的不透明维度标识符。
            resolution_path: 实际采用的路径。
            policy_rule_id: 支配该解决过程的策略规则。
            resolved_score: 裁决后的评分。若未解决则为 None。
            resolver_hypothesis_id: 如果由第三方评分者解决，则为该评分者的假设 ID。
            resolution_note: 对裁决决定的可选说明。
            is_resolved: 如果已生成最终评分则为 True；如果已升级则为 False。"""

    adjudication_id: str
    conflict_id: str
    dimension_id: str
    resolution_path: ResolutionPath
    policy_rule_id: str
    resolved_score: Optional[ScoreRepresentation]
    resolver_hypothesis_id: Optional[str]
    resolution_note: Optional[str]
    is_resolved: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "adjudication_id": self.adjudication_id,
            "conflict_id": self.conflict_id,
            "dimension_id": self.dimension_id,
            "resolution_path": self.resolution_path.value,
            "policy_rule_id": self.policy_rule_id,
            "resolved_score": self.resolved_score.to_dict() if self.resolved_score else None,
            "resolver_hypothesis_id": self.resolver_hypothesis_id,
            "resolution_note": self.resolution_note,
            "is_resolved": self.is_resolved,
        }



# ── FinalDimensionDecision ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class FinalDimensionDecision:
    """针对某一评分量规维度的单一权威评分决定。
    
        由裁决器生成（若未发生冲突，则直接来自评分）。这是反馈组装器与聚合策略用于计算可选的 CompositeDecision 时所消费的对象。
    
        完整的审计追踪通过以下链得以保留：evidence_span_ids -> descriptor_refs -> final_score -> adjudication_id
    
        属性：
            decision_id: 本次运行中该决定的唯一 ID。
            dimension_id: 来自评分量规配置的不透明维度标识符。
            final_score: 该维度的权威 ScoreRepresentation。
            primary_hypothesis_id: 占优势的假设（或裁决者假设）。
            adjudication_id: 如果冲突经过裁决，则为 AdjudicationRecord 的 ID。若未发生冲突则为 None。
            evidence_span_ids: 支持最终决定的证据片段。
            descriptor_refs: 与最终评分一致的评分量规描述符等级。
            decision_confidence: 最终决定的综合置信度。
            decision_note: 用于审计的可选说明或备注。"""

    decision_id: str
    dimension_id: str
    final_score: ScoreRepresentation
    primary_hypothesis_id: str
    adjudication_id: Optional[str]
    evidence_span_ids: List[str]
    descriptor_refs: List[str]
    decision_confidence: float
    decision_note: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "dimension_id": self.dimension_id,
            "final_score": self.final_score.to_dict(),
            "primary_hypothesis_id": self.primary_hypothesis_id,
            "adjudication_id": self.adjudication_id,
            "evidence_span_ids": list(self.evidence_span_ids),
            "descriptor_refs": list(self.descriptor_refs),
            "decision_confidence": self.decision_confidence,
            "decision_note": self.decision_note,
        }



# ── CompositeDecision ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CompositeDecision:
    """跨选定维度的可选聚合评分。
    
        在启用时由聚合策略生成。聚合公式、参与维度与权重均存储在 aggregation_detail 中，其反映聚合策略配置的内容——此处无任何硬编码。
    
        属性：
            composite_id: 该复合结果的唯一 ID。
            aggregation_policy_ref: 指向所用聚合策略产物的 URI 引用。
            contributing_decision_ids: 贡献给该复合结果的 FinalDimensionDecision ID。
            composite_score: 聚合后的 ScoreRepresentation。
            aggregation_detail: 来自策略的公式/权重详情快照。
            composite_note: 关于聚合结果的可选备注。"""

    composite_id: str
    aggregation_policy_ref: str
    contributing_decision_ids: List[str]
    composite_score: ScoreRepresentation
    aggregation_detail: Dict[str, Any]
    composite_note: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "composite_id": self.composite_id,
            "aggregation_policy_ref": self.aggregation_policy_ref,
            "contributing_decision_ids": list(self.contributing_decision_ids),
            "composite_score": self.composite_score.to_dict(),
            "aggregation_detail": dict(self.aggregation_detail),
            "composite_note": self.composite_note,
        }


# ═══════════════════════════════════════════════════════════════════════════
# v2 契约 —— 独立双链路评价（与上方 v1 契约并存，见 issue 09 删除计划）
#
#   Unit[]              -> DimensionScore   （单 Rater / Rater3 对一个二级指标的评分）
#   DimensionScore       -> RaterChainResult （单 Rater 完整链：选段 + 证据 + 分数）
#   RaterChainResult[]   -> FinalDecision    （两链比较：一致 or Rater3 仲裁后）
#
# 证据引用一律为 unit_ids（对 DataPackage.units 的编号引用），不复述原文，
# 消除 v1 EvidenceSpan 自由复述 + 模糊匹配带来的静默定位失败。
# ═══════════════════════════════════════════════════════════════════════════


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
    """单次评分（Rater 的 score 阶段，或 Rater3 仲裁）对一个二级指标的评分结果。

        属性：
            dimension_id: 来自评分量规配置的不透明二级指标标识符。
            score: 该二级指标的 ScoreRepresentation。
            supporting_unit_ids: 支持该评分的 Unit 编号引用。
            rationale: 评分理由（自由文本，用于审计追踪）。
            confidence: 评分者的置信度，范围 [0.0, 1.0]。"""

    dimension_id: str
    score: ScoreRepresentation
    supporting_unit_ids: List[int]
    rationale: str
    confidence: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"DimensionScore '{self.dimension_id}': confidence must be in "
                f"[0.0, 1.0], got {self.confidence}."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension_id": self.dimension_id,
            "score": self.score.to_dict(),
            "supporting_unit_ids": list(self.supporting_unit_ids),
            "rationale": self.rationale,
            "confidence": self.confidence,
        }


# ── RaterChainResult ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class RaterChainResult:
    """单个 Rater 对一个二级指标的完整链：选段 → 取证 → 评分。

        选段、证据引用与最终分数/rationale 绑定在同一结构里，构成可独立审计的一条链。

        属性：
            rater_id: 标识生成该链的评分代理（例如 "rater_1"）。
            dimension_id: 来自评分量规配置的不透明二级指标标识符。
            selected_unit_ids: select 阶段选出的相关 Unit 编号。
            evidence_unit_ids: extract 阶段引用为证据的 Unit 编号。
            score: score 阶段产出的 DimensionScore（含最终 rationale/confidence）。"""

    rater_id: str
    dimension_id: str
    selected_unit_ids: List[int]
    evidence_unit_ids: List[int]
    score: DimensionScore

    def __post_init__(self) -> None:
        if self.score.dimension_id != self.dimension_id:
            raise ValueError(
                f"RaterChainResult for rater '{self.rater_id}': dimension_id "
                f"'{self.dimension_id}' does not match score.dimension_id "
                f"'{self.score.dimension_id}'."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rater_id": self.rater_id,
            "dimension_id": self.dimension_id,
            "selected_unit_ids": list(self.selected_unit_ids),
            "evidence_unit_ids": list(self.evidence_unit_ids),
            "score": self.score.to_dict(),
        }


# ── FinalDecision ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FinalDecision:
    """一个二级指标的最终权威评分决定（双链一致，或经 Rater3 仲裁）。

        属性：
            dimension_id: 来自评分量规配置的不透明二级指标标识符。
            final_score: 该二级指标的权威 ScoreRepresentation。
            source: 该分数的来源 —— consensus（双链一致）或 adjudicated（Rater3 仲裁）。
            unit_ids: 支持最终决定的 Unit 编号引用。"""

    dimension_id: str
    final_score: ScoreRepresentation
    source: ScoreSource
    unit_ids: List[int]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension_id": self.dimension_id,
            "final_score": self.final_score.to_dict(),
            "source": self.source.value,
            "unit_ids": list(self.unit_ids),
        }
