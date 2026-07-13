"""引用定位工具，负责把抽取到的引文重新对齐到原文字符区间。"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import List, Optional, Sequence, Tuple

from src.contracts.request_models import TextUnit


@dataclass(frozen=True)
class QuoteMatchResult:
    """将抽取到的引文匹配回源文档的结果。"""

    start_offset: Optional[int]
    end_offset: Optional[int]
    unit_id: Optional[str]
    match_method: str  # "exact" | "normalized" | "fuzzy" | "unmatched"
    confidence: float


def _normalize_with_map(text: str) -> Tuple[str, List[int]]:
    """将空白字符折叠为单个空格，并保留 normalized->raw 的索引映射。"""
    tokens = list(re.finditer(r"\S+", text))
    if not tokens:
        return "", []

    normalized_chars: List[str] = []
    index_map: List[int] = []
    prev_end: Optional[int] = None

    for token in tokens:
        if prev_end is not None:
            normalized_chars.append(" ")
            index_map.append(prev_end)

        raw_start = token.start()
        raw_token = token.group(0)
        for idx, ch in enumerate(raw_token):
            normalized_chars.append(ch)
            index_map.append(raw_start + idx)
        prev_end = token.end()

    return "".join(normalized_chars), index_map


def _map_offsets(norm_start: int, norm_end: int, index_map: Sequence[int]) -> Tuple[int, int]:
    """将 normalized offsets 转换回源文本的 offsets。"""
    start = index_map[norm_start]
    end = index_map[norm_end - 1] + 1
    return start, end


def _candidate_starts(quote: str, text: str) -> List[int]:
    """使用 matching blocks alignment 生成潜在的 fuzzy window 起点。"""
    if not text:
        return []
    if not quote:
        return [0]

    matcher = SequenceMatcher(a=quote, b=text, autojunk=False)
    starts = set()
    for block in matcher.get_matching_blocks():
        if block.size <= 0:
            continue
        base = max(0, block.b - block.a)
        for delta in range(-3, 4):
            start = base + delta
            if 0 <= start < len(text):
                starts.add(start)

    if starts:
        return sorted(starts)

    step = max(1, len(quote) // 4)
    return list(range(0, len(text), step))


def _fuzzy_find(
    quote: str,
    normalized_text: str,
    *,
    min_ratio: float = 0.85,
) -> Optional[Tuple[int, int, float]]:
    """在文本中为 quote 寻找高相似度的 window。"""
    if not quote or not normalized_text:
        return None

    quote_len = len(quote)
    min_len = max(1, int(quote_len * 0.8))
    max_len = min(len(normalized_text), max(min_len, int(quote_len * 1.2)))

    best: Optional[Tuple[int, int, float]] = None
    for start in _candidate_starts(quote, normalized_text):
        for window_len in range(min_len, max_len + 1):
            end = start + window_len
            if end > len(normalized_text):
                continue
            candidate = normalized_text[start:end]
            ratio = SequenceMatcher(a=quote, b=candidate, autojunk=False).ratio()
            if best is None or ratio > best[2]:
                best = (start, end, ratio)

    if best is None or best[2] < min_ratio:
        return None
    return best


def _locate_unit_id(
    start_offset: int,
    end_offset: int,
    text_units: Sequence[TextUnit],
) -> Optional[str]:
    """返回与 [start_offset, end_offset) 重叠度最大的 unit_id。"""
    best_unit_id: Optional[str] = None
    best_overlap = -1
    best_seq = float("inf")

    for unit in text_units:
        overlap = max(
            0,
            min(end_offset, unit.end_offset) - max(start_offset, unit.start_offset),
        )
        if overlap > best_overlap or (overlap == best_overlap and unit.sequence_index < best_seq):
            best_overlap = overlap
            best_seq = unit.sequence_index
            best_unit_id = unit.unit_id

    if best_overlap <= 0:
        return None
    return best_unit_id


def _unmatched() -> QuoteMatchResult:
    return QuoteMatchResult(
        start_offset=None,
        end_offset=None,
        unit_id=None,
        match_method="unmatched",
        confidence=0.0,
    )


def match_quote(
    quote: str,
    normalized_text: str,
    text_units: List[TextUnit],
) -> QuoteMatchResult:
    """在 normalized_text 中定位 quote 并返回 offsets + 所属的 unit_id。"""
    if not quote or not normalized_text:
        return _unmatched()

    # Level 1: exact match
    exact_start = normalized_text.find(quote)
    if exact_start >= 0:
        exact_end = exact_start + len(quote)
        return QuoteMatchResult(
            start_offset=exact_start,
            end_offset=exact_end,
            unit_id=_locate_unit_id(exact_start, exact_end, text_units),
            match_method="exact",
            confidence=1.0,
        )

    # 为 level 2 和 3 准备 normalized forms。
    normalized_doc, doc_map = _normalize_with_map(normalized_text)
    normalized_quote, _ = _normalize_with_map(quote)
    if not normalized_doc or not normalized_quote or not doc_map:
        return _unmatched()

    # Level 2: whitespace-normalized match
    norm_start = normalized_doc.find(normalized_quote)
    if norm_start >= 0:
        norm_end = norm_start + len(normalized_quote)
        start, end = _map_offsets(norm_start, norm_end, doc_map)
        return QuoteMatchResult(
            start_offset=start,
            end_offset=end,
            unit_id=_locate_unit_id(start, end, text_units),
            match_method="normalized",
            confidence=0.95,
        )

    # Level 3: fuzzy match
    fuzzy = _fuzzy_find(normalized_quote, normalized_doc, min_ratio=0.85)
    if fuzzy is not None:
        fuzzy_start, fuzzy_end, ratio = fuzzy
        start, end = _map_offsets(fuzzy_start, fuzzy_end, doc_map)
        return QuoteMatchResult(
            start_offset=start,
            end_offset=end,
            unit_id=_locate_unit_id(start, end, text_units),
            match_method="fuzzy",
            confidence=float(ratio),
        )

    return _unmatched()
