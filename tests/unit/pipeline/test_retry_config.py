from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.agents import config_resolver
from src.contracts.artifact_bundle import OperationalParams, ResolvedArtifactBundle
from src.contracts.request_models import EvaluationRequest
from src.pipeline.runner import PipelineRunner


class _StopRun(Exception):
    """Sentinel used to stop PipelineRunner.run right after checkpoint init."""


def _run_until_checkpoint_init(
    monkeypatch: pytest.MonkeyPatch,
    bundle: ResolvedArtifactBundle,
) -> int:
    captured: dict[str, int] = {}

    class _FakeCheckpointManager:
        def __init__(self, run_id: str, max_retries: int) -> None:
            captured["max_retries"] = max_retries
            raise _StopRun

    monkeypatch.setattr("src.pipeline.runner.CheckpointManager", _FakeCheckpointManager)

    runner = PipelineRunner(bundle=bundle)
    request = EvaluationRequest(
        raw_text="This is a test essay.",
        bundle_ref="bundle://test",
    )
    with pytest.raises(_StopRun):
        runner.run(request)
    return captured["max_retries"]


@pytest.fixture(scope="module")
def resolved_bundle() -> ResolvedArtifactBundle:
    bundle_path = Path("configs/bundles/asap_set8_baseline.bundle.yaml")
    return config_resolver.run(bundle_path)


def test_runner_reads_max_retries_from_bundle(
    monkeypatch: pytest.MonkeyPatch,
    resolved_bundle: ResolvedArtifactBundle,
) -> None:
    bundle = replace(
        resolved_bundle,
        operational_params=OperationalParams(max_retries=1),
    )
    observed = _run_until_checkpoint_init(monkeypatch, bundle)
    assert observed == 1


def test_runner_falls_back_to_default_when_operational_params_missing(
    monkeypatch: pytest.MonkeyPatch,
    resolved_bundle: ResolvedArtifactBundle,
) -> None:
    bundle = replace(resolved_bundle, operational_params=None)
    observed = _run_until_checkpoint_init(monkeypatch, bundle)
    assert observed == 2
