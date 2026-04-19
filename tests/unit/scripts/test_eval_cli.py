from __future__ import annotations

from pathlib import Path

import pytest
import typer

from scripts import eval as eval_cli


def test_derive_sample_id_prefers_group_prefix(tmp_path: Path) -> None:
    input_path = tmp_path / "4组—AI助手.md"
    assert eval_cli._derive_sample_id(input_path) == "4"


def test_derive_sample_id_falls_back_to_stem(tmp_path: Path) -> None:
    input_path = tmp_path / "capstone_review.md"
    assert eval_cli._derive_sample_id(input_path) == "capstone_review"


def test_resolve_input_file_accepts_positional_or_option(tmp_path: Path) -> None:
    input_path = tmp_path / "sample.md"
    assert eval_cli._resolve_input_file(input_path, None) == input_path
    assert eval_cli._resolve_input_file(None, input_path) == input_path


def test_resolve_input_file_rejects_conflicting_inputs(tmp_path: Path) -> None:
    left = tmp_path / "left.md"
    right = tmp_path / "right.md"
    with pytest.raises(typer.BadParameter):
        eval_cli._resolve_input_file(left, right)


def test_default_output_dir_uses_task_name_sample_and_dim() -> None:
    class _Resolved:
        class _Policy:
            scoring_context = {"task_name": "maker_hackathon"}

        class _Bundle:
            metadata = {"active_task_id": "fallback_task"}

        policy_snapshot = _Policy()
        artifact_bundle = _Bundle()

    out_dir = eval_cli._default_output_dir(_Resolved(), "sample", "a4")
    assert out_dir == eval_cli._DEFAULT_OUTPUT_BASE / "maker_hackathon" / "sample" / "A4"
