"""
产物落盘：三层目录 `artifacts/{task}/{submission}/{dim}/`。

`feedback.json`、`rater_chains.json` 与 `run_trace.json` 落在 dim 层，每个二级指标
一份。纯 IO，无 LLM、无业务逻辑——内容组装在 agents/feedback.py 与 engine.py，本模块
只管写盘。

`package.json` **不在这里**：它是 parse 花钱买来的输入，住在
`packages/{task}/{submission}/`（见 src/parse/pipeline.py）。往 artifacts/ 里再存
一份运行时副本，只会让同一份包评 N 次就多出 N 份一模一样的文件。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def _write_json(path: Path, payload: Dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def submission_dir(base_dir: Path, task: str, submission: str) -> Path:
    """`artifacts/{task}/{submission}/`——该 submission 下各 dim 产物目录的父级。"""
    return Path(base_dir) / task / submission


def dim_dir(base_dir: Path, task: str, submission: str, dim: str) -> Path:
    """`artifacts/{task}/{submission}/{dim}/`——单次二级指标评价的产物目录。"""
    return submission_dir(base_dir, task, submission) / dim


def write_feedback_artifact(
    base_dir: Path, task: str, submission: str, dim: str, feedback_report: Dict[str, Any]
) -> Path:
    """写 feedback.json 到 dim 层。"""
    return _write_json(dim_dir(base_dir, task, submission, dim) / "feedback.json", feedback_report)


def write_rater_chains_artifact(
    base_dir: Path, task: str, submission: str, dim: str, rater_chains_report: Dict[str, Any]
) -> Path:
    """写 rater_chains.json 到 dim 层。"""
    return _write_json(dim_dir(base_dir, task, submission, dim) / "rater_chains.json", rater_chains_report)


def write_run_trace_artifact(
    base_dir: Path, task: str, submission: str, dim: str, run_trace: Dict[str, Any]
) -> Path:
    """写 run_trace.json 到 dim 层（仅成本/性能，不含决策）。"""
    return _write_json(dim_dir(base_dir, task, submission, dim) / "run_trace.json", run_trace)
