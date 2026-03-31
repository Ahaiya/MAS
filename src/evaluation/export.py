"""评测导出模块，负责把运行产物整理成便于外环分析的平面结构。

QWK-ready export: extract scored fields from run artifacts for evaluation.

Provides functions to convert MAS run artifacts (feedback.json, run_trace.json)
into flat structures suitable for QWK computation and downstream evaluation.

No LLM calls, no config loading — pure data transformation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json


@dataclass(frozen=True)
class DimensionScoreRecord:
    """A single dimension's score record, ready for QWK pairing with ground truth."""

    run_id: str
    essay_id: str
    dimension_id: str
    dimension_name: str
    final_score: int
    display_score: str
    confidence: float
    evidence_count: int


@dataclass(frozen=True)
class RunExport:
    """Full export of a run's scoring results, QWK-ready."""

    run_id: str
    essay_id: str
    bundle_id: str
    bundle_version: str
    provider: str
    status: str
    terminal_validation_passed: bool
    dimension_scores: tuple[DimensionScoreRecord, ...]

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "essay_id": self.essay_id,
            "bundle_id": self.bundle_id,
            "bundle_version": self.bundle_version,
            "provider": self.provider,
            "status": self.status,
            "terminal_validation_passed": self.terminal_validation_passed,
            "dimension_scores": [
                {
                    "dimension_id": r.dimension_id,
                    "dimension_name": r.dimension_name,
                    "final_score": r.final_score,
                    "display_score": r.display_score,
                    "confidence": r.confidence,
                    "evidence_count": r.evidence_count,
                }
                for r in self.dimension_scores
            ],
        }

    def scores_by_dimension(self) -> dict[str, int]:
        """Return {dimension_id: final_score} mapping for QWK computation."""
        return {r.dimension_id: r.final_score for r in self.dimension_scores}


def export_run(run_dir: Path, essay_id: str = "") -> RunExport:
    """Load run artifacts from a directory and return a QWK-ready RunExport.

    Args:
        run_dir: Directory containing run_trace.json and feedback.json.
        essay_id: Optional essay identifier (inferred from run_dir name if empty).

    Returns:
        RunExport with all dimension scores.

    Raises:
        FileNotFoundError: If required artifact files are missing.
        ValueError: If artifacts contain unexpected structure.
    """
    run_dir = Path(run_dir)
    trace_path = run_dir / "run_trace.json"
    feedback_path = run_dir / "feedback.json"

    if not trace_path.exists():
        raise FileNotFoundError(f"run_trace.json not found in {run_dir}")
    if not feedback_path.exists():
        raise FileNotFoundError(f"feedback.json not found in {run_dir}")

    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    feedback = json.loads(feedback_path.read_text(encoding="utf-8"))

    # Infer essay_id from directory structure if not provided
    if not essay_id:
        essay_id = run_dir.parent.name

    run_id = trace.get("run_id", "")
    bundle_id = trace.get("bundle_id", "")
    bundle_version = trace.get("bundle_version", "")
    provider = trace.get("replay_metadata", {}).get("provider", "unknown")
    status = trace.get("status", "")
    terminal_valid = trace.get("terminal_validation_passed", False)

    dimensions_raw = feedback.get("dimensions", {})
    if not isinstance(dimensions_raw, dict):
        raise ValueError(f"feedback.json 'dimensions' is not a dict in {run_dir}")

    records = []
    for dim_id, dim_data in dimensions_raw.items():
        records.append(
            DimensionScoreRecord(
                run_id=run_id,
                essay_id=essay_id,
                dimension_id=dim_id,
                dimension_name=dim_data.get("dimension_name", dim_id),
                final_score=int(
                    dim_data.get("canonical_score")
                    or dim_data.get("final_score")
                    or 0
                ),
                display_score=str(dim_data.get("display_score", "")),
                confidence=float(dim_data.get("confidence", 0.0)),
                evidence_count=int(dim_data.get("evidence_count", 0)),
            )
        )

    return RunExport(
        run_id=run_id,
        essay_id=essay_id,
        bundle_id=bundle_id,
        bundle_version=bundle_version,
        provider=provider,
        status=status,
        terminal_validation_passed=terminal_valid,
        dimension_scores=tuple(records),
    )


def export_snapshot(snapshot_path: Path) -> RunExport:
    """Load a golden snapshot JSON and return a QWK-ready RunExport."""
    snap = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    essay_id = snap.get("essay_id", "")

    # Write to a temp-like structure by reusing the same logic on snapshot data
    trace = snap.get("run_trace", {})
    feedback = snap.get("feedback", {})

    run_id = trace.get("run_id", "")
    bundle_id = trace.get("bundle_id", "")
    bundle_version = trace.get("bundle_version", "")
    provider = snap.get("provider", trace.get("replay_metadata", {}).get("provider", "unknown"))
    status = trace.get("status", "")
    terminal_valid = trace.get("terminal_validation_passed", False)

    dimensions_raw = feedback.get("dimensions", {})
    records = []
    for dim_id, dim_data in dimensions_raw.items():
        records.append(
            DimensionScoreRecord(
                run_id=run_id,
                essay_id=essay_id,
                dimension_id=dim_id,
                dimension_name=dim_data.get("dimension_name", dim_id),
                final_score=int(
                    dim_data.get("canonical_score")
                    or dim_data.get("final_score")
                    or 0
                ),
                display_score=str(dim_data.get("display_score", "")),
                confidence=float(dim_data.get("confidence", 0.0)),
                evidence_count=int(dim_data.get("evidence_count", 0)),
            )
        )

    return RunExport(
        run_id=run_id,
        essay_id=essay_id,
        bundle_id=bundle_id,
        bundle_version=bundle_version,
        provider=provider,
        status=status,
        terminal_validation_passed=terminal_valid,
        dimension_scores=tuple(records),
    )
