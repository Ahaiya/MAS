from __future__ import annotations

from src.contracts.request_models import TextUnit
from src.utils.quote_matcher import _locate_unit_id, match_quote


def _text_units_for(text: str) -> list[TextUnit]:
    pivot = len(text) // 2
    return [
        TextUnit(
            unit_id="u1",
            document_id="doc-1",
            text=text[:pivot],
            start_offset=0,
            end_offset=pivot,
            unit_type="chunk",
            sequence_index=0,
        ),
        TextUnit(
            unit_id="u2",
            document_id="doc-1",
            text=text[pivot:],
            start_offset=pivot,
            end_offset=len(text),
            unit_type="chunk",
            sequence_index=1,
        ),
    ]


def test_exact_match():
    text = "The quick brown fox jumps over the lazy dog."
    result = match_quote("brown fox", text, _text_units_for(text))

    assert result.match_method == "exact"
    assert result.confidence == 1.0
    assert result.start_offset == text.index("brown fox")
    assert result.end_offset == result.start_offset + len("brown fox")
    assert result.unit_id is not None


def test_normalized_match():
    text = "The quick brown   fox\njumps over the lazy dog."
    result = match_quote("brown fox jumps", text, _text_units_for(text))

    assert result.match_method == "normalized"
    assert result.start_offset is not None
    assert result.end_offset is not None
    assert result.unit_id is not None
    matched = text[result.start_offset:result.end_offset]
    assert "brown" in matched
    assert "jumps" in matched


def test_fuzzy_match():
    text = "The quick brown fox jumps over the lazy dog."
    result = match_quote("brown fex", text, _text_units_for(text))

    assert result.match_method == "fuzzy"
    assert result.confidence >= 0.85
    assert result.start_offset is not None
    assert result.end_offset is not None
    matched = text[result.start_offset:result.end_offset]
    assert "brown" in matched


def test_unmatched():
    text = "The quick brown fox jumps over the lazy dog."
    result = match_quote("completely absent quote", text, _text_units_for(text))

    assert result.match_method == "unmatched"
    assert result.confidence == 0.0
    assert result.start_offset is None
    assert result.end_offset is None
    assert result.unit_id is None


def test_locate_unit_id():
    units = [
        TextUnit(
            unit_id="u-left",
            document_id="doc-2",
            text="abcde",
            start_offset=0,
            end_offset=5,
            unit_type="chunk",
            sequence_index=0,
        ),
        TextUnit(
            unit_id="u-right",
            document_id="doc-2",
            text="fghij",
            start_offset=5,
            end_offset=10,
            unit_type="chunk",
            sequence_index=1,
        ),
    ]
    assert _locate_unit_id(3, 8, units) == "u-right"


def test_chinese_text():
    text = "今天阳光很好，我们一起去公园散步。明天可能下雨。"
    result = match_quote("一起去公园散步", text, _text_units_for(text))

    assert result.match_method == "exact"
    assert result.start_offset == text.index("一起去公园散步")
    assert result.end_offset == result.start_offset + len("一起去公园散步")
    assert result.unit_id is not None

