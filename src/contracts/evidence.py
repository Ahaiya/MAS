"""
证据与观察契约，定义抽取阶段和观察阶段之间传递的数据形状。

证据与观察契约

定义评估流水线中证据抽取阶段与观察构建阶段之间的中间数据形态：

  TextUnit[] + CoveragePlan[] -> EvidenceSpan[]
  EvidenceSpan[]              -> DimensionObservation[]

设计不变式：
- 所有模型均为冻结（不可变）的 dataclass。
- dimension_id 与 facet_ids 是来自 rubric 配置产物的透明字符串；此处不硬编码任何 trait 名称、代码或 scale 值。
- EvidenceScope 区分字符级片段证据与文档全局证据。
- SPAN 作用域片段必须携带有效的字符偏移；GLOBAL 作用域片段无需如此。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ── EvidenceScope ──────────────────────────────────────────────────────────────


class EvidenceScope(str, Enum):
    """EvidenceSpan 是锚定到字符范围，还是文档全局的。"""

    SPAN = "span"
    GLOBAL = "global"


# ── ObservationConfidence ──────────────────────────────────────────────────────


class ObservationConfidence(str, Enum):
    """
        DimensionObservation 的置信度。
    
        HIGH   – 所有必需的 facet 都有有力证据覆盖。
        MEDIUM – 大多数 facet 已覆盖；存在部分缺失或薄弱证据。
        LOW    – 覆盖缺口显著；建议进行裁决或重新抽取。
        """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ── EvidenceSpan ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvidenceSpan:
    """
        从文档中抽取的一条文本证据。
    
        抽取子代理生成 EvidenceSpan 对象；它们是 DimensionObservation、ScoreHypothesis、
        AdjudicationRecord 以及 Feedback assembler 引用的原子证据单元。
    
        属性：
            span_id: 该次运行中此 EvidenceSpan 的唯一标识符。
            document_id: 父 NormalizedDocument 引用。
            unit_id: 父 TextUnit 引用。GLOBAL 作用域的 EvidenceSpan 为 None。
            text_quote: 逐字文本摘录。GLOBAL 作用域的 EvidenceSpan 为 None。
            start_offset: 在 normalized_text 中的起始字符偏移（包含，仅 SPAN 作用域）。
            end_offset: 在 normalized_text 中的结束字符偏移（不包含，仅 SPAN 作用域）。
            scope: 字符锚定证据使用 SPAN；文档级证据使用 GLOBAL。
            dimension_id: 来自 rubric 配置的透明 dimension 标识符。
            facet_ids: 该 EvidenceSpan 相关的 rubric facets（来自配置的透明 ID）。
            extraction_note: 可选的抽取时注释（例如不确定性标记）。
            support_type: 该 EvidenceSpan 的支持极性：
                          "supporting" | "counter" | "neutral"。
            source_type: 引用文本的来源分类：
                         "human" | "ai" | "system" | "unknown"。
            source_label: 可选的原始来源标签（例如 "human_input"）。
        """

    span_id: str
    document_id: str
    unit_id: Optional[str]
    text_quote: Optional[str]
    start_offset: Optional[int]
    end_offset: Optional[int]
    scope: EvidenceScope
    dimension_id: str
    facet_ids: List[str]
    extraction_note: Optional[str]
    support_type: str = "supporting"
    source_type: str = "unknown"
    source_label: Optional[str] = None

    def __post_init__(self) -> None:
        if self.scope == EvidenceScope.SPAN:
            if self.start_offset is None or self.end_offset is None:
                raise ValueError(
                    f"EvidenceSpan '{self.span_id}': SPAN-scope spans must have "
                    f"start_offset and end_offset set."
                )
            if self.end_offset <= self.start_offset:
                raise ValueError(
                    f"EvidenceSpan '{self.span_id}': end_offset ({self.end_offset}) "
                    f"must be > start_offset ({self.start_offset})."
                )
        if self.support_type not in {"supporting", "counter", "neutral"}:
            raise ValueError(
                f"EvidenceSpan '{self.span_id}': support_type must be one of "
                "'supporting' | 'counter' | 'neutral'."
            )
        if self.source_type not in {"human", "ai", "system", "unknown"}:
            raise ValueError(
                f"EvidenceSpan '{self.span_id}': invalid source_type "
                f"'{self.source_type}'."
            )

    def span_length(self) -> Optional[int]:
        """该 EvidenceSpan 的字符长度；GLOBAL 作用域的 EvidenceSpan 为 None。"""
        if self.start_offset is not None and self.end_offset is not None:
            return self.end_offset - self.start_offset
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "span_id": self.span_id,
            "document_id": self.document_id,
            "unit_id": self.unit_id,
            "text_quote": self.text_quote,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "scope": self.scope.value,
            "dimension_id": self.dimension_id,
            "facet_ids": list(self.facet_ids),
            "extraction_note": self.extraction_note,
            "support_type": self.support_type,
            "source_type": self.source_type,
            "source_label": self.source_label,
        }



# ── FacetFinding ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FacetFinding:
    """
        一次观察中单个 rubric facet 的证据引用摘要。
    
        Observation Builder 按其所针对的 facet 对 EvidenceSpan ID 进行分组，
        为 dimension 的 observation_schema.required_facets 列表中的每个必需 facet
        生成一个 FacetFinding。
    
        属性：
            facet_id: 来自 rubric 配置的透明 facet 标识符（例如来自
                      dimension.observation_schema.required_facets）。
            supporting_span_ids: 正面支持该 facet 的 EvidenceSpan ID。
            counter_span_ids: 反驳或削弱该 facet 的 EvidenceSpan ID。
            finding_note: 关于该 facet 状态的可选人工/代理注释。
        """

    facet_id: str
    supporting_span_ids: List[str]
    counter_span_ids: List[str]
    finding_note: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "facet_id": self.facet_id,
            "supporting_span_ids": list(self.supporting_span_ids),
            "counter_span_ids": list(self.counter_span_ids),
            "finding_note": self.finding_note,
        }



# ── DimensionObservation ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class DimensionObservation:
    """
        单个 rubric dimension 的结构化观察。
    
        Observation Builder 每次评估运行都会为每个 dimension 生成一个
        DimensionObservation。它将 EvidenceSpan 聚合成支持/反对列表，按 facet 组织，
        并记录整体观察置信度。
    
        评分子代理接收 DimensionObservation（而非原始 EvidenceSpan），以确保评分依据
        锚定在有组织的证据上。
    
        属性：
            observation_id: 该观察的唯一 ID。
            document_id: 父 NormalizedDocument 引用。
            dimension_id: 来自 rubric 配置的透明 dimension 标识符。
            supporting_span_ids: 支持正面评估的 EvidenceSpan ID。
            counter_span_ids: 代表弱点或矛盾的 EvidenceSpan ID。
            facet_findings: 每个 facet 的证据分解（每个必需 facet 一个）。
            observation_confidence: 对该观察完整性的整体置信度。
            uncertainty_notes: 关于覆盖缺口或歧义的自由文本注释。
            coverage_miss_span_ids: 抽取得到的 EvidenceSpan ID，其 unit_id 不在该
                                    dimension 计划的目标单元范围内。
        """

    observation_id: str
    document_id: str
    dimension_id: str
    supporting_span_ids: List[str]
    counter_span_ids: List[str]
    facet_findings: List[FacetFinding]
    observation_confidence: ObservationConfidence
    uncertainty_notes: List[str]
    coverage_miss_span_ids: List[str] = field(default_factory=list)

    def get_facet_finding(self, facet_id: str) -> Optional[FacetFinding]:
        """返回给定 facet_id 对应的 FacetFinding；若不存在则返回 None。"""
        for ff in self.facet_findings:
            if ff.facet_id == facet_id:
                return ff
        return None

    def all_span_ids(self) -> List[str]:
        """返回 supporting 与 counter EvidenceSpan ID 的并集（已去重）。"""
        seen = set()
        result = []
        for sid in list(self.supporting_span_ids) + list(self.counter_span_ids):
            if sid not in seen:
                seen.add(sid)
                result.append(sid)
        return result

    def to_dict(self) -> Dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "document_id": self.document_id,
            "dimension_id": self.dimension_id,
            "supporting_span_ids": list(self.supporting_span_ids),
            "counter_span_ids": list(self.counter_span_ids),
            "facet_findings": [ff.to_dict() for ff in self.facet_findings],
            "observation_confidence": self.observation_confidence.value,
            "uncertainty_notes": list(self.uncertainty_notes),
            "coverage_miss_span_ids": list(self.coverage_miss_span_ids),
        }
