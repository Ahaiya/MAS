"""配置 Schema 定义，负责约束运行时 YAML 工件的结构。

当前只保留工程评估实际使用的简化 schema；完整/legacy schema 已删除。"""

from __future__ import annotations


from pydantic import BaseModel, ConfigDict


class PromptMetadataSchema(BaseModel):
    """prompt template 文件的元数据。"""

    model_config = ConfigDict(extra="forbid")

    template_version: str
    compatible_dimensions: list[str]


class PromptFileSchema(BaseModel):
    """prompt template YAML 文件的顶层 schema。"""

    model_config = ConfigDict(extra="forbid")

    prompt_template: str
    metadata: PromptMetadataSchema


class SimplifiedBundleFileSchema(BaseModel):
    """工程评估任务使用的简化 bundle 格式。"""

    schema_version: str
    bundle_id: str
    active_task_id: str
    active_dim_id: str | None = None
    rubric: dict  # {source, dimension|task}
    context: dict  # {task}
    prompts: dict  # {chunking, evidence_extraction, scoring, explanation}
    policies: dict  # {chunking, adjudication, aggregation}


class TaskRubricFileSchema(BaseModel):
    """per-task rubric YAML 文件的 schema。"""

    schema_version: str
    task_id: str
    task_name: str
    indicator_description: str
    scale: dict  # {type, min, max, levels}
    dimensions: list  # [{code, name, anchors}]


class DimensionRubricFileSchema(BaseModel):
    """per-dimension rubric YAML 文件的 schema。"""

    schema_version: str
    dim_id: str
    dim_name: str
    indicator_description: str
    scale: dict  # {type, min, max, levels}
    dimensions: list  # [{code, name, anchors}]


class TaskContextFileSchema(BaseModel):
    """per-task scoring context YAML 文件的 schema。"""

    schema_version: str
    task_id: str = ""
    material_context: dict  # {type, description, evidence_focus}
    score_anchors: list = []
    human_instructions: str = ""
    chunking_hints: str = ""
    scoring_context: list = []  # [{code, extraction_hints, calibration_notes, feedback_hints}]


class SimplifiedAdjudicationFileSchema(BaseModel):
    """adjudication policy YAML 文件的简化 schema。"""

    schema_version: str
    adjudication_policy: dict


class SimplifiedAggregationFileSchema(BaseModel):
    """aggregation policy YAML 文件的简化 schema。"""

    schema_version: str
    aggregation_policy: dict


class SimplifiedChunkingPolicyFileSchema(BaseModel):
    """chunking policy YAML 文件的简化 schema。"""

    schema_version: str
    chunking_policy: dict
