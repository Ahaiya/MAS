"""
产物落盘：三层目录 `artifacts/{task}/{sample}/{dim}/`。

`package.json`（切分后带编号单元）落在 sample 层，该 sample 下所有 dim 共享，
供把 feedback.json/rater_chains.json 里的 unit_ids 解读回原文。`feedback.json`
与 `rater_chains.json` 落在 dim 层，每个一级指标一份。纯 IO，无 LLM、无业务
逻辑——内容组装在 agents/report.py，本模块只管写盘。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from src.contracts.package import DataPackage


def _write_json(path: Path, payload: Dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def sample_dir(base_dir: Path, task: str, sample: str) -> Path:
    """`artifacts/{task}/{sample}/`——package.json 与该 sample 下各 dim 目录的父级。"""
    return Path(base_dir) / task / sample


def dim_dir(base_dir: Path, task: str, sample: str, dim: str) -> Path:
    """`artifacts/{task}/{sample}/{dim}/`——单次一级指标评价的产物目录。"""
    return sample_dir(base_dir, task, sample) / dim


def write_package_artifact(base_dir: Path, task: str, sample: str, package: DataPackage) -> Path:
    """写 package.json 到 sample 层。"""
    return _write_json(sample_dir(base_dir, task, sample) / "package.json", package.to_dict())


def write_feedback_artifact(
    base_dir: Path, task: str, sample: str, dim: str, feedback_report: Dict[str, Any]
) -> Path:
    """写 feedback.json 到 dim 层。"""
    return _write_json(dim_dir(base_dir, task, sample, dim) / "feedback.json", feedback_report)


def write_rater_chains_artifact(
    base_dir: Path, task: str, sample: str, dim: str, rater_chains_report: Dict[str, Any]
) -> Path:
    """写 rater_chains.json 到 dim 层。"""
    return _write_json(dim_dir(base_dir, task, sample, dim) / "rater_chains.json", rater_chains_report)
