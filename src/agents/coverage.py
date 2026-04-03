"""
覆盖规划 Agent，负责为每个评分维度筛出需要重点查看的文本单元。

Coverage Planner.

Produces one CoveragePlan per rubric dimension.

Execution mode:
- LLM relevance pre-filter: per-dimension prompt selects top-k chunk IDs.
  Any per-dimension failure raises an error for explicit handling upstream.
"""

from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.request_models import CoveragePlan, NormalizedDocument
from src.policies.rubric_core import build_dimension_traversal
from src.providers.base import BaseProvider, LLMRequest
from src.providers.prompt_loader import PromptTemplate, render_template
from src.providers.structured_output import normalize_structured_output

_FIRST_SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s*")
_RELEVANCE_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["relevant_chunk_ids"],
    "properties": {
        "relevant_chunk_ids": {
            "type": "array",
            "items": {"type": "string"},
        }
    },
}


def _hid(seed: str, length: int = 12) -> str:
    return hashlib.md5(seed.encode()).hexdigest()[:length]


def _first_sentence(text: str) -> str:
    parts = [p.strip() for p in _FIRST_SENTENCE_RE.split(text) if p.strip()]
    if parts:
        return parts[0]
    return text.strip()[:200]


def _all_unit_ids(document: NormalizedDocument) -> List[str]:
    return [u.unit_id for u in document.text_units]


def _resolve_top_k(chunking_policy: Optional[dict], dimension_id: str) -> int:
    """Resolve per-dimension top-k from policy with safe defaults."""
    default_top_k = 5
    if not isinstance(chunking_policy, dict):
        return default_top_k

    # Accept both outer {"chunking_policy": {...}} and inner {"coverage": {...}}.
    if "chunking_policy" in chunking_policy and isinstance(chunking_policy["chunking_policy"], dict):
        chunking_policy = chunking_policy["chunking_policy"]

    coverage_cfg = chunking_policy.get("coverage", {})
    if not isinstance(coverage_cfg, dict):
        return default_top_k

    raw_default = coverage_cfg.get("default_top_k", default_top_k)
    if isinstance(raw_default, int) and raw_default > 0:
        default_top_k = raw_default

    per_dim = coverage_cfg.get("per_dimension_top_k", {})
    if not isinstance(per_dim, dict):
        return default_top_k

    raw_top_k = per_dim.get(dimension_id, default_top_k)
    if isinstance(raw_top_k, int) and raw_top_k > 0:
        return raw_top_k
    return default_top_k


def _build_chunk_summaries(document: NormalizedDocument) -> List[Dict[str, str]]:
    summaries: List[Dict[str, str]] = []
    for unit in document.text_units:
        summaries.append(
            {
                "id": unit.unit_id,
                "title": unit.chunk_title or f"Chunk {unit.sequence_index + 1}",
                "first_sentence": _first_sentence(unit.text),
                "source_type": unit.source_type,
                "source_label": unit.source_label or "",
            }
        )
    return summaries


def _parse_relevant_ids(data: Dict[str, Any]) -> List[str]:
    raw = data.get("relevant_chunk_ids")
    if not isinstance(raw, list):
        raise ValueError("Missing or invalid relevant_chunk_ids")
    parsed: List[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("relevant_chunk_ids must be a list of strings")
        cid = item.strip()
        if cid and cid not in parsed:
            parsed.append(cid)
    return parsed


def _llm_plan_for_dimension(
    *,
    document: NormalizedDocument,
    traversal: Any,
    provider: BaseProvider,
    template: PromptTemplate,
    chunking_policy: Optional[dict],
    chunk_summaries: List[Dict[str, str]],
    rubric: RubricSnapshot,
) -> CoveragePlan:
    dim = traversal.dimension_id
    minimum_evidence_units = traversal.evidence_requirements.get("minimum_evidence_units", 1)
    top_k = _resolve_top_k(chunking_policy, dim)
    all_unit_ids = _all_unit_ids(document)
    allowed = set(all_unit_ids)

    dim_cfg = rubric.dimension_by_id.get(dim, {})
    context = {
        "dimension_name": traversal.name or traversal.dimension_id,
        "dimension_description": dim_cfg.get("description", ""),
        "required_facets": list(traversal.required_facets),
        "chunks": chunk_summaries,
        "top_k": top_k,
        # Reserved optional hook for per-dimension prompt override.
        "facet_emphasis_hint": "",
    }

    prompt = render_template(template, context)
    response = provider.complete(
        LLMRequest(
            prompt=prompt,
            output_schema=_RELEVANCE_OUTPUT_SCHEMA,
            metadata={
                "node_id": "node_coverage",
                "stage_name": "coverage_planning",
                "dimension_id": dim,
                "document_id": document.document_id,
                "template_source": template.source_path,
                "template_version": template.metadata.get("template_version"),
                "top_k": top_k,
                "chunk_candidate_count": len(chunk_summaries),
            },
        )
    )
    data = response.structured_data
    if data is None:
        data = normalize_structured_output(response.content, schema=_RELEVANCE_OUTPUT_SCHEMA)
    ranked_ids = _parse_relevant_ids(data)

    filtered: List[str] = []
    for cid in ranked_ids:
        if cid in allowed:
            filtered.append(cid)
        if len(filtered) >= top_k:
            break

    if not filtered:
        raise ValueError("No valid relevant chunk ids after filtering")

    relevance_scores = {cid: 1.0 / float(rank + 1) for rank, cid in enumerate(filtered)}
    return CoveragePlan(
        plan_id=f"plan-{_hid(f'{document.document_id}:{dim}')}",
        document_id=document.document_id,
        dimension_id=dim,
        target_unit_ids=filtered,
        required_facets=list(traversal.required_facets),
        minimum_evidence_units=minimum_evidence_units,
        allowed_evidence_scopes=["span", "global"],
        coverage_strategy="targeted",
        relevance_scores=relevance_scores,
    )


def run(
    document: NormalizedDocument,
    rubric: RubricSnapshot,
    provider: BaseProvider,
    template: PromptTemplate,
    chunking_policy: Optional[dict] = None,
) -> List[CoveragePlan]:
    """Generate one CoveragePlan per dimension in the rubric.

    Dimension iteration and facet resolution are handled by
    ``rubric_core.build_dimension_traversal`` — no raw dict access here.

    Args:
        document: The segmented NormalizedDocument to plan coverage for.
        rubric: RubricSnapshot supplying dimension definitions and required facets.
        provider: Provider for LLM relevance pre-filtering.
        template: Dimension relevance prompt template.
        chunking_policy: Optional chunking policy dict containing per-dimension top_k.

    Returns:
        List of CoveragePlan objects, one per rubric dimension.
    """
    traversals = build_dimension_traversal(rubric)
    chunk_summaries = _build_chunk_summaries(document)
    plan_by_dim: Dict[str, CoveragePlan] = {}
    max_workers = max(1, min(6, len(traversals)))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _llm_plan_for_dimension,
                document=document,
                traversal=trav,
                provider=provider,
                template=template,
                chunking_policy=chunking_policy,
                chunk_summaries=chunk_summaries,
                rubric=rubric,
            ): trav
            for trav in traversals
        }
        for fut in as_completed(futures):
            trav = futures[fut]
            try:
                plan = fut.result()
            except Exception as exc:
                raise RuntimeError(
                    f"Coverage planning failed for dimension '{trav.dimension_id}': {exc}"
                ) from exc
            plan_by_dim[trav.dimension_id] = plan

    # Preserve rubric-defined dimension order.
    return [plan_by_dim[trav.dimension_id] for trav in traversals]
