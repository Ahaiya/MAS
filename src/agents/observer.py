"""
观察构建 Agent，负责把证据片段聚合成维度级观察摘要。

Observation Builder — 确定性的 DimensionObservation 组装。

按 facet ID 和支持极性对 EvidenceSpans 进行分组，然后从 CoveragePlan 中为每个必需的 facet 构建一个 FacetFinding。

观察置信度源自 facet 证据覆盖率：
  - 所有必需的 facet 都有证据  -> HIGH
  - 部分必需的 facet 有证据 -> MEDIUM
  - 没有必需的 facet 有证据   -> LOW

观察 ID 源自 plan ID 和 span ID（确定性）。"""

from __future__ import annotations

import hashlib
from typing import List

from src.contracts.evidence import (
    DimensionObservation,
    EvidenceSpan,
    FacetFinding,
    ObservationConfidence,
)
from src.contracts.request_models import CoveragePlan


def _hid(seed: str, length: int = 12) -> str:
    return hashlib.md5(seed.encode()).hexdigest()[:length]


def _dedupe_preserve(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def run(spans: List[EvidenceSpan], plan: CoveragePlan) -> DimensionObservation:
    """根据证据 span 和覆盖计划构建 DimensionObservation。
    
        Args:
            spans: 为此维度提取的 EvidenceSpans。
            plan: 定义必需 facet 的 CoveragePlan。
    
        Returns:
            一个为每个必需 facet 包含 FacetFindings 的 DimensionObservation。"""
    target_unit_ids = set(plan.target_unit_ids)
    coverage_miss_span_ids = [
        span.span_id
        for span in spans
        if span.unit_id is not None
        and target_unit_ids
        and span.unit_id not in target_unit_ids
    ]

    supporting_span_ids: List[str] = []
    counter_span_ids: List[str] = []
    facet_findings: List[FacetFinding] = []
    uncertainty_notes: List[str] = []

    for facet_id in plan.required_facets:
        matching = [span for span in spans if facet_id in span.facet_ids]
        supporting = [
            span.span_id
            for span in matching
            if span.support_type in ("supporting", "neutral")
        ]
        counter = [
            span.span_id
            for span in matching
            if span.support_type == "counter"
        ]

        facet_findings.append(
            FacetFinding(
                facet_id=facet_id,
                supporting_span_ids=supporting,
                counter_span_ids=counter,
                finding_note=f"{len(supporting)} supporting, {len(counter)} counter",
            )
        )
        supporting_span_ids.extend(supporting)
        counter_span_ids.extend(counter)

        if not supporting and not counter:
            uncertainty_notes.append(f"facet '{facet_id}' has no evidence")

    # 根据 facet 覆盖率确定置信度
    covered = sum(
        1
        for ff in facet_findings
        if ff.supporting_span_ids or ff.counter_span_ids
    )
    total = len(plan.required_facets)
    if total == 0 or covered == total:
        confidence = ObservationConfidence.HIGH
    elif covered > 0:
        confidence = ObservationConfidence.MEDIUM
    else:
        confidence = ObservationConfidence.LOW

    if confidence == ObservationConfidence.LOW:
        uncertainty_notes.append("observation has significant coverage gaps")

    # 根据 plan + 排序后的 span ID 生成确定性的 observation_id
    span_seed = ":".join(sorted(s.span_id for s in spans))
    observation_id = f"obs-{_hid(f'{plan.plan_id}:{span_seed}')}"

    return DimensionObservation(
        observation_id=observation_id,
        document_id=plan.document_id,
        dimension_id=plan.dimension_id,
        supporting_span_ids=_dedupe_preserve(supporting_span_ids),
        counter_span_ids=_dedupe_preserve(counter_span_ids),
        facet_findings=facet_findings,
        observation_confidence=confidence,
        uncertainty_notes=uncertainty_notes,
        coverage_miss_span_ids=coverage_miss_span_ids,
    )
