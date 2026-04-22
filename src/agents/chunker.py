"""
文本切分 Agent，负责把原始作文整理成后续可消费的分段文本单元。

LLM-assisted document chunker.

Converts EvaluationRequest -> (NormalizedRequest, NormalizedDocument), using:
- short-document semantic chunking (single LLM call),
- long-document hierarchical chunking (hard split + summary + LLM call),
- strict LLM execution with explicit failure on chunking errors.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.contracts.request_models import (
    EvaluationRequest,
    NormalizedDocument,
    NormalizedRequest,
    TextUnit,
)
from src.providers.base import BaseProvider, LLMRequest
from src.providers.prompt_loader import PromptTemplate, render_template
from src.providers.structured_output import normalize_structured_output
from src.utils.dialogue_sources import extract_dialogue_source_spans, source_for_range

_CHINESE_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")
_NON_WS_RE = re.compile(r"\S")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s*")
_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["chunks"],
    "properties": {
        "chunks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "title", "text"],
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                },
            },
        }
    },
}


@dataclass(frozen=True)
class _HardChunk:
    idx: int
    start: int
    end: int
    text: str
    summary: str


def _hid(seed: str, length: int = 12) -> str:
    return hashlib.md5(seed.encode()).hexdigest()[:length]


def _resolve_document_type(request: EvaluationRequest) -> str:
    raw = request.metadata.get("document_type") if isinstance(request.metadata, dict) else None
    if not isinstance(raw, str):
        return "unknown"
    normalized = raw.strip().lower()
    if normalized in {"essay", "report", "dialogue", "unknown"}:
        return normalized
    return "unknown"


def _chinese_ratio(text: str) -> float:
    non_ws = len(_NON_WS_RE.findall(text))
    if non_ws == 0:
        return 0.0
    zh = len(_CHINESE_CHAR_RE.findall(text))
    return zh / non_ws


def _estimate_tokens(text: str) -> int:
    """Approximate token count by language profile (no tokenizer dependency)."""
    if not text:
        return 0
    if _chinese_ratio(text) >= 0.2:
        return int(math.ceil(len(text) * 1.5))
    return int(math.ceil(len(text.split()) * 1.3))


def _build_normalized_request(request: EvaluationRequest, normalized_text: str) -> NormalizedRequest:
    request_id = request.request_id or f"req-{_hid(normalized_text)}"
    return NormalizedRequest(
        request_id=request_id,
        raw_text=request.raw_text,
        bundle_ref=request.bundle_ref,
        normalized_at=datetime.now(timezone.utc),
        normalization_notes=["strip_whitespace"],
        metadata=dict(request.metadata),
    )


def _parse_chunks(data: Dict[str, Any]) -> List[Dict[str, str]]:
    chunks = data.get("chunks")
    if not isinstance(chunks, list):
        raise ValueError("Missing or invalid 'chunks' list")
    parsed: List[Dict[str, str]] = []
    for item in chunks:
        if not isinstance(item, dict):
            raise ValueError("Chunk item must be an object")
        chunk_id = item.get("id")
        title = item.get("title")
        text = item.get("text")
        if not isinstance(chunk_id, str) or not chunk_id.strip():
            raise ValueError("Chunk id must be a non-empty string")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Chunk title must be a non-empty string")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Chunk text must be a non-empty string")
        parsed.append({"id": chunk_id.strip(), "title": title.strip(), "text": text})
    if not parsed:
        raise ValueError("No chunks returned")
    return parsed


def _response_to_data(response_content: str, response_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(response_data, dict):
        return response_data
    return normalize_structured_output(response_content, schema=_OUTPUT_SCHEMA)


def _create_llm_units_from_text_match(
    normalized_text: str,
    document_id: str,
    chunks: List[Dict[str, str]],
    *,
    method: str,
) -> List[TextUnit]:
    units: List[TextUnit] = []
    cursor = 0
    for i, chunk in enumerate(chunks):
        chunk_text = chunk["text"]
        start = normalized_text.find(chunk_text, cursor)
        if start == -1:
            # Retry with stripped text for providers that trim chunk edges.
            stripped = chunk_text.strip()
            if stripped:
                start = normalized_text.find(stripped, cursor)
                chunk_text = stripped if start != -1 else chunk_text
        if start == -1:
            raise ValueError(f"Cannot align chunk '{chunk['id']}' to source text")
        end = start + len(chunk_text)
        unit_id = f"unit-{_hid(f'{document_id}:{i}:{chunk_text[:30]}')}"
        units.append(
            TextUnit(
                unit_id=unit_id,
                document_id=document_id,
                text=chunk_text,
                start_offset=start,
                end_offset=end,
                unit_type="chunk",
                sequence_index=i,
                chunk_title=chunk["title"],
                chunk_method=method,
            )
        )
        cursor = end
    return units


def _first_two_sentences(text: str) -> str:
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text) if p.strip()]
    if not parts:
        return text.strip()[:240]
    return " ".join(parts[:2])


def _hard_split_by_words(normalized_text: str, max_words_per_chunk: int = 1500) -> List[_HardChunk]:
    token_matches = list(re.finditer(r"\S+", normalized_text))
    if not token_matches:
        return []

    chunks: List[_HardChunk] = []
    idx = 0
    for start_idx in range(0, len(token_matches), max_words_per_chunk):
        end_idx_exclusive = min(start_idx + max_words_per_chunk, len(token_matches))
        start = token_matches[start_idx].start()
        if end_idx_exclusive < len(token_matches):
            end = token_matches[end_idx_exclusive].start()
        else:
            end = len(normalized_text)
        text = normalized_text[start:end]
        chunks.append(
            _HardChunk(
                idx=idx,
                start=start,
                end=end,
                text=text,
                summary=_first_two_sentences(text),
            )
        )
        idx += 1
    return chunks


def _resolve_material_strategy(
    document_type: str,
    chunking_policy: Optional[Dict[str, Any]],
    material_type: Optional[str],
) -> str:
    """Look up material strategy text from chunking policy.

    Uses ``material_type`` as primary key (from material_context.type),
    falls back to ``document_type`` (auto-detected), then falls back to
    the raw ``material_type or document_type`` string if no policy entry.
    """
    if not isinstance(chunking_policy, dict):
        return material_type or document_type
    doc_proc = chunking_policy.get("document_processing", {})
    if not isinstance(doc_proc, dict):
        return material_type or document_type
    strategies = doc_proc.get("material_strategies", {})
    if not isinstance(strategies, dict):
        return material_type or document_type
    # Try material_type first, then document_type
    for key in (material_type, document_type):
        if key and key in strategies:
            return str(strategies[key])
    return material_type or document_type


def _resolve_chunk_size_hint(chunking_policy: Optional[Dict[str, Any]]) -> str:
    """Read chunk_size_hint from chunking policy; returns empty string if absent."""
    if not isinstance(chunking_policy, dict):
        return ""
    doc_proc = chunking_policy.get("document_processing", {})
    if not isinstance(doc_proc, dict):
        return ""
    hint = doc_proc.get("chunk_size_hint", "")
    return str(hint) if hint else ""


def _render_chunking_prompt(
    template: PromptTemplate,
    *,
    material_strategy: str,
    chunk_size_hint: str,
    word_count: int,
    normalized_text: str,
    chunking_hints: str = "",
) -> str:
    return render_template(
        template,
        {
            "material_strategy": material_strategy,
            "chunk_size_hint": chunk_size_hint,
            "word_count": word_count,
            "normalized_text": normalized_text,
            "chunking_hints": chunking_hints,
        },
    )


def _summaries_text(hard_chunks: List[_HardChunk]) -> str:
    lines: List[str] = []
    for hc in hard_chunks:
        lines.append(f"- id: c{hc.idx}")
        lines.append(f"  summary: {hc.summary}")
    return "\n".join(lines)


def _titles_from_chunks(chunks: List[Dict[str, str]], total: int) -> Dict[int, str]:
    result: Dict[int, str] = {}
    for i, chunk in enumerate(chunks):
        cid = chunk["id"].strip().lower()
        index = None
        if cid.startswith("c") and cid[1:].isdigit():
            index = int(cid[1:])
        elif i < total:
            index = i
        if index is not None and 0 <= index < total and index not in result:
            result[index] = chunk["title"].strip()
    return result


def _create_hierarchical_units(
    normalized_text: str,
    document_id: str,
    hard_chunks: List[_HardChunk],
    title_by_idx: Dict[int, str],
) -> List[TextUnit]:
    units: List[TextUnit] = []
    for hc in hard_chunks:
        title = title_by_idx.get(hc.idx) or f"Chunk c{hc.idx}"
        chunk_text = normalized_text[hc.start:hc.end]
        unit_id = f"unit-{_hid(f'{document_id}:{hc.idx}:{chunk_text[:30]}')}"
        units.append(
            TextUnit(
                unit_id=unit_id,
                document_id=document_id,
                text=chunk_text,
                start_offset=hc.start,
                end_offset=hc.end,
                unit_type="chunk",
                sequence_index=hc.idx,
                chunk_title=title,
                chunk_method="llm_hierarchical",
            )
        )
    return units


def _build_document(
    *,
    norm_req: NormalizedRequest,
    normalized_text: str,
    text_units: List[TextUnit],
    document_type: str,
    token_estimate: int,
    method: str,
    source_spans: List[Dict[str, Any]],
) -> NormalizedDocument:
    document_id = f"doc-{_hid(normalized_text)}"
    return NormalizedDocument(
        document_id=document_id,
        request_id=norm_req.request_id,
        normalized_text=normalized_text,
        text_units=text_units,
        char_count=len(normalized_text),
        word_count=len(normalized_text.split()),
        document_metadata={
            "segmentation": "llm_chunking",
            "chunking": method,
            "unit_count": len(text_units),
            "source_spans": list(source_spans),
        },
        document_type=document_type,
        token_estimate=token_estimate,
    )


def _annotate_units_with_sources(
    text_units: List[TextUnit],
    source_spans: List[Dict[str, Any]],
) -> List[TextUnit]:
    if not source_spans:
        return text_units
    annotated: List[TextUnit] = []
    for unit in text_units:
        source_type, source_label = source_for_range(
            unit.start_offset,
            unit.end_offset,
            source_spans,
            allow_mixed=True,
        )
        annotated.append(
            replace(
                unit,
                source_type=source_type,
                source_label=source_label,
            )
        )
    return annotated


def run(
    request: EvaluationRequest,
    provider: BaseProvider,
    template: PromptTemplate,
    token_threshold: int = 4000,
    chunking_policy: Optional[Dict[str, Any]] = None,
    material_type: Optional[str] = None,
    chunking_hints: str = "",
) -> Tuple[NormalizedRequest, NormalizedDocument]:
    """
    LLM-assisted chunking entrypoint.

    Args:
        request: The evaluation request with raw text.
        provider: LLM provider for completion calls.
        template: Chunking prompt template.
        token_threshold: Token count above which hierarchical chunking is used.
        chunking_policy: Optional policy dict (inner ``chunking_policy`` section)
            providing ``material_strategies`` and ``chunk_size_hint``.
        material_type: Override for material type lookup (e.g. ``"conversation"``
            from ``material_context.type``).  Takes precedence over auto-detected
            ``document_type`` when looking up ``material_strategies``.
    """
    normalized_text = request.raw_text.strip()
    source_spans = extract_dialogue_source_spans(normalized_text)
    document_type = _resolve_document_type(request)
    if document_type == "unknown" and source_spans:
        document_type = "dialogue"
    token_estimate = _estimate_tokens(normalized_text)

    material_strategy = _resolve_material_strategy(document_type, chunking_policy, material_type)
    chunk_size_hint = _resolve_chunk_size_hint(chunking_policy)

    norm_req = _build_normalized_request(request, normalized_text)
    document_id = f"doc-{_hid(normalized_text)}"
    word_count = len(normalized_text.split())

    if token_estimate < token_threshold:
        try:
            prompt = _render_chunking_prompt(
                template,
                material_strategy=material_strategy,
                chunk_size_hint=chunk_size_hint,
                word_count=word_count,
                normalized_text=normalized_text,
                chunking_hints=chunking_hints,
            )
            response = provider.complete(
                LLMRequest(
                    prompt=prompt,
                    output_schema=_OUTPUT_SCHEMA,
                    metadata={
                        "node_id": "node_preprocess",
                        "stage_name": "chunking",
                        "document_type": document_type,
                        "token_estimate": token_estimate,
                        "token_threshold": token_threshold,
                        "chunking_mode": "semantic",
                        "template_source": template.source_path,
                        "template_version": template.metadata.get("template_version"),
                    },
                )
            )
            data = _response_to_data(response.content, response.structured_data)
            chunks = _parse_chunks(data)
            text_units = _create_llm_units_from_text_match(
                normalized_text, document_id, chunks, method="llm_semantic"
            )
            text_units = _annotate_units_with_sources(text_units, source_spans)
            doc = _build_document(
                norm_req=norm_req,
                normalized_text=normalized_text,
                text_units=text_units,
                document_type=document_type,
                token_estimate=token_estimate,
                method="llm_semantic",
                source_spans=source_spans,
            )
            return norm_req, doc
        except Exception as exc:
            raise RuntimeError(f"Semantic chunking failed: {exc}") from exc

    hard_chunks = _hard_split_by_words(normalized_text, max_words_per_chunk=1500)
    if not hard_chunks:
        raise ValueError("Hierarchical chunking failed: hard split produced no chunks")

    try:
        prompt = _render_chunking_prompt(
            template,
            material_strategy=material_strategy,
            chunk_size_hint=chunk_size_hint,
            word_count=word_count,
            normalized_text=_summaries_text(hard_chunks),
            chunking_hints=chunking_hints,
        )
        # 每个 chunk JSON 对象约 250 tokens，预留 512 token 余量，上限 8192
        output_max_tokens = min(8192, max(2048, len(hard_chunks) * 250 + 512))
        response = provider.complete(
            LLMRequest(
                prompt=prompt,
                output_schema=_OUTPUT_SCHEMA,
                params={"max_tokens": output_max_tokens},
                metadata={
                    "node_id": "node_preprocess",
                    "stage_name": "chunking",
                    "document_type": document_type,
                    "token_estimate": token_estimate,
                    "token_threshold": token_threshold,
                    "chunking_mode": "hierarchical",
                    "hard_chunk_count": len(hard_chunks),
                    "output_max_tokens": output_max_tokens,
                    "template_source": template.source_path,
                    "template_version": template.metadata.get("template_version"),
                },
            )
        )
        data = _response_to_data(response.content, response.structured_data)
        chunks = _parse_chunks(data)
        title_by_idx = _titles_from_chunks(chunks, total=len(hard_chunks))
        text_units = _create_hierarchical_units(
            normalized_text=normalized_text,
            document_id=document_id,
            hard_chunks=hard_chunks,
            title_by_idx=title_by_idx,
        )
        text_units = _annotate_units_with_sources(text_units, source_spans)
        doc = _build_document(
            norm_req=norm_req,
            normalized_text=normalized_text,
            text_units=text_units,
            document_type=document_type,
            token_estimate=token_estimate,
            method="llm_hierarchical",
            source_spans=source_spans,
        )
        return norm_req, doc
    except Exception as exc:
        raise RuntimeError(f"Hierarchical chunking failed: {exc}") from exc
