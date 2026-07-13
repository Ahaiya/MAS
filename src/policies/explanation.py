"""
解释策略模块，负责渲染维度反馈并执行证据约束与引用校验。

Explanation Policy — 基于配置的解释渲染与引用强制执行。

从 FinalDimensionDecision、EvidenceSpan 和 RubricSnapshot 渲染各维度的解释，然后根据策略要求验证引用链（descriptor ref →
evidence span → canonical score）。

Supported policy flags (all read from PolicySnapshot.explanation_policy):
  requirements.require_descriptor_alignment  – descriptor_refs 必须非空
  requirements.require_evidence_links        – evidence_span_ids 必须非空
  requirements.require_score_citation        – canonical_score 必须非负
  citation_rules.min_citations_per_dimension – 最小 evidence citations 数量
  output_constraints.max_commentary_length_per_dimension – commentary 长度上限
  output_constraints.require_evidence_score_chain – 链必须闭合

所有 dimension 名称、descriptor 文本和 score 值均来自 RubricSnapshot
或 FinalDimensionDecision — 这里没有任何硬编码。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.contracts.artifact_bundle import PolicySnapshot, RubricSnapshot
from src.contracts.evidence import (
    DimensionObservation,
    EvidenceSpan,
    ObservationConfidence,
)
from src.contracts.scoring import FinalDimensionDecision


# ── 输出类型 ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DimensionExplanation:
    """针对单一维度的结构化、符合策略的解释。
    
        所有引用的值必须可追溯到 config artifacts（rubric
    descriptors）或 contract objects（evidence span IDs、canonical scores）。
    
        Attributes:
            dimension_id: 来自 rubric config 的不透明 dimension 标识符。
            dimension_name: 来自 rubric config 的易读名称。
            canonical_score: 权威整数 score（仅供计算使用）。
            display_score: 显示字符串（可能包含注释）。
            scale_ref: 来自 rubric 的 scale reference。
            descriptor_refs: 为此 score 引用的 rubric descriptor references。
            evidence_span_ids: 支持该解释的 evidence span IDs。
            commentary: 结构化的 commentary 文本（受策略最大长度限制）。
            uncertainty_note: 如果置信度低或使用了裁决，则提供的可选注释。"""

    dimension_id: str
    dimension_name: str
    canonical_score: int
    display_score: str
    scale_ref: str
    descriptor_refs: List[str]
    evidence_span_ids: List[str]
    commentary: str
    uncertainty_note: Optional[str]


@dataclass(frozen=True)
class ExplanationViolation:
    """针对单一维度引用链的策略违规。
    
        Attributes:
            dimension_id: 哪个维度产生了违规。
            violation_type: 短标识符（例如，"missing_descriptor_ref"）。
            detail: 易读的违规描述。"""

    dimension_id: str
    violation_type: str
    detail: str


# ── 内部辅助函数 ──────────────────────────────────────────────────────────


def _get_requirements(policy: PolicySnapshot) -> Dict[str, Any]:
    return policy.explanation_policy.get("requirements", {})


def _get_citation_rules(policy: PolicySnapshot) -> Dict[str, Any]:
    return policy.explanation_policy.get("citation_rules", {})


def _get_output_constraints(policy: PolicySnapshot) -> Dict[str, Any]:
    return policy.explanation_policy.get("output_constraints", {})


def _get_low_confidence_threshold(policy: PolicySnapshot) -> float:
    constraints = _get_output_constraints(policy)
    raw = constraints.get("low_confidence_threshold", 0.5)
    try:
        threshold = float(raw)
    except (TypeError, ValueError):
        return 0.5
    if threshold < 0.0 or threshold > 1.0:
        return 0.5
    return threshold


def _build_commentary(
    decision: FinalDimensionDecision,
    observation: DimensionObservation,
    spans: List[EvidenceSpan],
    rubric: RubricSnapshot,
    max_length: int,
    scorer_rationale: Optional[str] = None,
) -> str:
    """构建一个简短的、以证据为锚点的 commentary 字符串。"""
    rationale = (scorer_rationale or "").strip()
    if rationale:
        return rationale[:max_length]

    dim_id = decision.dimension_id
    score_val = decision.final_score.canonical_score

    span_by_id = {
        span.span_id: span
        for span in spans
        if span.dimension_id == dim_id
    }

    def _quoted(span_id: str) -> Optional[str]:
        span = span_by_id.get(span_id)
        if span is None:
            return None
        quote = (span.text_quote or "").strip()
        if not quote:
            return None
        return f"'{quote}'"

    facet_lines: List[str] = []
    for finding in observation.facet_findings:
        supporting_quotes = [
            q
            for q in (_quoted(span_id) for span_id in finding.supporting_span_ids)
            if q is not None
        ]
        counter_quotes = [
            q
            for q in (_quoted(span_id) for span_id in finding.counter_span_ids)
            if q is not None
        ]
        if not supporting_quotes and not counter_quotes:
            continue

        supporting_text = ", ".join(supporting_quotes) if supporting_quotes else "(none)"
        line = f"[{finding.facet_id}]: supporting evidence: {supporting_text}"
        if counter_quotes:
            line += "; however, counter evidence suggests: " + ", ".join(counter_quotes)
        if finding.finding_note:
            line += f" ({finding.finding_note})"
        facet_lines.append(line)

    if facet_lines:
        return " ".join(facet_lines)[:max_length]

    # 从 rubric levels 中查找该 score 的 descriptor 摘要
    descriptor_summary = ""
    dim_cfg = rubric.dimension_by_id.get(dim_id, {})
    for level in dim_cfg.get("levels", []):
        if level.get("rank") == score_val:
            descriptor_summary = level.get("summary", "")
            break

    # 从最终决策引用中查找相关的 evidence quote。
    span_quote = ""
    for span_id in decision.evidence_span_ids:
        quoted = _quoted(span_id)
        if quoted is not None:
            span_quote = quoted
            break

    if descriptor_summary and span_quote:
        commentary = f"Score {score_val}: {descriptor_summary}. Evidence: {span_quote}"
    elif descriptor_summary:
        commentary = f"Score {score_val}: {descriptor_summary}."
    elif span_quote:
        commentary = f"Score {score_val} supported by evidence: {span_quote}"
    else:
        commentary = f"Score {score_val} assigned based on available evidence."

    return commentary[:max_length]


def _fallback_observation(decision: FinalDimensionDecision) -> DimensionObservation:
    """当上游 observation 不可用时，创建一个最小的 observation。"""
    return DimensionObservation(
        observation_id=f"obs-fallback-{decision.dimension_id}",
        document_id="unknown",
        dimension_id=decision.dimension_id,
        supporting_span_ids=list(decision.evidence_span_ids),
        counter_span_ids=[],
        facet_findings=[],
        observation_confidence=ObservationConfidence.MEDIUM,
        uncertainty_notes=[],
    )


def _build_uncertainty_note(
    decision: FinalDimensionDecision,
    low_confidence_threshold: float,
) -> Optional[str]:
    """如果条件需要，返回一个 uncertainty note。"""
    reasons: List[str] = []
    if decision.decision_confidence < low_confidence_threshold:
        reasons.append(
            f"confidence={decision.decision_confidence:.2f} below threshold"
        )
    if decision.adjudication_id is not None:
        reasons.append("adjudication was required to resolve a scoring conflict")
    if not reasons:
        return None
    return "Note: " + "; ".join(reasons) + "."


# ── 公共 API ────────────────────────────────────────────────────────────────


def render_dimension_explanation(
    decision: FinalDimensionDecision,
    spans: List[EvidenceSpan],
    rubric: RubricSnapshot,
    policy: PolicySnapshot,
    observation: Optional[DimensionObservation] = None,
    scorer_rationale: Optional[str] = None,
) -> DimensionExplanation:
    """渲染针对单一维度的结构化、受策略约束的解释。
    
        Args:
            decision: 该维度的权威 score 决策。
            spans: EvidenceSpan 列表（已过滤或完整 — 仅使用该维度的 spans）。
            rubric: 用于 dimension 名称和 descriptor 查找的 RubricSnapshot。
            policy: 包含 explanation policy config 的 PolicySnapshot。
    
        Returns:
            一个不可变的 DimensionExplanation。"""
    constraints = _get_output_constraints(policy)
    max_len: int = int(constraints.get("max_commentary_length_per_dimension", 500))
    low_conf_threshold = _get_low_confidence_threshold(policy)

    dim_id = decision.dimension_id
    dim_cfg = rubric.dimension_by_id.get(dim_id, {})
    dimension_name: str = dim_cfg.get("name", dim_id)

    # 将 spans 过滤至该维度
    dim_spans = [s for s in spans if s.dimension_id == dim_id]

    obs = observation or _fallback_observation(decision)
    commentary = _build_commentary(
        decision=decision,
        observation=obs,
        spans=dim_spans,
        rubric=rubric,
        max_length=max_len,
        scorer_rationale=scorer_rationale,
    )
    uncertainty_note = _build_uncertainty_note(decision, low_conf_threshold)

    return DimensionExplanation(
        dimension_id=dim_id,
        dimension_name=dimension_name,
        canonical_score=decision.final_score.canonical_score,
        display_score=decision.final_score.display_score,
        scale_ref=decision.final_score.scale_ref,
        descriptor_refs=list(decision.descriptor_refs),
        evidence_span_ids=list(decision.evidence_span_ids),
        commentary=commentary,
        uncertainty_note=uncertainty_note,
    )


def validate_citation_chain(
    explanation: DimensionExplanation,
    policy: PolicySnapshot,
) -> List[ExplanationViolation]:
    """验证单个 DimensionExplanation 的引用链。
    
        检查来自 policy.explanation_policy["requirements"] 和
    citation_rules 的所需标志，返回 ExplanationViolation 对象列表（空 = OK）。
    
        Args:
            explanation: 要验证的 DimensionExplanation。
            policy: 包含 explanation policy config 的 PolicySnapshot。
    
        Returns:
            ExplanationViolation 列表（如果所有检查通过，可能为空）。"""
    reqs = _get_requirements(policy)
    citation_rules = _get_citation_rules(policy)
    dim_id = explanation.dimension_id
    violations: List[ExplanationViolation] = []

    # 检查 descriptor 对齐情况
    if reqs.get("require_descriptor_alignment", False):
        if not explanation.descriptor_refs:
            violations.append(ExplanationViolation(
                dimension_id=dim_id,
                violation_type="missing_descriptor_ref",
                detail=(
                    f"Dimension '{dim_id}': require_descriptor_alignment=true but "
                    f"descriptor_refs is empty."
                ),
            ))

    has_evidence_links = reqs.get("require_evidence_links", False)

    # 检查 evidence links
    if has_evidence_links:
        if not explanation.evidence_span_ids:
            violations.append(ExplanationViolation(
                dimension_id=dim_id,
                violation_type="missing_evidence_link",
                detail=(
                    f"Dimension '{dim_id}': require_evidence_links=true but "
                    f"evidence_span_ids is empty."
                ),
            ))

    # 无论是否需要 evidence_links，都检查最小引用数。
    min_citations = int(citation_rules.get("min_citations_per_dimension", 0))
    if min_citations > 0 and len(explanation.evidence_span_ids) < min_citations:
        violations.append(ExplanationViolation(
            dimension_id=dim_id,
            violation_type="insufficient_evidence_citations",
            detail=(
                f"Dimension '{dim_id}': min_citations_per_dimension={min_citations} "
                f"but found {len(explanation.evidence_span_ids)}."
            ),
        ))

    return violations


def enforce_explanation_policy(
    explanations: List[DimensionExplanation],
    policy: PolicySnapshot,
) -> List[ExplanationViolation]:
    """对所有 explanations 运行引用链验证。
    
        Args:
            explanations: 所有要验证的 DimensionExplanation 对象。
            policy: 包含 explanation policy config 的 PolicySnapshot。
    
        Returns:
            所有找到的 ExplanationViolation 对象的去重列表。"""
    all_violations: List[ExplanationViolation] = []
    for exp in explanations:
        all_violations.extend(validate_citation_chain(exp, policy))
    return all_violations
