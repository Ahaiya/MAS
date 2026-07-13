"""用于识别对话日志内人类/AI/系统源区域的辅助函数。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_SECTION_RE = re.compile(r"(?ms)^###\s+(?P<label>[^\n]+)\n(?P<body>.*?)(?=^###\s+|\Z)")
_FENCE_RE = re.compile(r"(?ms)(?P<fence>`{3,})(?P<lang>[^\n]*)\n(?P<content>.*?)(?:\n(?P=fence))")


def _classify_section(label: str) -> str:
    normalized = label.strip().lower()
    if normalized in {"session_init", "human_input"}:
        return "human"
    if normalized in {"training_chat_response"} or normalized.endswith("_response"):
        return "ai"
    if normalized in {"entering_new_phase"}:
        return "system"
    return "unknown"


def extract_dialogue_source_spans(markdown_text: str) -> List[Dict[str, Any]]:
    """返回 markdown 对话日志中带源标签的内容范围。
    
        当前的训练数据格式将每个对话事件存储为：
    
        ### section_name
        标签: ...
        时间: ...
        ```text|json
        ...
        ```
    
        我们记录围栏内容的偏移量，以便后续阶段可以确定
        提取的引语是来自人类轮次还是来自助手/系统
        脚手架。"""
    spans: List[Dict[str, Any]] = []
    for section_match in _SECTION_RE.finditer(markdown_text):
        label = section_match.group("label").strip()
        section_body = section_match.group("body")
        fence_match = _FENCE_RE.search(section_body)
        if fence_match is None:
            continue
        content = fence_match.group("content")
        if not content.strip():
            continue
        body_offset = section_match.start("body")
        start_offset = body_offset + fence_match.start("content")
        end_offset = body_offset + fence_match.end("content")
        spans.append(
            {
                "source_type": _classify_section(label),
                "source_label": label,
                "start_offset": start_offset,
                "end_offset": end_offset,
            }
        )
    return spans


def source_for_range(
    start_offset: Optional[int],
    end_offset: Optional[int],
    source_spans: List[Dict[str, Any]],
    *,
    allow_mixed: bool,
) -> Tuple[str, Optional[str]]:
    """返回字符范围的主要 source_type/source_label。"""
    if start_offset is None or end_offset is None or end_offset <= start_offset:
        return "unknown", None
    if not source_spans:
        return "unknown", None

    overlap_by_type: Dict[str, int] = {}
    best_label: Optional[str] = None
    best_label_overlap = -1

    for source_span in source_spans:
        span_start = int(source_span.get("start_offset") or 0)
        span_end = int(source_span.get("end_offset") or 0)
        overlap = min(end_offset, span_end) - max(start_offset, span_start)
        if overlap <= 0:
            continue
        source_type = str(source_span.get("source_type") or "unknown")
        overlap_by_type[source_type] = overlap_by_type.get(source_type, 0) + overlap
        if overlap > best_label_overlap:
            best_label_overlap = overlap
            raw_label = source_span.get("source_label")
            best_label = str(raw_label) if raw_label else None

    if not overlap_by_type:
        return "unknown", None

    sorted_types = sorted(overlap_by_type.items(), key=lambda item: (-item[1], item[0]))
    dominant_type = sorted_types[0][0]
    if allow_mixed and len(sorted_types) > 1:
        return "mixed", best_label
    return dominant_type, best_label
