"""
Unit tests for LLM-enabled coverage planning (Stage D).
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from src.agents import coverage
from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.request_models import NormalizedDocument, TextUnit
from src.providers.base import BaseProvider, LLMRequest, LLMResponse, ProviderCapability, TokenUsage
from src.providers.prompt_loader import PromptLoader


class _DimensionMapProvider(BaseProvider):
    def __init__(
        self,
        payload_by_name: Dict[str, Dict[str, Any]],
        *,
        fail_names: Optional[Set[str]] = None,
        sleep_seconds: float = 0.0,
    ) -> None:
        self.payload_by_name = payload_by_name
        self.fail_names = fail_names or set()
        self.sleep_seconds = sleep_seconds
        self.calls = 0
        self.thread_ids: Set[int] = set()

    @property
    def name(self) -> str:
        return "dimension-map"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.TEXT_COMPLETION, ProviderCapability.STRUCTURED_OUTPUT})

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        self.thread_ids.add(threading.get_ident())
        if self.sleep_seconds > 0:
            time.sleep(self.sleep_seconds)

        m = re.search(r"^Name:\s*(.+)$", request.prompt, flags=re.MULTILINE)
        dim_name = m.group(1).strip() if m else ""
        if dim_name in self.fail_names:
            raise RuntimeError(f"forced failure for {dim_name}")
        payload = self.payload_by_name.get(dim_name, {"relevant_chunk_ids": []})

        return LLMResponse(
            content="{}",
            structured_data=payload,
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            provider_name=self.name,
            model_id="dimension-map-v1",
        )


def _make_rubric() -> RubricSnapshot:
    dims = [
        {
            "dimension_id": "dim_ideas",
            "code": "I",
            "name": "Ideas Content",
            "description": "Depth and clarity of ideas.",
            "scale_ref": "s1",
            "observation_schema": {"required_facets": ["focus", "development"]},
            "evidence_requirements": {"minimum_evidence_units": 1},
            "levels": [],
        },
        {
            "dimension_id": "dim_org",
            "code": "O",
            "name": "Organization",
            "description": "Coherence and structure.",
            "scale_ref": "s1",
            "observation_schema": {"required_facets": ["sequencing"]},
            "evidence_requirements": {"minimum_evidence_units": 1},
            "levels": [],
        },
        {
            "dimension_id": "dim_voice",
            "code": "V",
            "name": "Voice",
            "description": "Authorial presence.",
            "scale_ref": "s1",
            "observation_schema": {"required_facets": ["tone"]},
            "evidence_requirements": {"minimum_evidence_units": 1},
            "levels": [],
        },
    ]
    scale = {"scale_id": "s1", "min": 1, "max": 6}
    return RubricSnapshot(
        rubric_id="rubric-coverage-llm",
        rubric_version="v1",
        rubric_name="Coverage LLM Rubric",
        dimensions=dims,
        scales=[scale],
        dimension_by_id={d["dimension_id"]: d for d in dims},
        dimension_by_code={d["code"]: d for d in dims},
        scale_by_id={"s1": scale},
    )


def _make_document() -> NormalizedDocument:
    text = "Intro sentence. Body detail sentence. Closing sentence."
    units = [
        TextUnit(
            unit_id="u1",
            document_id="doc-coverage",
            text="Intro sentence.",
            start_offset=0,
            end_offset=15,
            unit_type="chunk",
            sequence_index=0,
            chunk_title="Intro",
            chunk_method="llm_semantic",
        ),
        TextUnit(
            unit_id="u2",
            document_id="doc-coverage",
            text="Body detail sentence.",
            start_offset=16,
            end_offset=37,
            unit_type="chunk",
            sequence_index=1,
            chunk_title="Body",
            chunk_method="llm_semantic",
        ),
        TextUnit(
            unit_id="u3",
            document_id="doc-coverage",
            text="Closing sentence.",
            start_offset=38,
            end_offset=55,
            unit_type="chunk",
            sequence_index=2,
            chunk_title="Closing",
            chunk_method="llm_semantic",
        ),
    ]
    return NormalizedDocument(
        document_id="doc-coverage",
        request_id="req-coverage",
        normalized_text=text,
        text_units=units,
        char_count=len(text),
        word_count=len(text.split()),
        document_metadata={},
        document_type="essay",
        token_estimate=60,
    )


def _load_template():
    root = Path(__file__).resolve().parents[3]
    return PromptLoader().load(root / "configs" / "prompts" / "dimension_relevance.yaml")


def _chunking_policy() -> dict:
    return {
        "coverage": {
            "default_top_k": 2,
            "per_dimension_top_k": {
                "dim_ideas": 2,
                "dim_org": 1,
                "dim_voice": 1,
            },
        }
    }


def test_full_scan_when_no_provider():
    doc = _make_document()
    rubric = _make_rubric()

    plans = coverage.run(doc, rubric)

    all_ids = [u.unit_id for u in doc.text_units]
    assert len(plans) == len(rubric.dimensions)
    assert all(p.coverage_strategy == "full_scan" for p in plans)
    assert all(p.target_unit_ids == all_ids for p in plans)
    assert all(p.relevance_scores == {} for p in plans)


def test_llm_filters_target_unit_ids():
    doc = _make_document()
    rubric = _make_rubric()
    template = _load_template()
    provider = _DimensionMapProvider(
        {
            "Ideas Content": {"relevant_chunk_ids": ["u2", "u1", "u3"]},
            "Organization": {"relevant_chunk_ids": ["u3", "u1"]},
            "Voice": {"relevant_chunk_ids": ["u1", "u2"]},
        }
    )

    plans = coverage.run(
        doc,
        rubric,
        provider=provider,
        template=template,
        chunking_policy=_chunking_policy(),
    )
    plan_by_dim = {p.dimension_id: p for p in plans}

    assert plan_by_dim["dim_ideas"].coverage_strategy == "targeted"
    assert plan_by_dim["dim_ideas"].target_unit_ids == ["u2", "u1"]
    assert plan_by_dim["dim_org"].target_unit_ids == ["u3"]
    assert plan_by_dim["dim_voice"].target_unit_ids == ["u1"]


def test_failed_dimension_falls_back_to_full_scan():
    doc = _make_document()
    rubric = _make_rubric()
    template = _load_template()
    provider = _DimensionMapProvider(
        {
            "Ideas Content": {"relevant_chunk_ids": ["u2", "u1"]},
            "Voice": {"relevant_chunk_ids": ["u3"]},
        },
        fail_names={"Organization"},
    )

    plans = coverage.run(
        doc,
        rubric,
        provider=provider,
        template=template,
        chunking_policy=_chunking_policy(),
    )
    plan_by_dim = {p.dimension_id: p for p in plans}
    all_ids = [u.unit_id for u in doc.text_units]

    assert plan_by_dim["dim_org"].coverage_strategy == "full_scan"
    assert plan_by_dim["dim_org"].target_unit_ids == all_ids
    assert plan_by_dim["dim_org"].relevance_scores == {}
    assert plan_by_dim["dim_ideas"].coverage_strategy == "targeted"
    assert plan_by_dim["dim_voice"].coverage_strategy == "targeted"


def test_relevance_scores_populated():
    doc = _make_document()
    rubric = _make_rubric()
    template = _load_template()
    provider = _DimensionMapProvider(
        {
            "Ideas Content": {"relevant_chunk_ids": ["u2", "u1", "u3"]},
            "Organization": {"relevant_chunk_ids": ["u3"]},
            "Voice": {"relevant_chunk_ids": ["u1"]},
        }
    )

    plans = coverage.run(
        doc,
        rubric,
        provider=provider,
        template=template,
        chunking_policy=_chunking_policy(),
    )
    plan_by_dim = {p.dimension_id: p for p in plans}
    assert plan_by_dim["dim_ideas"].relevance_scores == {"u2": 1.0, "u1": 0.5}


def test_parallel_calls_all_dimensions():
    doc = _make_document()
    rubric = _make_rubric()
    template = _load_template()
    provider = _DimensionMapProvider(
        {
            "Ideas Content": {"relevant_chunk_ids": ["u2", "u1"]},
            "Organization": {"relevant_chunk_ids": ["u3"]},
            "Voice": {"relevant_chunk_ids": ["u1"]},
        },
        sleep_seconds=0.05,
    )

    coverage.run(
        doc,
        rubric,
        provider=provider,
        template=template,
        chunking_policy=_chunking_policy(),
    )

    assert provider.calls == len(rubric.dimensions)
    assert len(provider.thread_ids) > 1

