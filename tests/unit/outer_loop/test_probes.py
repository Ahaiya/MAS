from __future__ import annotations

from pathlib import Path

from src.outer_loop.experiments.batch_runner import batch_eval
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


def test_all_probes_return_probe_result_on_single_essay(tmp_path: Path) -> None:
    tsv_path = tmp_path / "samples.tsv"
    _write_minimal_tsv(tsv_path)

    output_base = tmp_path / "artifacts" / "eval"
    batch_eval(
        sample_ids=["1001"],
        bundle_path=Path("configs/bundles/asap_set8_baseline.bundle.yaml"),
        tsv_path=tsv_path,
        output_base=output_base,
        mock_provider=True,
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
    assert result.essay_count == 0
