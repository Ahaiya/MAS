"""Unit tests for Stage-M feedback prompt/commentary upgrades."""

from __future__ import annotations

from src.agents import feedback
from src.contracts.artifact_bundle import PolicySnapshot, RubricSnapshot
from src.contracts.evidence import (
    DimensionObservation,
    EvidenceScope,
    EvidenceSpan,
    FacetFinding,
    ObservationConfidence,
)
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import FinalDimensionDecision, ScoreHypothesis
from src.providers.base import (
    BaseProvider,
    LLMRequest,
    LLMResponse,
    ProviderCapability,
    TokenUsage,
)
from src.providers.prompt_loader import PromptTemplate


class _CaptureProvider(BaseProvider):
    def __init__(self, response_text: str = "Generated feedback") -> None:
        self.response_text = response_text
        self.last_request: LLMRequest | None = None

    @property
    def name(self) -> str:
        return "capture"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.TEXT_COMPLETION})

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.last_request = request
        return LLMResponse(
            content=self.response_text,
            structured_data=None,
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            provider_name=self.name,
            model_id="capture-v1",
        )


def _rubric() -> RubricSnapshot:
    dim = {
        "dimension_id": "voice",
        "code": "V",
        "name": "Voice",
        "scale_ref": "s6",
        "observation_schema": {"required_facets": ["tone", "development"]},
        "levels": [
            {"rank": 1, "summary": "Minimal", "descriptors": ["Limited control."]},
            {"rank": 4, "summary": "Clear", "descriptors": ["Generally clear voice."]},
            {"rank": 6, "summary": "Exceptional", "descriptors": ["Distinctive and sustained voice."]},
        ],
    }
    scale = {"scale_id": "s6", "type": "ordinal", "min": 1, "max": 6}
    return RubricSnapshot(
        rubric_id="r-stage-m",
        rubric_version="v1",
        rubric_name="Stage M Rubric",
        dimensions=[dim],
        scales=[scale],
        dimension_by_id={"voice": dim},
        dimension_by_code={"V": dim},
        scale_by_id={"s6": scale},
    )


def _policy(low_confidence_threshold: float = 0.5, max_length: int = 220) -> PolicySnapshot:
    return PolicySnapshot(
        adjudication_policy={"triggers": []},
        aggregation_policy={},
        explanation_policy={
            "policy_id": "exp-stage-m",
            "requirements": {
                "require_descriptor_alignment": True,
                "require_evidence_links": True,
                "require_score_citation": True,
            },
            "citation_rules": {"min_citations_per_dimension": 1},
            "output_constraints": {
                "max_commentary_length_per_dimension": max_length,
                "require_evidence_score_chain": True,
                "low_confidence_threshold": low_confidence_threshold,
            },
            "render_sections": [],
        },
        policy_version="stage-m-v1",
    )


def _spans() -> list[EvidenceSpan]:
    return [
        EvidenceSpan(
            span_id="span-01",
            document_id="doc-1",
            unit_id="u1",
            text_quote="The opening sounds confident and clear.",
            start_offset=0,
            end_offset=39,
            scope=EvidenceScope.SPAN,
            dimension_id="voice",
            facet_ids=["tone"],
            extraction_note="test",
        ),
        EvidenceSpan(
            span_id="span-02",
            document_id="doc-1",
            unit_id="u2",
            text_quote="Ideas are developed with specific detail.",
            start_offset=40,
            end_offset=79,
            scope=EvidenceScope.SPAN,
            dimension_id="voice",
            facet_ids=["development"],
            extraction_note="test",
        ),
        EvidenceSpan(
            span_id="span-03",
            document_id="doc-1",
            unit_id="u3",
            text_quote="Tone becomes generic in later paragraphs.",
            start_offset=80,
            end_offset=120,
            scope=EvidenceScope.SPAN,
            dimension_id="voice",
            facet_ids=["tone"],
            extraction_note="test",
        ),
    ]


def _observation(conf: ObservationConfidence = ObservationConfidence.HIGH) -> DimensionObservation:
    notes = [] if conf != ObservationConfidence.LOW else ["coverage is limited in conclusion"]
    return DimensionObservation(
        observation_id="obs-voice-1",
        document_id="doc-1",
        dimension_id="voice",
        supporting_span_ids=["span-01", "span-02"],
        counter_span_ids=["span-03"],
        facet_findings=[
            FacetFinding(
                facet_id="tone",
                supporting_span_ids=["span-01"],
                counter_span_ids=["span-03"],
                finding_note="tone is mostly clear but not sustained",
            ),
            FacetFinding(
                facet_id="development",
                supporting_span_ids=["span-02"],
                counter_span_ids=[],
                finding_note="details support development",
            ),
        ],
        observation_confidence=conf,
        uncertainty_notes=notes,
    )


def _decision(
    confidence: float = 0.86,
    adjudication_id: str | None = None,
    primary_hypothesis_id: str = "hyp-primary",
) -> FinalDimensionDecision:
    return FinalDimensionDecision(
        decision_id="dec-voice-1",
        dimension_id="voice",
        final_score=create_score_representation(4, "s6"),
        primary_hypothesis_id=primary_hypothesis_id,
        adjudication_id=adjudication_id,
        evidence_span_ids=["span-01", "span-03"],
        descriptor_refs=["Generally clear voice."],
        decision_confidence=confidence,
        decision_note="resolution context note",
    )


def _hypothesis(rationale: str = "Scorer seed rationale for this dimension.") -> ScoreHypothesis:
    return ScoreHypothesis(
        hypothesis_id="hyp-primary",
        observation_id="obs-voice-1",
        dimension_id="voice",
        rater_id="rater_1",
        score=create_score_representation(4, "s6"),
        descriptor_refs=["Generally clear voice."],
        evidence_span_ids=["span-01", "span-03"],
        rationale=rationale,
        confidence=0.84,
    )


def _template() -> PromptTemplate:
    return PromptTemplate(
        template_text=(
            "DIM={{ dimension_name }}\n"
            "{% for facet in facet_evidence %}"
            "FACET={{ facet.facet_id }} "
            "S:{% for item in facet.supporting %}[{{ item.span_id }}]{{ item.quote }};{% endfor %} "
            "C:{% for item in facet.counter %}[{{ item.span_id }}]{{ item.quote }};{% endfor %}\n"
            "{% endfor %}"
            "CONF={{ observation_confidence }}\n"
            "RAT={{ scorer_rationale }}\n"
            "NOTE={{ decision_note }}\n"
            "ADJ={{ was_adjudicated }}\n"
            "OVR={{ dimension_override_notes }}"
        ),
        metadata={"template_version": "v2", "compatible_dimensions": ["*"]},
        source_path="inline-explanation",
    )


def test_facet_evidence_in_prompt() -> None:
    provider = _CaptureProvider()
    feedback.run(
        decisions=[_decision()],
        observations=[_observation()],
        spans=_spans(),
        hypotheses=[_hypothesis()],
        rubric=_rubric(),
        policy=_policy(),
        provider=provider,
        template=_template(),
    )
    assert provider.last_request is not None
    prompt = provider.last_request.prompt
    assert "FACET=tone" in prompt
    assert "[span-01]The opening sounds confident and clear." in prompt
    assert "[span-03]Tone becomes generic in later paragraphs." in prompt
    assert "span-02" not in prompt


def test_scorer_rationale_in_prompt() -> None:
    provider = _CaptureProvider()
    rationale = "RATER_JUSTIFICATION: tone is clear but inconsistent."
    feedback.run(
        decisions=[_decision()],
        observations=[_observation()],
        spans=_spans(),
        hypotheses=[_hypothesis(rationale=rationale)],
        rubric=_rubric(),
        policy=_policy(),
        provider=provider,
        template=_template(),
    )
    assert provider.last_request is not None
    assert rationale in provider.last_request.prompt


def test_policy_commentary_uses_facets_when_provider_returns_empty() -> None:
    provider = _CaptureProvider(response_text="")
    out = feedback.run(
        decisions=[_decision()],
        observations=[_observation()],
        spans=_spans(),
        hypotheses=[],
        rubric=_rubric(),
        policy=_policy(),
        provider=provider,
        template=_template(),
    )
    text = out["dimensions"]["voice"]["feedback_text"]
    assert "[tone]: supporting evidence:" in text
    assert "counter evidence suggests" in text


def test_policy_commentary_uses_rationale_when_provider_returns_empty() -> None:
    rationale = "This is the scorer's rationale seed that should dominate deterministic commentary."
    policy = _policy(max_length=40)
    provider = _CaptureProvider(response_text="")
    out = feedback.run(
        decisions=[_decision()],
        observations=[_observation()],
        spans=_spans(),
        hypotheses=[_hypothesis(rationale=rationale)],
        rubric=_rubric(),
        policy=policy,
        provider=provider,
        template=_template(),
    )
    assert out["dimensions"]["voice"]["feedback_text"] == rationale[:40]


def test_low_confidence_threshold_from_config() -> None:
    provider = _CaptureProvider(response_text="")
    out = feedback.run(
        decisions=[_decision(confidence=0.7)],
        observations=[_observation()],
        spans=_spans(),
        hypotheses=[_hypothesis()],
        rubric=_rubric(),
        policy=_policy(low_confidence_threshold=0.8),
        provider=provider,
        template=_template(),
    )
    note = out["dimensions"]["voice"]["uncertainty_note"]
    assert note is not None
    assert "below threshold" in note


def test_adjudication_uncertainty_note() -> None:
    provider = _CaptureProvider(response_text="")
    out = feedback.run(
        decisions=[_decision(confidence=0.95, adjudication_id="adj-voice-1")],
        observations=[_observation()],
        spans=_spans(),
        hypotheses=[_hypothesis()],
        rubric=_rubric(),
        policy=_policy(),
        provider=provider,
        template=_template(),
    )
    note = out["dimensions"]["voice"]["uncertainty_note"]
    assert note is not None
    assert "adjudication was required" in note


def test_override_template_for_dimension() -> None:
    provider = _CaptureProvider()
    override = PromptTemplate(
        template_text="OVERRIDE {{ dimension_name }} :: {{ scorer_rationale }}",
        metadata={"template_version": "v1", "compatible_dimensions": ["voice"]},
        source_path="inline-override",
    )
    feedback.run(
        decisions=[_decision()],
        observations=[_observation()],
        spans=_spans(),
        hypotheses=[_hypothesis()],
        rubric=_rubric(),
        policy=_policy(),
        provider=provider,
        template=_template(),
        override_templates={"voice": override},
    )
    assert provider.last_request is not None
    assert provider.last_request.prompt.startswith("OVERRIDE Voice")
    assert provider.last_request.metadata.get("template_source") == "inline-override"


def test_scorer_rationale_in_output() -> None:
    rationale = "Explicit scorer rationale passthrough."
    provider = _CaptureProvider(response_text="")
    out = feedback.run(
        decisions=[_decision()],
        observations=[_observation()],
        spans=_spans(),
        hypotheses=[_hypothesis(rationale=rationale)],
        rubric=_rubric(),
        policy=_policy(),
        provider=provider,
        template=_template(),
    )
    entry = out["dimensions"]["voice"]
    assert entry["scorer_rationale"] == rationale
    assert entry["was_adjudicated"] is False


def test_backward_compat_no_hypotheses() -> None:
    provider = _CaptureProvider(response_text="")
    out = feedback.run(
        [_decision()],
        [_observation()],
        _spans(),
        _rubric(),
        _policy(),
        provider=provider,
        template=_template(),
    )
    entry = out["dimensions"]["voice"]
    assert "scorer_rationale" in entry
    assert entry["scorer_rationale"] == ""
