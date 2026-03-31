"""Tests for scoring context config wiring (Stage K7.2)."""

from __future__ import annotations

from pathlib import Path

from src.agents.config_resolver import run as resolve_bundle
from src.agents.prompt_builders import build_scoring_prompt
from src.config.resolver import ConfigResolver
from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.evidence import DimensionObservation, FacetFinding, ObservationConfidence
from src.providers.prompt_loader import PromptTemplate

CONFIGS_ROOT = Path("configs")
BUNDLE_PATH = CONFIGS_ROOT / "bundles" / "asap_set8_baseline.bundle.yaml"


def _rubric_for(dim_id: str) -> RubricSnapshot:
    scale_id = "s1"
    dim = {
        "dimension_id": dim_id,
        "code": "X",
        "name": dim_id,
        "scale_ref": scale_id,
        "levels": [{"rank": 1, "summary": "low", "descriptors": ["d"]}],
        "observation_schema": {"required_facets": ["f1"]},
    }
    scale = {"scale_id": scale_id, "type": "ordinal", "min": 1, "max": 6}
    return RubricSnapshot(
        rubric_id="r1",
        rubric_version="v1",
        rubric_name="test",
        dimensions=[dim],
        scales=[scale],
        dimension_by_id={dim_id: dim},
        dimension_by_code={"X": dim},
        scale_by_id={scale_id: scale},
    )


def _obs_for(dim_id: str) -> DimensionObservation:
    return DimensionObservation(
        observation_id=f"obs-{dim_id}",
        document_id="doc-1",
        dimension_id=dim_id,
        supporting_span_ids=[],
        counter_span_ids=[],
        facet_findings=[FacetFinding(facet_id="f1", supporting_span_ids=[], counter_span_ids=[], finding_note="")],
        observation_confidence=ObservationConfidence.HIGH,
        uncertainty_notes=[],
    )


def test_scoring_context_file_resolves_from_bundle_ref():
    resolver = ConfigResolver(CONFIGS_ROOT)
    bundle = resolver.load_bundle_file(BUNDLE_PATH)

    assert bundle.scoring_context_ref is not None
    loaded = resolver.load_artifact(bundle.scoring_context_ref)
    assert loaded.loaded_data is not None
    assert loaded.loaded_data.get("scoring_context", {}).get("context_id") == "asap_set8_scoring_context_v1"


def test_scoring_context_is_available_on_policy_snapshot():
    resolved = resolve_bundle(BUNDLE_PATH)
    context = resolved.policy_snapshot.scoring_context

    assert context.get("context_id") == "asap_set8_scoring_context_v1"
    assert isinstance(context.get("score_anchors"), list)
    assert len(context.get("score_anchors") or []) > 0


def test_score_anchors_can_be_filtered_by_dimension():
    resolved = resolve_bundle(BUNDLE_PATH)
    context = resolved.policy_snapshot.scoring_context
    inline_tpl = PromptTemplate(
        template_text="Anchors:{% for a in score_anchors %}[{{ a.title }}]{% endfor %}",
        metadata={"template_version": "v1", "compatible_dimensions": ["*"]},
        source_path="inline",
    )

    voice_prompt = build_scoring_prompt(
        _obs_for("voice"),
        [],
        _rubric_for("voice"),
        inline_tpl,
        scoring_context=context,
    )
    unknown_prompt = build_scoring_prompt(
        _obs_for("dim_not_in_context"),
        [],
        _rubric_for("dim_not_in_context"),
        inline_tpl,
        scoring_context=context,
    )

    assert "Anchors:[" in voice_prompt
    assert unknown_prompt == "Anchors:"
