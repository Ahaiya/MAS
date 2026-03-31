"""
确定性切分 Agent，在 mock 路径下用规则方式生成文本分段。

Deterministic chunker.

Converts EvaluationRequest -> (NormalizedRequest, NormalizedDocument) with
rule-based sentence segmentation.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import List, Tuple

from src.contracts.request_models import (
    EvaluationRequest,
    NormalizedDocument,
    NormalizedRequest,
    TextUnit,
)


def _hid(seed: str, length: int = 12) -> str:
    """Compute a short deterministic hex ID from a string seed."""
    return hashlib.md5(seed.encode()).hexdigest()[:length]


def _segment_text(normalized_text: str, document_id: str) -> List[TextUnit]:
    """Split text into sentence-level TextUnits with character offsets."""
    pattern = re.compile(r"(?<=[.!?。！？])\s*")
    sentences = pattern.split(normalized_text)
    sentences = [s for s in sentences if s.strip()]
    if not sentences:
        sentences = [normalized_text] if normalized_text.strip() else []

    units: List[TextUnit] = []
    cursor = 0
    for i, sentence in enumerate(sentences):
        start = normalized_text.find(sentence, cursor)
        if start == -1:
            start = cursor
        end = start + len(sentence)
        unit_id = f"unit-{_hid(f'{document_id}:{i}:{sentence[:30]}')}"
        units.append(
            TextUnit(
                unit_id=unit_id,
                document_id=document_id,
                text=sentence,
                start_offset=start,
                end_offset=end,
                unit_type="sentence",
                sequence_index=i,
            )
        )
        cursor = end

    if not units and normalized_text:
        unit_id = f"unit-{_hid(f'{document_id}:0:full')}"
        units.append(
            TextUnit(
                unit_id=unit_id,
                document_id=document_id,
                text=normalized_text,
                start_offset=0,
                end_offset=len(normalized_text),
                unit_type="sentence",
                sequence_index=0,
            )
        )
    return units


def run(request: EvaluationRequest) -> Tuple[NormalizedRequest, NormalizedDocument]:
    """Normalize and segment text via deterministic sentence rules."""
    normalized_text = request.raw_text.strip()
    request_id = request.request_id or f"req-{_hid(normalized_text)}"
    now = datetime.now(timezone.utc)

    norm_req = NormalizedRequest(
        request_id=request_id,
        raw_text=request.raw_text,
        bundle_ref=request.bundle_ref,
        normalized_at=now,
        normalization_notes=["strip_whitespace"],
        metadata=dict(request.metadata),
    )

    document_id = f"doc-{_hid(normalized_text)}"
    text_units = _segment_text(normalized_text, document_id)

    doc = NormalizedDocument(
        document_id=document_id,
        request_id=request_id,
        normalized_text=normalized_text,
        text_units=text_units,
        char_count=len(normalized_text),
        word_count=len(normalized_text.split()),
        document_metadata={"segmentation": "sentence_split", "unit_count": len(text_units)},
    )

    return norm_req, doc
