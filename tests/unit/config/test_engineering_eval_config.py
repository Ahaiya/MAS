"""
Phase 1 验收测试 — engineering eval 配置加载层

验证以下内容：
1. 简化 Schema 类能正确解析 engineering eval 的各 YAML 文件
2. ConfigResolver 能加载简化格式 bundle（无 artifact_bundle 包装）
3. ConfigCompiler 能编译 engineering_eval_baseline bundle
   - rubric_snapshot 包含 3 个维度 a4_1/a4_2/a4_3
   - policy_snapshot.scoring_context 包含 material_context
"""

import pytest
import yaml
from pathlib import Path

from src.config.schema import (
    SimplifiedBundleFileSchema,
    TaskRubricFileSchema,
    TaskContextFileSchema,
    SimplifiedAdjudicationFileSchema,
    SimplifiedAggregationFileSchema,
    SimplifiedChunkingPolicyFileSchema,
)
from src.config.resolver import ConfigResolver, ResolverError
from src.config.compiler import ConfigCompiler
from src.contracts.artifact_bundle import ArtifactBundle, ResolvedArtifactBundle

CONFIGS_ROOT = Path("configs")
BUNDLE_PATH = CONFIGS_ROOT / "bundles/engineering_eval_baseline.bundle.yaml"


# ── Schema 验证测试 ────────────────────────────────────────────────────────────


class TestSimplifiedBundleFileSchema:
    def test_validates_engineering_eval_bundle(self):
        data = yaml.safe_load(BUNDLE_PATH.read_text())
        schema = SimplifiedBundleFileSchema(**data)
        assert schema.bundle_id == "engineering_eval_baseline"
        assert schema.active_task_id == "a4"
        assert "chunking" in schema.prompts
        assert "adjudication" in schema.policies

    def test_has_required_prompt_keys(self):
        data = yaml.safe_load(BUNDLE_PATH.read_text())
        schema = SimplifiedBundleFileSchema(**data)
        for key in ("chunking", "evidence_extraction", "scoring", "explanation"):
            assert key in schema.prompts, f"Missing prompt key: {key}"

    def test_has_required_policy_keys(self):
        data = yaml.safe_load(BUNDLE_PATH.read_text())
        schema = SimplifiedBundleFileSchema(**data)
        for key in ("chunking", "adjudication", "aggregation"):
            assert key in schema.policies, f"Missing policy key: {key}"


class TestTaskRubricFileSchema:
    def test_validates_task_a4_rubric(self):
        path = CONFIGS_ROOT / "rubrics/tasks/task_a4_rubric.yaml"
        data = yaml.safe_load(path.read_text())
        schema = TaskRubricFileSchema(**data)
        assert schema.task_id == "a4"
        assert len(schema.dimensions) == 3
        assert schema.scale["min"] == 1
        assert schema.scale["max"] == 5

    def test_dimension_codes(self):
        path = CONFIGS_ROOT / "rubrics/tasks/task_a4_rubric.yaml"
        data = yaml.safe_load(path.read_text())
        schema = TaskRubricFileSchema(**data)
        codes = [d["code"] for d in schema.dimensions]
        assert "A4-1" in codes
        assert "A4-2" in codes
        assert "A4-3" in codes

    def test_anchors_have_5_levels(self):
        path = CONFIGS_ROOT / "rubrics/tasks/task_a4_rubric.yaml"
        data = yaml.safe_load(path.read_text())
        schema = TaskRubricFileSchema(**data)
        for dim in schema.dimensions:
            assert len(dim["anchors"]) == 5, f"{dim['code']} should have 5 anchor levels"


class TestTaskContextFileSchema:
    def test_validates_task_a4_context(self):
        path = CONFIGS_ROOT / "tasks/task_a4_context.yaml"
        data = yaml.safe_load(path.read_text())
        schema = TaskContextFileSchema(**data)
        assert schema.material_context["type"] == "conversation"
        assert "evidence_focus" in schema.material_context

    def test_scoring_context_is_list(self):
        path = CONFIGS_ROOT / "tasks/task_a4_context.yaml"
        data = yaml.safe_load(path.read_text())
        schema = TaskContextFileSchema(**data)
        assert isinstance(schema.scoring_context, list)
        codes = [item["code"] for item in schema.scoring_context]
        assert "A4-1" in codes
        assert "A4-2" in codes
        assert "A4-3" in codes


class TestSimplifiedPolicySchemas:
    def test_adjudication(self):
        path = CONFIGS_ROOT / "policies/adjudication/engineering_eval_adjudication.yaml"
        data = yaml.safe_load(path.read_text())
        schema = SimplifiedAdjudicationFileSchema(**data)
        assert "policy_id" in schema.adjudication_policy
        assert "triggers" in schema.adjudication_policy

    def test_aggregation(self):
        path = CONFIGS_ROOT / "policies/aggregation/engineering_eval_aggregation.yaml"
        data = yaml.safe_load(path.read_text())
        schema = SimplifiedAggregationFileSchema(**data)
        assert "policy_id" in schema.aggregation_policy
        assert "composite_formula" in schema.aggregation_policy

    def test_chunking(self):
        path = CONFIGS_ROOT / "policies/chunking/engineering_eval_chunking.yaml"
        data = yaml.safe_load(path.read_text())
        schema = SimplifiedChunkingPolicyFileSchema(**data)
        assert "policy_id" in schema.chunking_policy
        assert "document_processing" in schema.chunking_policy


# ── Resolver 测试 ─────────────────────────────────────────────────────────────


class TestConfigResolverSimplifiedBundle:
    @pytest.fixture
    def resolver(self):
        return ConfigResolver(CONFIGS_ROOT)

    def test_returns_artifact_bundle(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert isinstance(bundle, ArtifactBundle)

    def test_bundle_id(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert bundle.bundle_id == "engineering_eval_baseline"

    def test_rubric_ref_source_file(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert bundle.rubric_ref.source_file == "rubrics/tasks/task_a4_rubric.yaml"

    def test_scoring_context_ref_source_file(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert bundle.scoring_context_ref is not None
        assert bundle.scoring_context_ref.source_file == "tasks/task_a4_context.yaml"

    def test_chunking_ref_source_file(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert bundle.chunking_policy_ref is not None
        assert "engineering_eval_chunking" in bundle.chunking_policy_ref.source_file

    def test_prompt_refs_count(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert len(bundle.prompt_refs) == 4

    def test_explanation_policy_ref_is_none(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert bundle.explanation_policy_ref is None

    def test_not_frozen(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        assert not bundle.is_frozen()

    def test_load_rubric_artifact(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        loaded = resolver.load_artifact(bundle.rubric_ref)
        assert loaded.loaded_data is not None
        assert "dimensions" in loaded.loaded_data

    def test_load_context_artifact(self, resolver):
        bundle = resolver.load_bundle_file(BUNDLE_PATH)
        loaded = resolver.load_artifact(bundle.scoring_context_ref)
        assert loaded.loaded_data is not None
        assert "material_context" in loaded.loaded_data


# ── Compiler 测试 ─────────────────────────────────────────────────────────────


class TestConfigCompilerEngineeringEval:
    @pytest.fixture
    def result(self):
        compiler = ConfigCompiler(CONFIGS_ROOT)
        return compiler.compile(BUNDLE_PATH)

    def test_returns_resolved_bundle(self, result):
        assert isinstance(result, ResolvedArtifactBundle)

    def test_bundle_is_frozen(self, result):
        assert result.artifact_bundle.is_frozen()

    def test_rubric_snapshot_has_3_dimensions(self, result):
        assert len(result.rubric_snapshot.dimensions) == 3

    def test_rubric_snapshot_dimension_ids(self, result):
        ids = set(result.rubric_snapshot.dimension_by_id.keys())
        assert ids == {"a4_1", "a4_2", "a4_3"}

    def test_rubric_snapshot_dimension_codes(self, result):
        codes = set(result.rubric_snapshot.dimension_by_code.keys())
        assert codes == {"A4-1", "A4-2", "A4-3"}

    def test_rubric_snapshot_scale(self, result):
        scale = result.rubric_snapshot.scale_by_id.get("ordinal_1_5")
        assert scale is not None
        assert scale["min"] == 1
        assert scale["max"] == 5

    def test_dimension_has_5_levels(self, result):
        dim = result.rubric_snapshot.dimension_by_id["a4_1"]
        assert len(dim["levels"]) == 5

    def test_level_rank_5_summary_is_excellent(self, result):
        dim = result.rubric_snapshot.dimension_by_id["a4_1"]
        rank5 = next(lv for lv in dim["levels"] if lv["rank"] == 5)
        assert rank5["summary"] == "优秀"

    def test_level_rank_1_summary_is_needs_improvement(self, result):
        dim = result.rubric_snapshot.dimension_by_id["a4_1"]
        rank1 = next(lv for lv in dim["levels"] if lv["rank"] == 1)
        assert rank1["summary"] == "待改进"

    def test_policy_snapshot_has_adjudication_triggers(self, result):
        assert "triggers" in result.policy_snapshot.adjudication_policy

    def test_policy_snapshot_has_composite_formula(self, result):
        assert "composite_formula" in result.policy_snapshot.aggregation_policy

    def test_policy_snapshot_scoring_context_has_material_context(self, result):
        ctx = result.policy_snapshot.scoring_context
        assert "material_context" in ctx
        assert ctx["material_context"]["type"] == "conversation"

    def test_policy_snapshot_scoring_context_has_evidence_focus(self, result):
        ctx = result.policy_snapshot.scoring_context
        assert ctx["material_context"].get("evidence_focus")

    def test_rubric_snapshot_preserves_indicator_description(self, result):
        assert result.rubric_snapshot.indicator_description
        assert "最终用户" in result.rubric_snapshot.indicator_description

    def test_rubric_snapshot_preserves_raw_task_rubric(self, result):
        raw = result.rubric_snapshot.raw_task_rubric
        assert raw["task_id"] == "a4"
        assert len(raw["dimensions"]) == 3

    def test_policy_snapshot_scoring_context_list(self, result):
        ctx = result.policy_snapshot.scoring_context
        assert isinstance(ctx.get("scoring_context"), list)

    def test_chunking_policy_present(self, result):
        policy = result.policy_snapshot.chunking_policy
        assert policy.get("policy_id") == "engineering_eval_chunking"

    def test_prompt_templates_count(self, result):
        assert len(result.prompt_templates) == 4

    def test_total_hash_is_valid(self, result):
        assert result.total_hash is not None
        assert len(result.total_hash) == 64

    def test_validate_score_within_range(self, result):
        assert result.rubric_snapshot.validate_score("a4_1", 3) is True
        assert result.rubric_snapshot.validate_score("a4_1", 5) is True
        assert result.rubric_snapshot.validate_score("a4_1", 0) is False
        assert result.rubric_snapshot.validate_score("a4_1", 6) is False
