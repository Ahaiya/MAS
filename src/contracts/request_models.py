"""
请求契约，定义原始输入、标准化文档与文本单元的核心数据结构。

Request Normalization and Text Segmentation Contracts

定义了评估请求流水线的数据结构：
  EvaluationRequest -> NormalizedRequest -> NormalizedDocument (with TextUnit[])
  NormalizedDocument + RubricSnapshot -> CoveragePlan[]

设计不变量：
- 所有模型均为冻结（不可变）的 dataclasses。
- 此处不对维度名称、特征代码、刻度范围或分面名称进行硬编码。所有此类值均是从 rubric config 制品流入的不透明字符串。
- to_dict() 生成普通的 JSON 安全字典；datetime 序列化为 ISO 8601 格式。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


# ── TextUnit ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TextUnit:
    """NormalizedDocument 内的连续文本切片。
    
        偏移量是字符级别的，从 0 开始索引，半开区间 [start_offset, end_offset)。
    
        Attributes:
            unit_id: 文档内的唯一标识符。
            document_id: 父文档引用。
            text: 此单元的实际文本内容。
            start_offset: 在完整文档文本中包含的起始字符偏移量。
            end_offset: 不包含的结束字符偏移量。
            unit_type: 语义类型 — 例如 "sentence"、"paragraph"、"full_document"。
            sequence_index: 此单元在文档顺序中从 0 开始的位置。
            chunk_title: 当分块由 LLM chunker 生成时的可选语义标题。
            chunk_method: 分块方法标记（"rule"、"llm_semantic"、"llm_hierarchical"）。
            source_type: 当文档是对话日志时此单元的来源分类（"human"、"ai"、"system"、"mixed"、"unknown"）。
            source_label: 可选的原始来源标签（例如 "human_input"）。"""

    unit_id: str
    document_id: str
    text: str
    start_offset: int
    end_offset: int
    unit_type: str
    sequence_index: int
    chunk_title: Optional[str] = None
    chunk_method: str = "rule"
    source_type: str = "unknown"
    source_label: Optional[str] = None

    def __post_init__(self) -> None:
        if self.end_offset <= self.start_offset:
            raise ValueError(
                f"TextUnit '{self.unit_id}': end_offset ({self.end_offset}) "
                f"must be > start_offset ({self.start_offset})"
            )
        if self.source_type not in {"human", "ai", "system", "mixed", "unknown"}:
            raise ValueError(
                f"TextUnit '{self.unit_id}': invalid source_type '{self.source_type}'."
            )

    def span_length(self) -> int:
        """此单元覆盖的字符数。"""
        return self.end_offset - self.start_offset

    def to_dict(self) -> Dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "document_id": self.document_id,
            "text": self.text,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "unit_type": self.unit_type,
            "sequence_index": self.sequence_index,
            "chunk_title": self.chunk_title,
            "chunk_method": self.chunk_method,
            "source_type": self.source_type,
            "source_label": self.source_label,
        }



# ── EvaluationRequest ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvaluationRequest:
    """原始入站评估请求 — 系统边界入口点。
    
        Attributes:
            raw_text: 要评估的学生作文或文本。必须非空。
            bundle_ref: 指向要使用的 ArtifactBundle 的 URI 样式引用。
            request_id: 调用方提供的 ID。None 表示流水线将生成一个。
            metadata: 任意调用方元数据（例如 essay_id、source、session_id）。"""

    raw_text: str
    bundle_ref: str
    request_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_text": self.raw_text,
            "bundle_ref": self.bundle_ref,
            "request_id": self.request_id,
            "metadata": dict(self.metadata),
        }



# ── NormalizedRequest ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NormalizedRequest:
    """输入标准化后的 EvaluationRequest。
    
        预处理节点将 EvaluationRequest 转换为 NormalizedRequest，
        记录应用了哪些转换，以便运行可审计。
    
        Attributes:
            request_id: 权威 ID（如果原始请求中没有，则由流水线生成）。
            raw_text: 原始文本，原样保留以供审计追踪。
            bundle_ref: 从 EvaluationRequest 转发而来。
            normalized_at: 标准化的 UTC 时间戳。
            normalization_notes: 已应用的标准化步骤列表（例如 "strip_bom"）。
            metadata: 转发的调用方元数据以及流水线添加的任何字段。"""

    request_id: str
    raw_text: str
    bundle_ref: str
    normalized_at: datetime
    normalization_notes: List[str]
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "raw_text": self.raw_text,
            "bundle_ref": self.bundle_ref,
            "normalized_at": self.normalized_at.isoformat(),
            "normalization_notes": list(self.normalization_notes),
            "metadata": dict(self.metadata),
        }



# ── NormalizedDocument ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NormalizedDocument:
    """准备好进行证据提取的标准化、分段的文档。
    
        由文本预处理节点从 NormalizedRequest 生成。
    
        Attributes:
            document_id: 唯一文档标识符（可能等于 request_id）。
            request_id: 链接回原始的 NormalizedRequest。
            normalized_text: 完整的标准化文本（用于所有偏移量的规范形式）。
            text_units: TextUnit 切片的有序列表（句子、段落等）。
            char_count: normalized_text 的总字符数。
            word_count: 大致字数统计（按空格分词）。
            document_metadata: 流水线生成的文档元数据。
            document_type: 可选的高级类型提示（"essay"、"report"、"dialogue"、"unknown"）。
            token_estimate: 用于长文档处理中分支选择的大致 token 计数提示。"""

    document_id: str
    request_id: str
    normalized_text: str
    text_units: List[TextUnit]
    char_count: int
    word_count: int
    document_metadata: Dict[str, Any]
    document_type: str = "unknown"
    token_estimate: int = 0

    def get_unit(self, unit_id: str) -> Optional[TextUnit]:
        """通过 unit_id 查找 TextUnit。如果未找到，则返回 None。"""
        for u in self.text_units:
            if u.unit_id == unit_id:
                return u
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "request_id": self.request_id,
            "normalized_text": self.normalized_text,
            "text_units": [u.to_dict() for u in self.text_units],
            "char_count": self.char_count,
            "word_count": self.word_count,
            "document_metadata": dict(self.document_metadata),
            "document_type": self.document_type,
            "token_estimate": self.token_estimate,
        }



# ── CoveragePlan ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CoveragePlan:
    """由 Coverage Planner 生成的按维度的提取计划。
    
        指定要搜索哪些文本单元、要满足哪些分面，以及
        需要多少证据单元 — 所有值均来源于 rubric config 制品，从不硬编码。
    
        Attributes:
            plan_id: 此计划的唯一标识符。
            document_id: 此计划适用的 NormalizedDocument。
            dimension_id: 来自 rubric config 的不透明维度标识符。绝不能假定其等于任何硬编码的特征名称。
            target_unit_ids: 提取工作线程应扫描的 TextUnit ID。
            required_facets: 观察必须覆盖的分面 ID（来自 rubric config）。
            minimum_evidence_units: 所需的最小证据跨度数（来自 rubric config）。
            allowed_evidence_scopes: 有效的范围类型（例如 ["span", "global"]）。
            coverage_strategy: 提取策略提示（例如 "full_scan"、"targeted"）。
            relevance_scores: 来自基于 LLM 的覆盖规划的可选相关性分数映射（chunk/unit id -> score）；对于 full-scan 计划为空。"""

    plan_id: str
    document_id: str
    dimension_id: str
    target_unit_ids: List[str]
    required_facets: List[str]
    minimum_evidence_units: int
    allowed_evidence_scopes: List[str]
    coverage_strategy: str
    relevance_scores: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "document_id": self.document_id,
            "dimension_id": self.dimension_id,
            "target_unit_ids": list(self.target_unit_ids),
            "required_facets": list(self.required_facets),
            "minimum_evidence_units": self.minimum_evidence_units,
            "allowed_evidence_scopes": list(self.allowed_evidence_scopes),
            "coverage_strategy": self.coverage_strategy,
            "relevance_scores": dict(self.relevance_scores),
        }
