"""配置 Schema 定义：校验运行时 YAML 工件的结构。

只剩 prompt 模板文件这一处需要 schema 校验（PromptLoader.load 用）。bundle/rubric/
policy 的 schema 随 v1 的 ConfigResolver 一并删除——v2 直接读那几个文件，形状由
读取处自己把关。"""

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
