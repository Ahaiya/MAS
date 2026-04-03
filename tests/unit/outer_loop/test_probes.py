from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.outer_loop.experiments.batch_runner import RunResult, batch_eval
from src.outer_loop.probes import ProbeResult, run_probe, run_probes

_ALL_PROBES = [
    "coverage_probe",
    "evidence_quality_probe",
    "observation_confidence_probe",
    "rater_consistency_probe",
    "conflict_pattern_probe",
    "resolution_cost_probe",
    "feedback_grounding_probe",
    "qwk_probe",
    "cost_probe",
]


def _write_minimal_tsv(path: Path) -> None:
    path.write_text(
        "essay_id\tessay\n"
        "1001\tProbe test essay body.\n",
        encoding="latin-1",
    )


def _run_single_stub(
    essay_id: str,
    essay_text: str,
    tsv_row: dict[str, Any] | None,
    resolved: Any,
    default_provider: Any | None,
    rater_providers: dict[str, Any],
    stage_providers: dict[str, Any],
    log_providers: list[Any],
    prompt_templates: dict[str, Any],
    output_dir: Path,
    verbose: bool = False,
    debug_bundle: bool = False,
) -> RunResult:
    _ = (
        essay_text,
        tsv_row,
        resolved,
        default_provider,
        rater_providers,
        stage_providers,
        log_providers,
        prompt_templates,
        verbose,
        debug_bundle,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "run_trace.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "started_at": "2026-01-01T00:00:00+00:00",
                "finished_at": "2026-01-01T00:00:01+00:00",
                "node_traces": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (output_dir / "feedback.json").write_text(
        json.dumps({"dimensions": {}}, indent=2),
        encoding="utf-8",
    )
    for name in [
        "hypotheses.json",
        "evidence_spans.json",
        "observations.json",
        "conflicts.json",
        "adjudication_records.json",
    ]:
        (output_dir / name).write_text(json.dumps({}, indent=2), encoding="utf-8")
    return RunResult(
        essay_id=essay_id,
        success=True,
        output_dir=output_dir,
        trace_dict={"status": "completed"},
        feedback_dict={"dimensions": {}},
    )


def test_all_probes_return_probe_result_on_single_essay(tmp_path: Path) -> None:
    tsv_path = tmp_path / "samples.tsv"
    _write_minimal_tsv(tsv_path)

    output_base = tmp_path / "artifacts" / "eval"
    batch_eval(
        sample_ids=["1001"],
        bundle_path=Path("configs/bundles/engineering_eval_baseline.bundle.yaml"),
        tsv_path=tsv_path,
        output_base=output_base,
        run_single_fn=_run_single_stub,
        rows_by_id={"1001": {"essay_id": "1001", "essay": "stub"}},
        resolved_bundle=object(),
        default_provider=object(),
        rater_providers={},
        stage_providers={},
        log_providers=[],
        prompt_templates={},
    )

    essay_dir = output_base / "1001"

    for probe_name in _ALL_PROBES:
        result = run_probe(probe_name, essay_dir, tsv_path=tsv_path)
        assert isinstance(result, ProbeResult)
        assert result.probe_name == probe_name
        assert isinstance(result.metrics, dict)

    aggregated = run_probes(_ALL_PROBES, essay_dir, tsv_path=tsv_path)
    assert set(aggregated.keys()) == set(_ALL_PROBES)


def test_probe_returns_empty_metrics_when_required_json_missing(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts" / "eval" / "1001"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # coverage_probe requires both evidence_spans.json and observations.json.
    result = run_probe("coverage_probe", artifacts_dir)
    assert result.probe_name == "coverage_probe"
    assert result.metrics == {}
    assert result.sample_count == 0


def test_qwk_probe_uses_dynamic_dimension_ids(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts" / "eval"
    sample_1 = artifacts_dir / "1001"
    sample_2 = artifacts_dir / "1002"
    sample_1.mkdir(parents=True, exist_ok=True)
    sample_2.mkdir(parents=True, exist_ok=True)

    for sample_dir, alpha, beta, total in [
        (sample_1, 4, 3, 7),
        (sample_2, 2, 5, 7),
    ]:
        (sample_dir / "run_trace.json").write_text(
            json.dumps({"status": "completed"}, indent=2),
            encoding="utf-8",
        )
        (sample_dir / "feedback.json").write_text(
            json.dumps(
                {
                    "dimensions": {
                        "alpha": {"canonical_score": alpha},
                        "beta": {"canonical_score": beta},
                    },
                    "composite": {"composite_score": {"canonical_score": total}},
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    tsv_path = tmp_path / "scores.tsv"
    tsv_path.write_text(
        "essay_id\trater1_alpha\trater2_alpha\trater1_beta\trater2_beta\tcomposite_score\n"
        "1001\t4\t4\t3\t3\t7\n"
        "1002\t2\t2\t5\t5\t7\n",
        encoding="utf-8",
    )

    result = run_probe("qwk_probe", artifacts_dir, tsv_path=tsv_path)
    assert result.probe_name == "qwk_probe"
    assert result.metrics["n_alpha"] == 2
    assert result.metrics["n_beta"] == 2
    assert "qwk_alpha" in result.metrics
    assert "qwk_beta" in result.metrics


def test_qwk_probe_accepts_indicator_score_alias(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts" / "eval"
    sample_1 = artifacts_dir / "1001"
    sample_2 = artifacts_dir / "1002"
    sample_1.mkdir(parents=True, exist_ok=True)
    sample_2.mkdir(parents=True, exist_ok=True)

    for sample_dir, alpha, beta, total in [
        (sample_1, 4, 3, 4),
        (sample_2, 2, 5, 4),
    ]:
        (sample_dir / "run_trace.json").write_text(
            json.dumps({"status": "completed"}, indent=2),
            encoding="utf-8",
        )
        (sample_dir / "feedback.json").write_text(
            json.dumps(
                {
                    "dimensions": {
                        "alpha": {"canonical_score": alpha},
                        "beta": {"canonical_score": beta},
                    },
                    "indicator_score": {"composite_score": {"canonical_score": total}},
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    tsv_path = tmp_path / "scores.tsv"
    tsv_path.write_text(
        "essay_id\trater1_alpha\trater2_alpha\trater1_beta\trater2_beta\tcomposite_score\n"
        "1001\t4\t4\t3\t3\t4\n"
        "1002\t2\t2\t5\t5\t4\n",
        encoding="utf-8",
    )

    result = run_probe("qwk_probe", artifacts_dir, tsv_path=tsv_path)
    assert result.probe_name == "qwk_probe"
    assert result.metrics["n_composite"] == 2
