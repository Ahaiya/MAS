"""
配置加载：给定 task_id + dim_id，直接读那份 rubric yaml 建出 RubricSnapshot。

v2 的 `configs/bundle.yaml` 只声明 active_task_id + policies + prompts，engine 需要
的也只是「列出某任务下所有一级指标」与「加载某个一级指标的量规」两件事，两者都直接读文件即可。

不包含 rubric 语义：trait 名称、分数值、adjudication 阈值、prompt 文本全部只经由
加载的配置文件流入。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.contracts.artifact_bundle import RubricSnapshot


class ConfigCompileError(Exception):
    """加载或校验配置失败时抛出。"""


def _build_rubric_snapshot(rubric_file_data: dict[str, Any]) -> RubricSnapshot:
    """从任务/维度 rubric 格式构建 RubricSnapshot。
    
        转换如下：
        - ``dimensions[].code``（例如 ``"A4-1"``） → ``dimension_id = "a4_1"``
        - ``dimensions[].anchors`` → 包含 rank/summary/descriptors 的 ``levels`` 列表
        - ``scale`` → 合成 ScaleEntry，其中 ``scale_id = "ordinal_{min}_{max}"``"""
    rubric_key = (
        rubric_file_data.get("dim_id")
        or rubric_file_data.get("task_id")
        or "unknown"
    )
    rubric_name = (
        rubric_file_data.get("dim_name")
        or rubric_file_data.get("task_name")
        or ""
    )
    indicator_description = str(rubric_file_data.get("indicator_description", "") or "")
    scale_data: dict[str, Any] = rubric_file_data.get("scale", {})

    scale_min: int = int(scale_data.get("min", 1))
    scale_max: int = int(scale_data.get("max", 5))
    scale_type: str = str(scale_data.get("type", "ordinal"))
    # YAML 可能将整数键解析为 int；归一化为 int
    scale_level_labels: dict[int, str] = {
        int(k): str(v) for k, v in (scale_data.get("levels") or {}).items()
    }

    scale_id = f"ordinal_{scale_min}_{scale_max}"
    scale_entry: dict[str, Any] = {
        "scale_id": scale_id,
        "type": scale_type,
        "min": scale_min,
        "max": scale_max,
    }

    dimensions: list[dict[str, Any]] = []
    for dim_raw in rubric_file_data.get("dimensions", []):
        code: str = dim_raw["code"]                          # 例如 "A4-1"
        dimension_id: str = code.lower().replace("-", "_")   # 例如 "a4_1"
        name: str = dim_raw.get("name", "")
        # YAML 可能将整数键解析为 int
        anchors: dict[int, str] = {
            int(k): str(v) for k, v in (dim_raw.get("anchors") or {}).items()
        }

        levels: list[dict[str, Any]] = []
        for rank in range(scale_min, scale_max + 1):
            summary = scale_level_labels.get(rank, str(rank))
            anchor_text = anchors.get(rank, "")
            levels.append({
                "rank": rank,
                "summary": summary,
                "descriptors": [anchor_text] if anchor_text else [],
            })

        dimensions.append({
            "dimension_id": dimension_id,
            "code": code,
            "name": name,
            "scale_ref": scale_id,
            "description": name,
            "observation_schema": {
                "required_facets": [dimension_id],
                "facet_descriptions": {dimension_id: name},
            },
            "evidence_requirements": {
                "minimum_evidence_units": 1,
                "allowed_evidence_scope": ["full_document"],
                "require_textual_grounding": True,
            },
            "levels": levels,
            "metadata": {},
        })

    scales = [scale_entry]
    return RubricSnapshot(
        rubric_id=f"dim_{rubric_key}",
        rubric_version="1.0",
        rubric_name=rubric_name,
        dimensions=dimensions,
        scales=scales,
        indicator_description=indicator_description,
        raw_task_rubric=dict(rubric_file_data),
        dimension_by_id={d["dimension_id"]: d for d in dimensions},
        dimension_by_code={d["code"]: d for d in dimensions},
        scale_by_id={s["scale_id"]: s for s in scales},
    )


def strip_configs_prefix(path: str) -> str:
    """bundle 里的引用写成 `configs/prompts/select.yaml`，而它们都相对 configs_root
    解析——去掉这个前缀。

    Engine 与 `scripts/cli.py` 的 `config validate` 必须用完全相同的方式解析 bundle
    引用，否则会出现"校验通过但引擎跑不起来"，所以放在这里共用，而不是各自实现。"""
    if path.startswith("configs/"):
        return path[len("configs/"):]
    return path


def list_task_dimension_ids(configs_root: Path | str, task_id: str) -> list[str]:
    """列出某任务下所有一级指标 dim_id（按文件名排序），用于"缺省评全部一级指标"。"""
    dimension_dir = Path(configs_root) / "tasks" / task_id / "dimension"
    if not dimension_dir.exists():
        raise ConfigCompileError(f"Task dimension directory not found: {dimension_dir}")
    suffix = "_rubric.yaml"
    return sorted(p.name[: -len(suffix)] for p in dimension_dir.glob(f"*{suffix}"))


def load_dimension_rubric(configs_root: Path | str, task_id: str, dim_id: str) -> RubricSnapshot:
    """直接读 configs/tasks/{task_id}/dimension/{dim_id}_rubric.yaml 构建
    RubricSnapshot，不经过 bundle 级别的工件引用解析。"""
    path = Path(configs_root) / "tasks" / task_id / "dimension" / f"{dim_id}_rubric.yaml"
    if not path.exists():
        raise ConfigCompileError(f"Dimension rubric file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _build_rubric_snapshot(data)
