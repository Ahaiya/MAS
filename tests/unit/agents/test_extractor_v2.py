"""Unit tests for Stage-I extractor v2 behavior."""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from src.agents import extractor
from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.evidence import EvidenceScope
from src.contracts.request_models import CoveragePlan, NormalizedDocument, TextUnit
from src.providers.base import BaseProvider, LLMRequest, LLMResponse, ProviderCapability, TokenUsage
from src.providers.prompt_loader import PromptTemplate
from src.utils.quote_matcher import QuoteMatchResult


class _CaptureProvider(BaseProvider):
    def __init__(self, payload: Dict[str, Any]) -> None:
        self.payload = payload
        self.last_request: LLMRequest | None = None

    @property
    def name(self) -> str:
        return "capture"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.TEXT_COMPLETION, ProviderCapability.STRUCTURED_OUTPUT})

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.last_request = request
        return LLMResponse(
            content=json.dumps(self.payload, ensure_ascii=False),
            structured_data=self.payload,
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            provider_name=self.name,
            model_id="capture-v1",
        )


def _template(text: str) -> PromptTemplate:
    return PromptTemplate(
        template_text=text,
        metadata={"template_version": "v2", "compatible_dimensions": ["*"]},
        source_path="inline",
    )


def _rubric() -> RubricSnapshot:
    scale_id = "s1"
    dim = {
        "dimension_id": "dim1",
        "code": "D1",
        "name": "Dimension One",
        "scale_ref": scale_id,
        "observation_schema": {"required_facets": ["facet_a", "facet_b"]},
        "levels": [
            {"rank": 1, "summary": "Low", "descriptors": ["weak"]},
            {"rank": 2, "summary": "High", "descriptors": ["strong"]},
        ],
    }
    return RubricSnapshot(
        rubric_id="r1",
        rubric_version="v1",
        rubric_name="test",
        dimensions=[dim],
        scales=[{"scale_id": scale_id, "type": "ordinal", "min": 1, "max": 2}],
        dimension_by_id={"dim1": dim},
        dimension_by_code={"D1": dim},
        scale_by_id={scale_id: {"scale_id": scale_id, "type": "ordinal", "min": 1, "max": 2}},
    )


def _document() -> NormalizedDocument:
    chunk_texts = [
        ("u1", "Intro", "Alpha intro sentence."),
        ("u2", "Evidence", "Beta evidence appears here."),
        ("u3", "Close", "Gamma closing idea."),
    ]
    units = []
    cursor = 0
    parts = []
    for idx, (uid, title, text) in enumerate(chunk_texts):
        start = cursor
        end = start + len(text)
        units.append(
            TextUnit(
                unit_id=uid,
                document_id="doc1",
                text=text,
                start_offset=start,
                end_offset=end,
                unit_type="chunk",
                sequence_index=idx,
                chunk_title=title,
                chunk_method="rule",
            )
        )
        parts.append(text)
        cursor = end + 1
    normalized = " ".join(parts)
    return NormalizedDocument(
        document_id="doc1",
        request_id="req1",
        normalized_text=normalized,
        text_units=units,
        char_count=len(normalized),
        word_count=len(normalized.split()),
        document_metadata={},
    )


def _plan(*, strategy: str, target_ids: list[str]) -> CoveragePlan:
    return CoveragePlan(
        plan_id="plan1",
        document_id="doc1",
        dimension_id="dim1",
        target_unit_ids=target_ids,
        required_facets=["facet_a", "facet_b"],
        minimum_evidence_units=1,
        allowed_evidence_scopes=["span", "global"],
        coverage_strategy=strategy,
    )


def _extraction_template() -> PromptTemplate:
    return _template(
        "Dim {{ dimension_name }} ({{ dimension_code }})\n"
        "Min {{ minimum_evidence_units }}\n"
        "Facets:{% for f in facet_descriptions %}[{{ f.facet_id }}]{% endfor %}\n"
        "Levels:{% for l in levels %}[{{ l.rank }} {{ l.summary }}]{% endfor %}\n"
        "Chunks:{% for c in chunks %}[{{ c.id }}]{{ c.title }}::{{ c.text }}{% endfor %}\n"
    )


def test_targeted_extraction_only_passes_target_chunks():
    provider = _CaptureProvider({"evidence_spans": []})
    extractor.run(
        _plan(strategy="targeted", target_ids=["u2"]),
        _document(),
        _rubric(),
        provider,
        _extraction_template(),
    )
    assert provider.last_request is not None
    prompt = provider.last_request.prompt
    assert "[u2]" in prompt
    assert "[u1]" not in prompt
    assert "[u3]" not in prompt


def test_full_scan_passes_all_chunks():
    provider = _CaptureProvider({"evidence_spans": []})
    extractor.run(
        _plan(strategy="full_scan", target_ids=["u2"]),
        _document(),
        _rubric(),
        provider,
        _extraction_template(),
    )
    assert provider.last_request is not None
    prompt = provider.last_request.prompt
    assert "[u1]" in prompt
    assert "[u2]" in prompt
    assert "[u3]" in prompt


def test_quote_backfill_exact():
    quote = "Beta evidence appears here."
    provider = _CaptureProvider(
        {
            "evidence_spans": [
                {"quote": quote, "chunk_id": "u2", "facets": ["facet_a"], "support_type": "supporting"}
            ]
        }
    )
    doc = _document()
    spans = extractor.run(_plan(strategy="targeted", target_ids=["u2"]), doc, _rubric(), provider, _extraction_template())
    span = spans[0]

    expected_start = doc.normalized_text.index(quote)
    assert span.scope == EvidenceScope.SPAN
    assert span.unit_id == "u2"
    assert span.start_offset == expected_start
    assert span.end_offset == expected_start + len(quote)
    assert span.support_type == "supporting"
    assert ":exact:" in (span.extraction_note or "")


def test_quote_backfill_fuzzy():
    provider = _CaptureProvider(
        {
            "evidence_spans": [
                {"quote": "Beta evidence appear here.", "chunk_id": "u2", "facets": ["facet_a"], "support_type": "supporting"}
            ]
        }
    )
    spans = extractor.run(_plan(strategy="targeted", target_ids=["u2"]), _document(), _rubric(), provider, _extraction_template())
    span = spans[0]

    assert span.scope == EvidenceScope.SPAN
    assert span.unit_id == "u2"
    assert span.start_offset is not None
    assert span.end_offset is not None
    assert ":fuzzy:" in (span.extraction_note or "")


def test_quote_unmatched_falls_to_global():
    provider = _CaptureProvider(
        {
            "evidence_spans": [
                {"quote": "completely-not-in-document", "chunk_id": "u2", "facets": ["facet_a"], "support_type": "counter"}
            ]
        }
    )
    spans = extractor.run(_plan(strategy="targeted", target_ids=["u2"]), _document(), _rubric(), provider, _extraction_template())
    span = spans[0]

    assert span.scope == EvidenceScope.GLOBAL
    assert span.unit_id is None
    assert span.start_offset is None
    assert span.end_offset is None
    assert span.support_type == "counter"
    assert ":unmatched:" in (span.extraction_note or "")


def test_chunk_id_assists_matching(monkeypatch: pytest.MonkeyPatch):
    provider = _CaptureProvider(
        {
            "evidence_spans": [
                {"quote": "chunk-local-only-text", "chunk_id": "u2", "facets": ["facet_a"], "support_type": "supporting"}
            ]
        }
    )
    calls = {"count": 0}

    def _fake_match(quote: str, normalized_text: str, text_units: list[TextUnit]) -> QuoteMatchResult:
        calls["count"] += 1
        if calls["count"] == 1:
            return QuoteMatchResult(None, None, None, "unmatched", 0.0)
        return QuoteMatchResult(0, len(quote), text_units[0].unit_id, "exact", 1.0)

    monkeypatch.setattr("src.agents.extractor.match_quote", _fake_match)
    spans = extractor.run(_plan(strategy="targeted", target_ids=["u2"]), _document(), _rubric(), provider, _extraction_template())
    span = spans[0]

    assert calls["count"] >= 2
    assert span.scope == EvidenceScope.SPAN
    assert span.unit_id == "u2"
    assert span.start_offset is not None
    assert span.end_offset is not None


def test_facet_fallback_still_works():
    provider = _CaptureProvider(
        {
            "evidence_spans": [
                {"quote": "Beta evidence appears here.", "chunk_id": "u2", "facets": ["facet_a"], "support_type": "supporting"}
            ]
        }
    )
    spans = extractor.run(_plan(strategy="targeted", target_ids=["u2"]), _document(), _rubric(), provider, _extraction_template())

    fallback = [s for s in spans if s.extraction_note == "provider_fallback:no_evidence"]
    assert len(fallback) == 1
    assert fallback[0].scope == EvidenceScope.GLOBAL
    assert fallback[0].facet_ids == ["facet_b"]
    assert fallback[0].support_type == "supporting"


def test_override_template_used_when_present():
    provider = _CaptureProvider({"evidence_spans": []})
    default_tpl = _template("DEFAULT-TPL {% for c in chunks %}[{{ c.id }}]{% endfor %}")
    override_tpl = _template("OVERRIDE-TPL {% for c in chunks %}[{{ c.id }}]{% endfor %}")

    extractor.run(
        _plan(strategy="targeted", target_ids=["u2"]),
        _document(),
        _rubric(),
        provider,
        default_tpl,
        override_template=override_tpl,
    )
    assert provider.last_request is not None
    assert "OVERRIDE-TPL" in provider.last_request.prompt
    assert "DEFAULT-TPL" not in provider.last_request.prompt
