from __future__ import annotations

from pathlib import Path

from src.outer_loop.experiments.batch_runner import batch_eval


def _write_minimal_tsv(path: Path) -> None:
    path.write_text(
        "essay_id\tessay\n"
        "1001\tThis is a short test essay for batch runner.\n",
        encoding="latin-1",
    )


def test_batch_eval_iter_id_layout(tmp_path: Path) -> None:
    tsv_path = tmp_path / "samples.tsv"
    _write_minimal_tsv(tsv_path)

    output_base = tmp_path / "artifacts" / "eval"
    results = batch_eval(
        sample_ids=["1001"],
        bundle_path=Path("configs/bundles/asap_set8_baseline.bundle.yaml"),
        tsv_path=tsv_path,
        output_base=output_base,
        iter_id="iter_001",
        mock_provider=True,
    )

    assert len(results) == 1
    result = results[0]
    assert result.success is True
    expected_dir = output_base / "iter_001" / "1001"
    assert result.output_dir == expected_dir
    assert (expected_dir / "run_trace.json").exists()
    assert (expected_dir / "feedback.json").exists()


def test_batch_eval_default_layout_without_iter_id(tmp_path: Path) -> None:
    tsv_path = tmp_path / "samples.tsv"
    _write_minimal_tsv(tsv_path)

    output_base = tmp_path / "artifacts" / "eval"
    results = batch_eval(
        sample_ids=["1001"],
        bundle_path=Path("configs/bundles/asap_set8_baseline.bundle.yaml"),
        tsv_path=tsv_path,
        output_base=output_base,
        iter_id=None,
        mock_provider=True,
    )

    assert len(results) == 1
    result = results[0]
    expected_dir = output_base / "1001"
    assert result.output_dir == expected_dir
    assert (expected_dir / "run_trace.json").exists()
    assert (expected_dir / "feedback.json").exists()
