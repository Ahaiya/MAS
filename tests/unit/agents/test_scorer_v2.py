"""Unit tests for Stage-K scorer behavior and scoring prompt wiring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.agents import scorer
from src.agents.prompt_builders import build_scoring_prompt
from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.evidence import (
    DimensionObservation,
    EvidenceScope,
    EvidenceSpan,
    FacetFinding,
    ObservationConfidence,
)
from src.providers.base import BaseProvider, LLMRequest, LLMResponse, ProviderCapability, TokenUsage
from src.providers.base import ProviderParseError
from src.providers.prompt_loader import PromptLoader, PromptTemplate


class _CaptureProvider(BaseProvider):
    def __init__(self, payload: dict[str, Any]) -> None:
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


class _RetryThenSuccessProvider(BaseProvider):
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.requests: list[LLMRequest] = []

    @property
    def name(self) -> str:
        return "retry_then_success"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.TEXT_COMPLETION, ProviderCapability.STRUCTURED_OUTPUT})

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            raise ProviderParseError("broken json", raw_content="{")
        return LLMResponse(
            content=json.dumps(self.payload, ensure_ascii=False),
            structured_data=self.payload,
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            provider_name=self.name,
            model_id="capture-v1",
        )


def _rubric() -> RubricSnapshot:
    scale_id = "ordinal_6"
    dim = {
        "dimension_id": "voice",
        "code": "V",
        "name": "Voice",
        "scale_ref": scale_id,
        "observation_schema": {"required_facets": ["authenticity", "audience_awareness"]},
        "levels": [
            {"rank": 1, "summary": "Minimal", "descriptors": ["Voice is absent."]},
            {"rank": 4, "summary": "Clear", "descriptors": ["Voice is mostly consistent."]},
            {"rank": 6, "summary": "Exceptional", "descriptors": ["Voice is distinctive and sustained."]},
        ],
    }
    scale = {"scale_id": scale_id, "type": "ordinal", "min": 1, "max": 6}
    return RubricSnapshot(
        rubric_id="r-test",
        rubric_version="v1",
        rubric_name="Test Rubric",
        dimensions=[dim],
        scales=[scale],
        dimension_by_id={"voice": dim},
        dimension_by_code={"V": dim},
        scale_by_id={scale_id: scale},
    )


def _spans() -> list[EvidenceSpan]:
    return [
        EvidenceSpan(
            span_id="span-01",
            document_id="doc-1",
            unit_id="u1",
            text_quote="I promised myself I would not hide anymore.",
            start_offset=0,
            end_offset=47,
            scope=EvidenceScope.SPAN,
            dimension_id="voice",
            facet_ids=["authenticity"],
            extraction_note="test",
            support_type="supporting",
        ),
        EvidenceSpan(
            span_id="span-02",
            document_id="doc-1",
            unit_id="u2",
            text_quote="I kept saying things happened, but not what I felt.",
            start_offset=50,
            end_offset=101,
            scope=EvidenceScope.SPAN,
            dimension_id="voice",
            facet_ids=["audience_awareness"],
            extraction_note="test",
            support_type="supporting",
        ),
        EvidenceSpan(
            span_id="span-07",
            document_id="doc-1",
            unit_id="u3",
            text_quote="Some sections sound generic and detached.",
            start_offset=110,
            end_offset=151,
            scope=EvidenceScope.SPAN,
            dimension_id="voice",
            facet_ids=["authenticity"],
            extraction_note="test",
            support_type="counter",
        ),
    ]


def _observation(conf: ObservationConfidence = ObservationConfidence.HIGH) -> DimensionObservation:
    notes = [] if conf != ObservationConfidence.LOW else ["facet 'audience_awareness' has no evidence"]
    return DimensionObservation(
        observation_id="obs-voice-1",
        document_id="doc-1",
        dimension_id="voice",
        supporting_span_ids=["span-01", "span-02"],
        counter_span_ids=["span-07"],
        facet_findings=[
            FacetFinding(
                facet_id="authenticity",
                supporting_span_ids=["span-01"],
                counter_span_ids=["span-07"],
                finding_note="1 supporting, 1 counter",
            ),
            FacetFinding(
                facet_id="audience_awareness",
                supporting_span_ids=["span-02"],
                counter_span_ids=[],
                finding_note="1 supporting, 0 counter",
            ),
        ],
        observation_confidence=conf,
        uncertainty_notes=notes,
    )


def _template() -> PromptTemplate:
    loader = PromptLoader()
    return loader.load(Path("configs/prompts/scoring.yaml"))


def _context() -> dict[str, Any]:
    return {
        "context_id": "ctx-test",
        "role_description": "You are a trained essay scorer.",
        "dataset_notes": "Dataset note: placeholders are not errors.",
        "calibration_notes": "Calibration note: do not score too conservatively.",
        "score_anchors": [
            {
                "anchor_id": "anchor-voice-5",
                "target_score": 5,
                "title": "Anchor A",
                "per_dimension": {
                    "voice": {"score": 6, "note": "Sustained, distinctive, audience-aware voice."}
                },
            }
        ],
    }


def _override() -> PromptTemplate:
    return PromptTemplate(
        template_text="VOICE-OVERRIDE: keep baseline register from inflating score.",
        metadata={"template_version": "v1", "compatible_dimensions": ["voice"]},
        source_path="inline",
    )


def test_no_essay_text_in_prompt():
    prompt = build_scoring_prompt(_observation(), _spans(), _rubric(), _template())
    assert "## Essay Text" not in prompt
    assert "Facet Evidence" in prompt


def test_facet_evidence_in_prompt():
    prompt = build_scoring_prompt(_observation(), _spans(), _rubric(), _template())
    assert "span-01" in prompt
    assert "I promised myself I would not hide anymore." in prompt
    assert "authenticity" in prompt


def test_score_anchors_from_context():
    prompt = build_scoring_prompt(
        _observation(),
        _spans(),
        _rubric(),
        _template(),
        scoring_context=_context(),
    )
    assert "Anchor A" in prompt
    assert "Sustained, distinctive, audience-aware voice." in prompt


def test_no_context_still_works():
    prompt = build_scoring_prompt(_observation(), _spans(), _rubric(), _template())
    assert "trained writing scorer" in prompt
    assert "Voice" in prompt


def test_evidence_ids_from_llm_response():
    provider = _CaptureProvider(
        {
            "proposed_score": 4,
            "descriptor_refs": ["Voice is mostly consistent."],
            "evidence_ids": ["span-01", "span-07"],
            "confidence": 0.65,
            "justification": "Uses both support and counter evidence.",
        }
    )
    hyp = scorer.run(
        _observation(),
        _spans(),
        _rubric(),
        provider,
        _template(),
        "rater_1",
        scoring_context=_context(),
    )
    assert hyp.evidence_span_ids == ["span-01", "span-07"]


def test_invalid_evidence_ids_fallback():
    provider = _CaptureProvider(
        {
            "proposed_score": 4,
            "descriptor_refs": ["Voice is mostly consistent."],
            "evidence_ids": ["ghost-span"],
            "confidence": 0.6,
            "justification": "Invalid ids in response.",
        }
    )
    obs = _observation()
    hyp = scorer.run(
        obs,
        _spans(),
        _rubric(),
        provider,
        _template(),
        "rater_1",
        scoring_context=_context(),
    )
    assert hyp.evidence_span_ids == obs.supporting_span_ids


def test_override_template_for_voice():
    prompt = build_scoring_prompt(
        _observation(),
        _spans(),
        _rubric(),
        _template(),
        scoring_context=_context(),
        override_template=_override(),
    )
    assert "VOICE-OVERRIDE" in prompt


def test_low_confidence_observation():
    obs = _observation(ObservationConfidence.LOW)
    prompt = build_scoring_prompt(
        obs,
        _spans(),
        _rubric(),
        _template(),
        scoring_context=_context(),
    )
    assert "LOW" in prompt
    assert "facet 'audience_awareness' has no evidence" in prompt


def test_score_is_clamped_to_scale_range():
    provider = _CaptureProvider(
        {
            "proposed_score": 99,
            "descriptor_refs": [],
            "evidence_ids": ["span-01"],
            "confidence": 0.6,
            "justification": "Deliberately out-of-range for clamp test.",
        }
    )
    hyp = scorer.run(
        _observation(),
        _spans(),
        _rubric(),
        provider,
        _template(),
        "rater_1",
        scoring_context=_context(),
    )
    assert hyp.score.canonical_score == 6


def test_parse_error_retries_once_with_recovery_budget():
    provider = _RetryThenSuccessProvider(
        {
            "proposed_score": 4,
            "descriptor_refs": ["Voice is mostly consistent."],
            "evidence_ids": ["span-01"],
            "confidence": 0.7,
            "justification": "Recovered after parse error.",
        }
    )
    hyp = scorer.run(
        _observation(),
        _spans(),
        _rubric(),
        provider,
        _template(),
        "rater_2",
        scoring_context=_context(),
    )
    assert hyp.score.canonical_score == 4
    assert len(provider.requests) == 2
    assert provider.requests[1].params["temperature"] == 0.0
    assert provider.requests[1].params["max_tokens"] == 2048
    assert provider.requests[1].metadata["recovery_reason"] == "parse_error"
    assert provider.requests[1].metadata["scoring_retry_attempt"] == 2
