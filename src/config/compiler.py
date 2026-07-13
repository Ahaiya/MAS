"""
配置编译器，负责将 bundle、rubric、policy 和 provider 配置编译为冻结快照。

Config Compiler：编排 bundle 加载、artifact 解析与冻结。

生成一个冻结的 ResolvedArtifactBundle，供 orchestrator 使用。
编译器是将 bundle YAML 文件路径转换为运行时就绪、经过哈希验证的配置快照的唯一入口。

职责：
- 通过 ConfigResolver 加载并解析 bundle
- 解析所有 artifact 引用（加载 + 验证 + 哈希）
- 从加载的数据构建 RubricSnapshot 和 PolicySnapshot
- 通过 freeze.py 计算总 bundle 哈希
- 返回完全冻结的 ResolvedArtifactBundle

不包含：rubric 语义、trait 名称、score 值、adjudication 阈值、aggregation 公式或 prompt 文本。所有这些数据仅通过加载的配置 artifact 流入。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.freeze import compute_bundle_hash
from src.config.resolver import ConfigResolver, ResolverError
from src.contracts.artifact_bundle import (
    ArtifactBundle,
    OperationalParams,
    PolicySnapshot,
    ProviderConfig,
    ProviderEntryConfig,
    ResolvedArtifactBundle,
    RubricSnapshot,
)

COMPILER_VERSION = "0.1.0"


class ConfigCompileError(Exception):
    """在 bundle 编译的任何阶段失败时抛出。"""


class ConfigCompiler:
    """将 bundle YAML 文件编译为冻结的 ResolvedArtifactBundle。
    
        参数：
            configs_root：配置文件根目录（默认：'configs/'）。"""

    def __init__(self, configs_root: Path | str | None = None) -> None:
        self.configs_root = Path(configs_root or "configs")
        self._resolver = ConfigResolver(self.configs_root)

    def compile(self, bundle_path: Path | str) -> ResolvedArtifactBundle:
        """将 bundle 文件编译为冻结的 ResolvedArtifactBundle。
        
                步骤：
                1. 加载 bundle YAML → ArtifactBundle（引用未解析）
                2. 解析所有 artifact 引用（加载 + 验证 + 为每个计算哈希）
                3. 构建 RubricSnapshot 和 PolicySnapshot
                4. 根据所有 artifact 内容哈希计算 total_hash
                5. 构建冻结的 ArtifactBundle 和 ResolvedArtifactBundle
        
                参数：
                    bundle_path：bundle YAML 文件路径。
        
                返回：
                    完全冻结的 ResolvedArtifactBundle。
        
                抛出：
                    ConfigCompileError：如果加载、解析或验证失败。"""
        bundle_path = Path(bundle_path)

        # 步骤 1：加载 bundle
        try:
            bundle = self._resolver.load_bundle_file(bundle_path)
        except ResolverError as exc:
            raise ConfigCompileError(f"Failed to load bundle '{bundle_path}': {exc}") from exc

        # 步骤 2：解析所有 artifact 引用
        try:
            loaded_rubric = self._resolver.load_artifact(bundle.rubric_ref)
            loaded_adj = self._resolver.load_artifact(bundle.adjudication_policy_ref)
            loaded_agg = self._resolver.load_artifact(bundle.aggregation_policy_ref)
            loaded_exp = (
                self._resolver.load_artifact(bundle.explanation_policy_ref)
                if bundle.explanation_policy_ref is not None
                else None
            )
            loaded_prompts = [
                self._resolver.load_artifact(ref) for ref in bundle.prompt_refs
            ]
            loaded_chunking = (
                self._resolver.load_artifact(bundle.chunking_policy_ref)
                if bundle.chunking_policy_ref is not None
                else None
            )
            loaded_scoring_context = (
                self._resolver.load_artifact(bundle.scoring_context_ref)
                if bundle.scoring_context_ref is not None
                else None
            )
        except ResolverError as exc:
            raise ConfigCompileError(f"Failed to resolve artifact: {exc}") from exc

        # 步骤 3a：从 rubric 核心数据构建 RubricSnapshot
        rubric_snapshot = _build_rubric_snapshot(loaded_rubric.loaded_data)

        # 步骤 3b：从 policy 数据构建 PolicySnapshot
        exp_file_data = loaded_exp.loaded_data if loaded_exp is not None else {"explanation_policy": {}}
        policy_snapshot = _build_policy_snapshot(
            loaded_adj.loaded_data,
            loaded_agg.loaded_data,
            exp_file_data,
            loaded_chunking.loaded_data if loaded_chunking is not None else None,
            loaded_scoring_context.loaded_data if loaded_scoring_context is not None else None,
        )

        # 步骤 3c：构建 prompt 模板字典 {source_file: template_string}
        prompt_templates = {
            p.source_file: p.loaded_data["prompt_template"]
            for p in loaded_prompts
        }

        # 步骤 4：计算总 bundle 哈希
        all_content_hashes = [
            loaded_rubric.content_hash,
            loaded_adj.content_hash,
            loaded_agg.content_hash,
            *[p.content_hash for p in loaded_prompts],
        ]
        if loaded_exp is not None:
            all_content_hashes.append(loaded_exp.content_hash)
        if loaded_chunking is not None:
            all_content_hashes.append(loaded_chunking.content_hash)
        if loaded_scoring_context is not None:
            all_content_hashes.append(loaded_scoring_context.content_hash)
        total_hash = compute_bundle_hash(all_content_hashes)

        # 步骤 4b：从原始字典构建 ProviderConfig（如果 bundle 中存在）
        provider_config = _build_provider_config(bundle.provider_config_raw)
        operational_params = _build_operational_params(bundle.operational_params_raw)

        # 步骤 5：构建冻结的 ArtifactBundle（设置 freeze_hash）
        resolved_at = datetime.now(timezone.utc)
        frozen_bundle = ArtifactBundle(
            bundle_id=bundle.bundle_id,
            bundle_version=bundle.bundle_version,
            bundle_name=bundle.bundle_name,
            description=bundle.description,
            schema_version=bundle.schema_version,
            rubric_ref=loaded_rubric,
            adjudication_policy_ref=loaded_adj,
            aggregation_policy_ref=loaded_agg,
            explanation_policy_ref=loaded_exp,
            prompt_refs=loaded_prompts,
            source_documents=bundle.source_documents,
            freeze_hash=total_hash,
            freeze_timestamp=resolved_at,
            validation_rules=bundle.validation_rules,
            metadata=bundle.metadata,
            provider_config_raw=bundle.provider_config_raw,
            operational_params_raw=bundle.operational_params_raw,
            chunking_policy_ref=loaded_chunking,
            scoring_context_ref=loaded_scoring_context,
        )


        return ResolvedArtifactBundle(
            artifact_bundle=frozen_bundle,
            rubric_snapshot=rubric_snapshot,
            policy_snapshot=policy_snapshot,
            prompt_templates=prompt_templates,
            provider_config=provider_config,
            operational_params=operational_params,
            resolved_at=resolved_at,
            resolver_version=COMPILER_VERSION,
            total_hash=total_hash,
        )


def _parse_provider_entry(raw: dict[str, Any]) -> ProviderEntryConfig:
    """将单个 provider 条目字典解析为 ProviderEntryConfig。"""
    return ProviderEntryConfig(
        api_key_env=raw["api_key_env"],
        model=raw.get("model", "") or "",
        api_base=raw.get("api_base", "") or "",
        params=dict(raw.get("params") or {}),
    )


def _build_provider_config(raw: dict[str, Any] | None) -> ProviderConfig | None:
    """将 bundle YAML 的 provider_config 部分解析为 ProviderConfig。
    
        如果没有 provider_config 部分，则返回 None。
        如果该部分格式错误，则抛出 ConfigCompileError。"""
    if raw is None:
        return None
    try:
        default = _parse_provider_entry(raw["default"])
        rater_providers = {
            rater_id: _parse_provider_entry(entry)
            for rater_id, entry in (raw.get("rater_providers") or {}).items()
        }
        stage_providers = {
            stage: _parse_provider_entry(entry)
            for stage, entry in (raw.get("stage_providers") or {}).items()
        }
        return ProviderConfig(
            default=default,
            rater_providers=rater_providers,
            stage_providers=stage_providers,
        )
    except (KeyError, TypeError) as exc:
        raise ConfigCompileError(f"Malformed provider_config in bundle: {exc}") from exc


def _build_operational_params(raw: dict[str, Any] | None) -> OperationalParams | None:
    """从 bundle YAML 解析操作参数为类型化的运行时配置。"""
    if raw is None:
        return None
    try:
        max_retries = raw.get("max_retries")
        if not isinstance(max_retries, int):
            raise TypeError("max_retries must be int")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        return OperationalParams(max_retries=max_retries)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ConfigCompileError(f"Malformed operational_params in bundle: {exc}") from exc


def _build_rubric_snapshot(rubric_file_data: dict[str, Any]) -> RubricSnapshot:
    """从 rubric 文件数据构建带有查询映射的 RubricSnapshot。
    
        支持两种格式：
        - 完整 rubric 格式：包含 ``rubric_core`` 键（遗留 ASAP 格式）。
        - 任务 rubric 格式：在顶层包含 ``dimensions`` 和 ``scale``
          （工程评估格式）。"""
    if "rubric_core" in rubric_file_data:
        return _build_rubric_snapshot_legacy(rubric_file_data)
    return _build_rubric_snapshot_task(rubric_file_data)


def _build_rubric_snapshot_legacy(rubric_file_data: dict[str, Any]) -> RubricSnapshot:
    """从完整 rubric_core 格式构建 RubricSnapshot。"""
    core = rubric_file_data["rubric_core"]
    dimensions: list[dict[str, Any]] = core["dimensions"]
    scales: list[dict[str, Any]] = core["scales"]
    return RubricSnapshot(
        rubric_id=core["rubric_id"],
        rubric_version=core["rubric_version"],
        rubric_name=core["rubric_name"],
        dimensions=dimensions,
        scales=scales,
        dimension_by_id={d["dimension_id"]: d for d in dimensions},
        dimension_by_code={d["code"]: d for d in dimensions},
        scale_by_id={s["scale_id"]: s for s in scales},
    )


def _build_rubric_snapshot_task(rubric_file_data: dict[str, Any]) -> RubricSnapshot:
    """从简化的任务/维度 rubric 格式构建 RubricSnapshot。
    
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


def _build_policy_snapshot(
    adj_file_data: dict[str, Any],
    agg_file_data: dict[str, Any],
    exp_file_data: dict[str, Any],
    chunking_file_data: dict[str, Any] | None = None,
    scoring_context_file_data: dict[str, Any] | None = None,
) -> PolicySnapshot:
    """根据每个文件的内部 policy 内容构建 PolicySnapshot。
    
        ``scoring_context_file_data`` 可能有两种格式：
        - 遗留 ScoringContextSchema：``scoring_context`` 键映射到一个字典
          （包含 context_id、role_description 等）。为向后兼容，存储该内部字典。
        - 任务上下文格式：``scoring_context`` 键映射到
          ``{code, calibration_notes}`` 条目列表。存储整个文件数据字典，以便 runner 访问 ``material_context`` 和其他字段。"""
    adj_policy = adj_file_data["adjudication_policy"]
    agg_policy = agg_file_data["aggregation_policy"]
    exp_policy = exp_file_data.get("explanation_policy", {})

    chunking_policy: dict[str, Any] = {}
    if isinstance(chunking_file_data, dict):
        chunking_policy = dict(chunking_file_data.get("chunking_policy") or {})

    scoring_context: dict[str, Any] = {}
    if isinstance(scoring_context_file_data, dict):
        sc = scoring_context_file_data.get("scoring_context")
        if isinstance(sc, list):
            # 任务上下文格式——存储完整文件数据供下游访问
            scoring_context = dict(scoring_context_file_data)
        elif isinstance(sc, dict):
            # 遗留 ScoringContextSchema 格式——提取内部字典
            scoring_context = dict(sc)

    adj_version = adj_policy.get("policy_version") or adj_policy.get("policy_id", "unknown")
    agg_version = agg_policy.get("policy_version") or agg_policy.get("policy_id", "unknown")
    exp_version = exp_policy.get("policy_version", "unknown")
    policy_version = f"adj:{adj_version}|agg:{agg_version}|exp:{exp_version}"

    return PolicySnapshot(
        adjudication_policy=adj_policy,
        aggregation_policy=agg_policy,
        explanation_policy=exp_policy,
        policy_version=policy_version,
        chunking_policy=chunking_policy,
        scoring_context=scoring_context,
    )
