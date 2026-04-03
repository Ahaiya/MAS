from __future__ import annotations

from pathlib import Path

import yaml

from src.outer_loop.task_setup import TaskSetupManager
from src.providers.base import (
    BaseProvider,
    LLMRequest,
    LLMResponse,
    ProviderCapability,
    TokenUsage,
)


class _StaticTaskSetupProvider(BaseProvider):
    @property
    def name(self) -> str:
        return "task_setup_test_provider"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.TEXT_COMPLETION})

    def complete(self, request: LLMRequest) -> LLMResponse:
        _ = request
        content = (
            "```yaml\n"
            "task_setup_draft:\n"
            "  task_id: \"task_demo\"\n"
            "  task_name: \"示例任务\"\n"
            "  task_brief_summary: \"围绕一个工程项目的需求分析、方案择优与伦理责任进行评价。\"\n"
            "  selected_indicators:\n"
            "    - source_dimension_code: \"A\"\n"
            "      source_dimension_name: \"问题认知与分析能力\"\n"
            "      source_indicator_id: \"A4\"\n"
            "      source_indicator_name: \"用户需求与痛点分析\"\n"
            "      selection_reason: \"任务需要分析用户、痛点与成功标准。\"\n"
            "      observation_points:\n"
            "        - observation_id: \"user_needs_analysis\"\n"
            "          name: \"用户需求识别\"\n"
            "          focus: \"是否准确识别用户、场景、痛点与成功标准。\"\n"
            "          anchors:\n"
            "            excellent: \"能够精准识别关键用户、痛点与成功标准。\"\n"
            "            good: \"能够较好识别用户需求与主要痛点。\"\n"
            "            pass: \"能够识别基本需求与显性痛点。\"\n"
            "            needs_improvement: \"需求识别模糊，痛点界定不清。\"\n"
            "    - source_dimension_code: \"B\"\n"
            "      source_dimension_name: \"方案设计与创新能力\"\n"
            "      source_indicator_id: \"B3\"\n"
            "      source_indicator_name: \"解决方案生成与择优\"\n"
            "      selection_reason: \"任务需要比较多种方案并说明取舍。\"\n"
            "      observation_points:\n"
            "        - observation_id: \"solution_generation\"\n"
            "          name: \"方案比较与择优\"\n"
            "          focus: \"是否提出备选方案并基于工程标准择优。\"\n"
            "          anchors:\n"
            "            excellent: \"能够提出多种方案并基于多维标准清晰择优。\"\n"
            "            good: \"能够提出多个方案并说明主要取舍。\"\n"
            "            pass: \"能够提出至少两个方案并做初步比较。\"\n"
            "            needs_improvement: \"方案单一或缺乏明确比较依据。\"\n"
            "    - source_dimension_code: \"F\"\n"
            "      source_dimension_name: \"职业发展与责任意识\"\n"
            "      source_indicator_id: \"F2\"\n"
            "      source_indicator_name: \"工程伦理与社会责任\"\n"
            "      selection_reason: \"任务需要回应伦理风险与责任边界。\"\n"
            "      observation_points:\n"
            "        - observation_id: \"engineering_ethics\"\n"
            "          name: \"伦理与责任判断\"\n"
            "          focus: \"是否识别伦理风险、规范要求与社会影响。\"\n"
            "          anchors:\n"
            "            excellent: \"能够系统识别伦理风险并给出负责任的工程决策。\"\n"
            "            good: \"能够结合规范说明主要伦理风险与应对方式。\"\n"
            "            pass: \"能够指出基本伦理问题并给出初步回应。\"\n"
            "            needs_improvement: \"缺乏对伦理风险和责任问题的识别。\"\n"
            "```"
        )
        usage = TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20)
        return LLMResponse(
            content=content,
            structured_data=None,
            usage=usage,
            provider_name=self.name,
            model_id="test-model",
        )


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def test_task_setup_confirm_writes_frozen_files_and_updates_bundle(tmp_path: Path, monkeypatch) -> None:
    configs_root = tmp_path / "configs"
    experiments_root = tmp_path / "experiments"
    bundle_path = configs_root / "bundles" / "engineering_eval_baseline.bundle.yaml"
    aggregation_path = (
        configs_root / "policies" / "aggregation" / "engineering_eval_aggregation.yaml"
    )

    (configs_root / "rubrics" / "source").mkdir(parents=True, exist_ok=True)
    (configs_root / "rubrics" / "source" / "rubric.md").write_text(
        "# rubric source\n",
        encoding="utf-8",
    )
    _write_yaml(
        bundle_path,
        {
            "schema_version": "2.0",
            "artifact_bundle": {
                "bundle_id": "engineering_eval_baseline",
                "bundle_version": "v1",
                "bundle_name": "bundle",
                "description": "bundle",
                "rubric_core_ref": "rubric://task_bootstrap_rubric/v1",
                "rubric_source_file": "rubrics/tasks/task_bootstrap_rubric.yaml",
                "adjudication_policy_ref": "policy://engineering_eval_adjudication/v1",
                "adjudication_source_file": "policies/adjudication/engineering_eval_adjudication.yaml",
                "aggregation_policy_ref": "policy://engineering_eval_aggregation/v1",
                "aggregation_source_file": "policies/aggregation/engineering_eval_aggregation.yaml",
                "explanation_policy_ref": "explain://engineering_eval_explanation/v1",
                "explanation_source_file": "policies/explanation/engineering_eval_explanation.yaml",
                "operational_prompt_recipe_ref": "ops://prompts/engineering_eval_baseline/v1",
                "prompt_templates": ["prompts/scoring.yaml"],
                "scoring_context_ref": "context://task_bootstrap_scoring_context/v1",
                "scoring_context_source_file": "prompts/tasks/task_bootstrap_scoring_context.yaml",
                "freeze_hash": "TBD",
                "freeze_timestamp": "2026-04-02",
                "source_documents": ["configs/rubrics/source/rubric.md"],
                "validation_rules": [],
                "provider_config": {
                    "default": {
                        "api_key_env": "LLM_API_KEY",
                        "model": "deepseek-chat",
                        "api_base": "https://api.deepseek.com/v1",
                        "params": {"temperature": 0.0},
                    }
                },
                "operational_params": {"max_retries": 1},
                "metadata": {"active_task_id": "bootstrap"},
            },
        },
    )
    _write_yaml(
        aggregation_path,
        {
            "schema_version": "2.0",
            "aggregation_policy": {
                "policy_id": "engineering_eval_aggregation",
                "policy_version": "v1",
                "policy_name": "aggregation",
                "description": "old",
                "outputs": [
                    {"output_id": "trait_scores", "type": "dimension_scores", "description": "old", "always_produce": True},
                    {"output_id": "composite_score", "type": "weighted_sum", "description": "old", "always_produce": True},
                ],
                "composite_formula": [
                    {
                        "variant_id": "without_resolution",
                        "applies_when": "resolution_not_used",
                        "description": "old",
                        "source_raters": ["rater_1", "rater_2"],
                        "aggregation_method": "average_per_trait_then_weighted_sum",
                        "weights": {"legacy_dim": 1},
                        "formula_representation": "legacy",
                    },
                    {
                        "variant_id": "with_resolution",
                        "applies_when": "resolution_used",
                        "description": "old",
                        "source_raters": ["rater_3"],
                        "aggregation_method": "average_per_trait_then_weighted_sum",
                        "weights": {"legacy_dim": 1},
                        "formula_representation": "legacy",
                    },
                ],
                "notes": ["legacy"],
                "metadata": {},
            },
        },
    )

    monkeypatch.setattr(
        "src.outer_loop.task_setup.ConfigCompiler.compile",
        lambda self, bundle_path: object(),
    )

    manager = TaskSetupManager(
        provider=_StaticTaskSetupProvider(),
        configs_root=configs_root,
        experiments_root=experiments_root,
        bundle_path=bundle_path,
        aggregation_policy_path=aggregation_path,
    )

    draft = manager.start(task_id="demo", task_brief="请评价一个工程项目方案。")
    assert draft["task_setup_draft"]["task_id"] == "task_demo"

    result = manager.confirm(task_id="demo")
    assert result.task_id == "task_demo"
    assert result.rubric_path.exists()
    assert result.scoring_context_path.exists()

    rubric_doc = yaml.safe_load(result.rubric_path.read_text(encoding="utf-8"))
    scale = rubric_doc["rubric_core"]["scales"][0]
    assert scale["scale_id"] == "ordinal_5"
    assert scale["min"] == 1
    assert scale["max"] == 5
    for dim in rubric_doc["rubric_core"]["dimensions"]:
        assert dim["scale_ref"] == "ordinal_5"
        assert [level["rank"] for level in dim["levels"]] == [1, 2, 3, 4, 5]

    bundle_doc = yaml.safe_load(bundle_path.read_text(encoding="utf-8"))
    assert bundle_doc["artifact_bundle"]["rubric_source_file"] == "rubrics/tasks/task_demo_rubric.yaml"
    assert bundle_doc["artifact_bundle"]["scoring_context_source_file"] == (
        "prompts/tasks/task_demo_scoring_context.yaml"
    )

    aggregation_doc = yaml.safe_load(aggregation_path.read_text(encoding="utf-8"))
    weights = aggregation_doc["aggregation_policy"]["composite_formula"][0]["weights"]
    assert weights == {
        "user_needs_analysis": 1,
        "solution_generation": 1,
        "engineering_ethics": 1,
    }
    assert aggregation_doc["aggregation_policy"]["description"].endswith("量表保持 1-5。")
    assert aggregation_doc["aggregation_policy"]["outputs"][1]["type"] == "weighted_average"
    assert aggregation_doc["aggregation_policy"]["outputs"][1]["description"].endswith("量表保持 1-5）")
    assert (
        aggregation_doc["aggregation_policy"]["composite_formula"][0]["aggregation_method"]
        == "average_per_trait_then_weighted_average"
    )
    assert (
        aggregation_doc["aggregation_policy"]["composite_formula"][1]["aggregation_method"]
        == "direct_weighted_average"
    )
