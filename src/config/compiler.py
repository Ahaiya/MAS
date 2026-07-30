"""
配置加载：按约定路径读 configs/ 下的量规、提示词与仲裁策略。

仲裁策略在 `{root}/adjudication.yaml`
提示词在 `{root}/prompts/{stage}.yaml`
量规在`{root}/tasks/{task_id}/dimension/{dim_id}_rubric.yaml`
任务由调用现场传入。

"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.config.errors import ConfigCompileError
from src.config.rubric_validation import validate_rubric
from src.contracts.configuration import PolicySnapshot, RubricSnapshot

# 提示词阶段名 = 文件名（`{configs_root}/prompts/{stage}.yaml`）。
PROMPT_STAGES = ("select", "extraction", "scoring", "adjudication", "feedback")


def _build_rubric_snapshot(rubric_file_data: dict[str, Any]) -> RubricSnapshot:
    """把一份 `{dim_id}_rubric.yaml` 读成 RubricSnapshot。

        结构与 YAML 一一对应，只做类型规整（档位键 YAML 可能解析成 int 也可能是
        str，统一成 int）。观测点以 code 为唯一标识，不再派生第二套 id。"""

    scale_data: dict[str, Any] = rubric_file_data["scale"]
    scale_min: int = int(scale_data["min"])
    scale_max: int = int(scale_data["max"])

    dimensions = [
        {
            "code": str(dim_raw["code"]),
            "name": str(dim_raw["name"]),
            # 观测点权重，聚合 dim 分时用（rubric_validation 保证必填且和为 1.0）
            "weight": float(dim_raw["weight"]),
            "anchors": {int(rank): str(text) for rank, text in dim_raw["anchors"].items()},
        }
        for dim_raw in rubric_file_data["dimensions"]
    ]

    return RubricSnapshot(
        dim_id=str(rubric_file_data["dim_id"]),
        dim_name=str(rubric_file_data["dim_name"]),
        indicator_description=str(rubric_file_data["indicator_description"]),
        dimensions=dimensions,
        scale_min=scale_min,
        scale_max=scale_max,
        scale_levels={int(rank): str(label) for rank, label in scale_data["levels"].items()},
    )


def prompt_path(configs_root: Path | str, stage: str) -> Path:
    """提示词按约定固定在 `{configs_root}/prompts/{stage}.yaml`——文件名即阶段名。"""
    return Path(configs_root) / "prompts" / f"{stage}.yaml"


def load_adjudication_policy(configs_root: Path | str) -> PolicySnapshot:
    """读 `{configs_root}/adjudication.yaml` 建 PolicySnapshot。"""

    path = Path(configs_root) / "adjudication.yaml"
    if not path.exists():
        raise ConfigCompileError(f"仲裁策略文件不存在：{path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    values: dict[str, int] = {}
    for key in ("score_gap_threshold", "drift_min_dimensions"):
        raw = data.get(key)
        if not isinstance(raw, int) or isinstance(raw, bool):
            raise ConfigCompileError(f"{path} 的 '{key}' 缺失或不是整数：{raw!r}")
        values[key] = raw

    return PolicySnapshot(**values)


def list_task_dimension_ids(configs_root: Path | str, task_id: str) -> list[str]:
    """列出某任务下所有二级指标 dim_id（按文件名排序），用于"缺省评全部二级指标"。"""
    dimension_dir = Path(configs_root) / "tasks" / task_id / "dimension"
    if not dimension_dir.exists():
        raise ConfigCompileError(f"Task dimension directory not found: {dimension_dir}")
    suffix = "_rubric.yaml"
    return sorted(p.name[: -len(suffix)] for p in dimension_dir.glob(f"*{suffix}"))


def load_dimension_rubric(configs_root: Path | str, task_id: str, dim_id: str) -> RubricSnapshot:
    """读 configs/tasks/{task_id}/dimension/{dim_id}_rubric.yaml 构建 RubricSnapshot。"""

    path = Path(configs_root) / "tasks" / task_id / "dimension" / f"{dim_id}_rubric.yaml"
    if not path.exists():
        raise ConfigCompileError(f"Dimension rubric file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # 先校验后构建：缺字段在这里响，而不是烂到 prompt 里变成"没有量规也照样打分"。
    validate_rubric(data, source=str(path))
    return _build_rubric_snapshot(data)
