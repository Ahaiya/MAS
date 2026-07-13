"""
配置引用解析器，负责将 bundle 中的相对引用解析为可验证的工件路径。

配置解析器：从 bundle YAML 文件中加载并验证工件引用。

职责：
- 将 bundle YAML 文件解析为 ArtifactBundle dataclass
- 从 configs/ 加载每个被引用的工件文件
- 根据对应的 Pydantic schema 验证每个工件
- 计算每个工件的内容哈希
- 返回已填充 loaded_data 和 content_hash 的 ArtifactRef

不包含：rubric 语义、裁决逻辑、聚合公式，
或任何业务事实。所有 schema 验证仅为结构性验证。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from src.config.freeze import compute_content_hash
from src.config.schema import (
    DimensionRubricFileSchema,
    PromptFileSchema,
    SimplifiedAdjudicationFileSchema,
    SimplifiedAggregationFileSchema,
    SimplifiedBundleFileSchema,
    SimplifiedChunkingPolicyFileSchema,
    TaskContextFileSchema,
    TaskRubricFileSchema,
)
from src.contracts.artifact_bundle import (
    ArtifactBundle,
    ArtifactRef,
    SchemaVersion,
)


class ResolverError(Exception):
    """在工件加载或 schema 验证失败时抛出。"""


# 将源文件路径前缀模式映射到对应的 Pydantic schema 类。
# 顺序很重要：更具体的模式在前。
_SCHEMA_ROUTE: list[tuple[str, type]] = [
    ("rubrics/dimension/", DimensionRubricFileSchema),
    ("rubrics/tasks/", TaskRubricFileSchema),
    ("policies/adjudication/", SimplifiedAdjudicationFileSchema),
    ("policies/aggregation/", SimplifiedAggregationFileSchema),
    ("policies/chunking/", SimplifiedChunkingPolicyFileSchema),
    ("tasks/", TaskContextFileSchema),
    ("prompts/", PromptFileSchema),
]


def _resolve_schema_class(source_file: str) -> type | None:
    """返回给定源文件路径对应的 schema 类，如果未知则返回 None。"""
    # 任务作用域目录下的 rubric 文件优先于通用
    # "tasks/" 前缀，该前缀会路由到 TaskContextFileSchema。
    if source_file.startswith("tasks/") and "/dimension/" in source_file:
        return DimensionRubricFileSchema
    for prefix, schema_cls in _SCHEMA_ROUTE:
        if source_file.startswith(prefix):
            return schema_cls
    return None


class ConfigResolver:
    """加载 bundle YAML 文件并解析工件引用。
    
        Args:
            configs_root: 所有配置文件所在的根目录（默认：'configs/'）。"""

    def __init__(self, configs_root: Path | str = "configs") -> None:
        self.configs_root = Path(configs_root)

    def load_bundle_file(self, bundle_path: Path | str) -> ArtifactBundle:
        """将 bundle YAML 文件解析为 ArtifactBundle。
        
                返回的 bundle 中的引用尚未加载（没有 loaded_data 或 content_hash）。
                对每个引用调用 load_artifact() 以填充这些字段。
        
                Args:
                    bundle_path: bundle YAML 文件的绝对路径或相对路径。
        
                Returns:
                    所有引用已解析但尚未加载的 ArtifactBundle。
        
                Raises:
                    ResolverError: 当文件不存在或 YAML 解析失败时抛出。"""
        bundle_path = Path(bundle_path)
        if not bundle_path.exists():
            raise ResolverError(f"Bundle file not found: {bundle_path}")

        try:
            raw = yaml.safe_load(bundle_path.read_text())
        except yaml.YAMLError as exc:
            raise ResolverError(f"Failed to parse bundle YAML {bundle_path}: {exc}") from exc

        # 该仓库仅使用简化的 bundle 格式。
        return self._load_simplified_bundle(raw, bundle_path)

    def _load_simplified_bundle(
        self, raw: dict[str, Any], bundle_path: Path
    ) -> ArtifactBundle:
        """将简化的 bundle YAML（没有 'artifact_bundle' 包装器）解析为 ArtifactBundle。
        
                路径模板可引用 ``{active_task_id}``、``{active_dim_id}``
                或旧版的 ``{task_file_prefix}`` 占位符。
        
                Args:
                    raw: 已解析的 YAML 内容。
                    bundle_path: 原始文件路径（用于错误信息）。
        
                Returns:
                    所有引用已解析但尚未加载的 ArtifactBundle。
        
                Raises:
                    ResolverError: 当 YAML 格式错误或缺少必填键时抛出。"""
        try:
            bundle_doc = SimplifiedBundleFileSchema(**raw)
        except ValidationError as exc:
            raise ResolverError(
                f"Malformed simplified bundle file {bundle_path}: {exc}"
            ) from exc

        task_id = bundle_doc.active_task_id
        dim_id = bundle_doc.active_dim_id or task_id
        task_file_prefix = f"task_{task_id}"

        def _sub(path: str) -> str:
            return (
                path.replace("{task_file_prefix}", task_file_prefix)
                .replace("{active_task_id}", task_id)
                .replace("{active_dim_id}", dim_id)
            )

        def _strip_configs(path: str) -> str:
            """移除前导的 'configs/'，以生成相对于 configs_root 的路径。"""
            if path.startswith("configs/"):
                return path[len("configs/"):]
            return path

        def _make_ref(template: str, ref_uri: str) -> ArtifactRef:
            source_file = _strip_configs(_sub(template))
            return ArtifactRef(ref_uri=ref_uri, source_file=source_file)

        try:
            rubric_template = bundle_doc.rubric.get("dimension") or bundle_doc.rubric["task"]
            rubric_ref = _make_ref(
                rubric_template,
                f"rubric://dimension_{dim_id}/v1",
            )
            adj_ref = _make_ref(
                bundle_doc.policies["adjudication"],
                f"policy://adjudication_{task_id}/v1",
            )
            agg_ref = _make_ref(
                bundle_doc.policies["aggregation"],
                f"policy://aggregation_{task_id}/v1",
            )
            chunking_ref = _make_ref(
                bundle_doc.policies["chunking"],
                f"policy://chunking_{task_id}/v1",
            )
            scoring_context_ref = _make_ref(
                bundle_doc.context["task"],
                f"context://task_{task_id}/v1",
            )
            prompt_refs = [
                _make_ref(path, f"ops://prompts/{Path(_strip_configs(_sub(path))).stem}/v1")
                for path in [
                    bundle_doc.prompts["chunking"],
                    bundle_doc.prompts["evidence_extraction"],
                    bundle_doc.prompts["scoring"],
                    bundle_doc.prompts["explanation"],
                ]
            ]
        except KeyError as exc:
            raise ResolverError(
                f"Missing required key in simplified bundle {bundle_path}: {exc}"
            ) from exc

        return ArtifactBundle(
            bundle_id=bundle_doc.bundle_id,
            bundle_version="1.0",
            bundle_name=bundle_doc.bundle_id,
            description="",
            schema_version=SchemaVersion(str(bundle_doc.schema_version)),
            rubric_ref=rubric_ref,
            adjudication_policy_ref=adj_ref,
            aggregation_policy_ref=agg_ref,
            explanation_policy_ref=None,
            prompt_refs=prompt_refs,
            source_documents=[],
            chunking_policy_ref=chunking_ref,
            scoring_context_ref=scoring_context_ref,
            metadata={
                "active_task_id": task_id,
                "active_dim_id": dim_id,
                "selected_indicator_ids": [dim_id.upper()],
            },
        )

    def load_artifact(self, ref: ArtifactRef) -> ArtifactRef:
        """加载工件文件，验证其 schema，并返回已填充的引用。
        
                Args:
                    ref: 已设置 source_file 的 ArtifactRef（loaded_data 可能为 None）。
        
                Returns:
                    已填充 loaded_data 和 content_hash 的新 ArtifactRef。
        
                Raises:
                    ResolverError: 当文件缺失、YAML 解析失败或 schema 验证失败时抛出。"""
        artifact_path = self.configs_root / ref.source_file
        if not artifact_path.exists():
            raise ResolverError(f"Artifact file not found: {artifact_path}")

        try:
            content = artifact_path.read_text(encoding="utf-8")
            data: dict[str, Any] = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ResolverError(
                f"Failed to parse artifact YAML {artifact_path}: {exc}"
            ) from exc

        content_hash = compute_content_hash(content)

        schema_cls = _resolve_schema_class(ref.source_file)
        if schema_cls is not None:
            try:
                schema_cls(**data)
            except ValidationError as exc:
                raise ResolverError(
                    f"Schema validation failed for {ref.source_file}: {exc}"
                ) from exc

        return ArtifactRef(
            ref_uri=ref.ref_uri,
            source_file=ref.source_file,
            loaded_data=data,
            content_hash=content_hash,
        )
