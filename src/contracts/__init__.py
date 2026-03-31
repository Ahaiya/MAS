"""
契约层入口，集中定义流水线阶段之间传递的 typed contract。

MAS Evaluation Engine - Contracts Package

This package contains all data contracts used by the Multi-Agent System.
All data passed between agents/nodes MUST use types defined here.

Core Contracts:
- artifact_bundle: ArtifactBundle and ResolvedArtifactBundle for config resolution
- score_representation: Canonical score and display annotation separation
- request_models: Request normalization and text segmentation
- evidence: Evidence spans and dimension observations
- scoring: Score hypotheses, conflicts, adjudication, and final decisions
- trace: Run trace and replay metadata
"""

__version__ = "0.1.0"

from .artifact_bundle import (
    SchemaVersion,
    ArtifactRef,
    ArtifactBundle,
    RubricSnapshot,
    PolicySnapshot,
    ResolvedArtifactBundle,
    create_artifact_ref,
)

__all__ = [
    "SchemaVersion",
    "ArtifactRef",
    "ArtifactBundle",
    "RubricSnapshot",
    "PolicySnapshot",
    "ResolvedArtifactBundle",
    "create_artifact_ref",
    "__version__",
]
