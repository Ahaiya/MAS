from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.outer_loop.experiments.batch_runner import RunResult, batch_eval


def _write_minimal_tsv(path: Path) -> None:
    path.write_text(
        "essay_id\tessay\n"
        "1001\tThis is a short test essay for batch runner.\n",
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
        json.dumps({"status": "completed"}, indent=2),
        encoding="utf-8",
    )
    (output_dir / "feedback.json").write_text(
        json.dumps({"dimensions": {}}, indent=2),
        encoding="utf-8",
    )
    return RunResult(
        essay_id=essay_id,
        success=True,
        output_dir=output_dir,
        trace_dict={"status": "completed"},
        feedback_dict={"dimensions": {}},
    )


def test_batch_eval_iter_id_layout(tmp_path: Path) -> None:
    tsv_path = tmp_path / "samples.tsv"
    _write_minimal_tsv(tsv_path)

    output_base = tmp_path / "artifacts" / "eval"
    results = batch_eval(
        sample_ids=["1001"],
        bundle_path=Path("configs/bundles/engineering_eval_baseline.bundle.yaml"),
        tsv_path=tsv_path,
        output_base=output_base,
        iter_id="iter_001",
        run_single_fn=_run_single_stub,
        rows_by_id={"1001": {"essay_id": "1001", "essay": "stub"}},
        resolved_bundle=object(),
        default_provider=object(),
        rater_providers={},
        stage_providers={},
        log_providers=[],
        prompt_templates={},
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
        bundle_path=Path("configs/bundles/engineering_eval_baseline.bundle.yaml"),
        tsv_path=tsv_path,
        output_base=output_base,
        iter_id=None,
        run_single_fn=_run_single_stub,
        rows_by_id={"1001": {"essay_id": "1001", "essay": "stub"}},
        resolved_bundle=object(),
        default_provider=object(),
        rater_providers={},
        stage_providers={},
        log_providers=[],
        prompt_templates={},
    )

    assert len(results) == 1
    result = results[0]
    expected_dir = output_base / "1001"
    assert result.output_dir == expected_dir
    assert (expected_dir / "run_trace.json").exists()
    assert (expected_dir / "feedback.json").exists()
