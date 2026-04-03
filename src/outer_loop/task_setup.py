"""Task setup workflow for engineering evaluation tasks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.config.compiler import ConfigCompiler
from src.providers.base import BaseProvider, LLMRequest

_TASK_SETUP_SYSTEM_PROMPT = """You are the MAS task-setup agent.

Mission:
- Read the canonical rubric source and the teacher's Task Brief.
- Recommend a task-specific subset of secondary indicators.
- Produce observation-point drafts that can be compiled into a frozen runtime rubric.

Rules:
- Output exactly one fenced YAML block and no prose outside it.
- Keep YAML keys and structural identifiers in English exactly as requested.
- Use Simplified Chinese for all user-visible descriptive strings.
- Default to 3 observation points in total unless the revision instruction explicitly asks otherwise.
- `task_id` and every `observation_id` must be lowercase snake_case ASCII identifiers.
- Each observation point must include four anchor bands:
  `excellent`, `good`, `pass`, `needs_improvement`.
- In revision mode, the user's latest instruction has highest priority.

YAML schema:
```yaml
task_setup_draft:
  task_id: "task_example"
  task_name: "任务名称"
  task_brief_summary: "对任务背景和评价范围的中文概括"
  selected_indicators:
    - source_dimension_code: "A"
      source_dimension_name: "问题认知与分析能力"
      source_indicator_id: "A4"
      source_indicator_name: "用户需求与痛点分析"
      selection_reason: "为什么这个二级指标适合本次任务"
      observation_points:
        - observation_id: "user_needs_analysis"
          name: "观测点名称"
          focus: "这个观测点具体看什么"
          anchors:
            excellent: "优秀 4-5 分锚点"
            good: "良好 3-4 分锚点"
            pass: "合格 2-3 分锚点"
            needs_improvement: "待改进 1-2 分锚点"
```
"""

_TASK_SETUP_USER_TEMPLATE = """Mode: {mode}
Canonical task_id: {task_id}

## Rubric Source
{rubric_source}

## Task Brief
{task_brief}

## Current Draft
{current_draft}

## Latest Instruction
{instruction}

Return the YAML block defined by the system prompt.
"""


@dataclass(frozen=True)
class TaskSetupPaths:
    session_dir: Path
    draft_path: Path
    brief_path: Path
    rubric_path: Path
    scoring_context_path: Path


@dataclass(frozen=True)
class TaskConfirmResult:
    task_id: str
    draft_path: Path
    rubric_path: Path
    scoring_context_path: Path
    bundle_path: Path
    aggregation_policy_path: Path


class TaskSetupManager:
    """Manage the CLI-driven task setup workflow."""

    def __init__(
        self,
        *,
        provider: BaseProvider | None = None,
        configs_root: Path | str = "configs",
        experiments_root: Path | str = "experiments",
        bundle_path: Path | str = "configs/bundles/engineering_eval_baseline.bundle.yaml",
        aggregation_policy_path: Path | str = (
            "configs/policies/aggregation/engineering_eval_aggregation.yaml"
        ),
    ) -> None:
        self.provider = provider
        self.configs_root = Path(configs_root)
        self.experiments_root = Path(experiments_root)
        self.bundle_path = Path(bundle_path)
        self.aggregation_policy_path = Path(aggregation_policy_path)
        self.rubric_source_path = self.configs_root / "rubrics" / "source" / "rubric.md"

    def start(self, *, task_id: str, task_brief: str) -> dict[str, Any]:
        canonical_id = _canonical_task_id(task_id)
        paths = self._paths_for(canonical_id)
        paths.session_dir.mkdir(parents=True, exist_ok=True)
        paths.brief_path.write_text(task_brief.strip() + "\n", encoding="utf-8")

        draft = self._generate_draft(
            mode="draft",
            canonical_task_id=canonical_id,
            task_brief=task_brief,
            current_draft=None,
            instruction="请先给出推荐指标和观测点草案。",
        )
        self._write_yaml(paths.draft_path, draft)
        return draft

    def show(self, *, task_id: str) -> dict[str, Any]:
        canonical_id = _canonical_task_id(task_id)
        return self._load_draft(canonical_id)

    def revise(self, *, task_id: str, instruction: str) -> dict[str, Any]:
        canonical_id = _canonical_task_id(task_id)
        paths = self._paths_for(canonical_id)
        current_draft = self._load_draft(canonical_id)
        if not paths.brief_path.exists():
            raise FileNotFoundError(f"Task brief not found: {paths.brief_path}")
        task_brief = paths.brief_path.read_text(encoding="utf-8")

        revised = self._generate_draft(
            mode="revise",
            canonical_task_id=canonical_id,
            task_brief=task_brief,
            current_draft=current_draft,
            instruction=instruction,
        )
        self._write_yaml(paths.draft_path, revised)
        return revised

    def confirm(self, *, task_id: str) -> TaskConfirmResult:
        canonical_id = _canonical_task_id(task_id)
        paths = self._paths_for(canonical_id)
        draft = self._load_draft(canonical_id)

        if paths.rubric_path.exists():
            raise FileExistsError(f"Frozen task rubric already exists: {paths.rubric_path}")
        if paths.scoring_context_path.exists():
            raise FileExistsError(
                f"Task scoring context already exists: {paths.scoring_context_path}"
            )

        rubric_doc = _compile_task_rubric(draft)
        scoring_context_doc = _build_minimal_scoring_context(canonical_id)

        bundle_before = self.bundle_path.read_text(encoding="utf-8")
        aggregation_before = self.aggregation_policy_path.read_text(encoding="utf-8")
        created_paths: list[Path] = []

        try:
            self._write_yaml(paths.rubric_path, rubric_doc)
            created_paths.append(paths.rubric_path)

            self._write_yaml(paths.scoring_context_path, scoring_context_doc)
            created_paths.append(paths.scoring_context_path)

            bundle_doc = _safe_load_yaml_text(bundle_before)
            updated_bundle = _update_bundle_for_task(bundle_doc, canonical_id, draft)
            self._write_yaml(self.bundle_path, updated_bundle)

            aggregation_doc = _safe_load_yaml_text(aggregation_before)
            updated_aggregation = _update_aggregation_for_task(aggregation_doc, draft)
            self._write_yaml(self.aggregation_policy_path, updated_aggregation)

            ConfigCompiler(configs_root=self.configs_root).compile(self.bundle_path)
        except Exception:
            self.bundle_path.write_text(bundle_before, encoding="utf-8")
            self.aggregation_policy_path.write_text(aggregation_before, encoding="utf-8")
            for path in created_paths:
                if path.exists():
                    path.unlink()
            raise

        return TaskConfirmResult(
            task_id=canonical_id,
            draft_path=paths.draft_path,
            rubric_path=paths.rubric_path,
            scoring_context_path=paths.scoring_context_path,
            bundle_path=self.bundle_path,
            aggregation_policy_path=self.aggregation_policy_path,
        )

    def format_draft(self, draft: dict[str, Any]) -> str:
        return yaml.safe_dump(draft, sort_keys=False, allow_unicode=True)

    def _paths_for(self, canonical_task_id: str) -> TaskSetupPaths:
        session_dir = self.experiments_root / "task_setup" / canonical_task_id
        return TaskSetupPaths(
            session_dir=session_dir,
            draft_path=session_dir / "draft.yaml",
            brief_path=session_dir / "task_brief.md",
            rubric_path=self.configs_root / "rubrics" / "tasks" / f"{canonical_task_id}_rubric.yaml",
            scoring_context_path=(
                self.configs_root
                / "prompts"
                / "tasks"
                / f"{canonical_task_id}_scoring_context.yaml"
            ),
        )

    def _generate_draft(
        self,
        *,
        mode: str,
        canonical_task_id: str,
        task_brief: str,
        current_draft: dict[str, Any] | None,
        instruction: str,
    ) -> dict[str, Any]:
        if self.provider is None:
            raise ValueError("Task setup provider is required for draft/revise operations.")
        if not self.rubric_source_path.exists():
            raise FileNotFoundError(f"Rubric source not found: {self.rubric_source_path}")

        current_text = (
            yaml.safe_dump(current_draft, sort_keys=False, allow_unicode=True)
            if current_draft is not None
            else "(none)"
        )
        user_prompt = _TASK_SETUP_USER_TEMPLATE.format(
            mode=mode,
            task_id=canonical_task_id,
            rubric_source=self.rubric_source_path.read_text(encoding="utf-8"),
            task_brief=task_brief.strip(),
            current_draft=current_text,
            instruction=instruction.strip(),
        )
        response = self.provider.complete(
            LLMRequest(
                prompt=user_prompt,
                system=_TASK_SETUP_SYSTEM_PROMPT,
                metadata={
                    "task_setup_mode": mode,
                    "task_id": canonical_task_id,
                },
            )
        )
        draft = _parse_task_setup_response(response.content)
        return _normalize_task_setup_draft(draft, canonical_task_id=canonical_task_id)

    def _load_draft(self, canonical_task_id: str) -> dict[str, Any]:
        paths = self._paths_for(canonical_task_id)
        if not paths.draft_path.exists():
            raise FileNotFoundError(f"Task setup draft not found: {paths.draft_path}")
        return _normalize_task_setup_draft(
            _safe_load_yaml_text(paths.draft_path.read_text(encoding="utf-8")),
            canonical_task_id=canonical_task_id,
        )

    def _write_yaml(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )


def _canonical_task_id(raw_task_id: str) -> str:
    cleaned = _normalize_identifier(raw_task_id, fallback="task_auto")
    if cleaned.startswith("task_"):
        return cleaned
    return f"task_{cleaned}"


def _normalize_identifier(raw: Any, *, fallback: str) -> str:
    text = str(raw or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or fallback


def _safe_load_yaml_text(raw_text: str) -> dict[str, Any]:
    loaded = yaml.safe_load(raw_text) if raw_text.strip() else {}
    if isinstance(loaded, dict):
        return loaded
    raise ValueError("YAML payload must decode to a mapping")


def _parse_task_setup_response(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if not text:
        raise ValueError("Empty task-setup response")

    match = re.search(r"```(?:yaml|yml)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    yaml_text = match.group(1).strip() if match else text
    loaded = _safe_load_yaml_text(yaml_text)
    draft = loaded.get("task_setup_draft")
    if not isinstance(draft, dict):
        raise ValueError("Missing task_setup_draft in response")
    return {"task_setup_draft": draft}


def _normalize_task_setup_draft(
    payload: dict[str, Any],
    *,
    canonical_task_id: str,
) -> dict[str, Any]:
    draft = payload.get("task_setup_draft")
    if not isinstance(draft, dict):
        raise ValueError("task_setup_draft must be a mapping")

    selected_indicators = draft.get("selected_indicators")
    if not isinstance(selected_indicators, list) or not selected_indicators:
        raise ValueError("selected_indicators must be a non-empty list")

    normalized_indicators: list[dict[str, Any]] = []
    observation_counter = 0
    seen_observation_ids: set[str] = set()
    for ind_index, raw_indicator in enumerate(selected_indicators, start=1):
        if not isinstance(raw_indicator, dict):
            raise ValueError("each selected_indicator must be a mapping")
        observation_points = raw_indicator.get("observation_points")
        if not isinstance(observation_points, list) or not observation_points:
            raise ValueError("each selected_indicator must contain observation_points")

        normalized_points: list[dict[str, Any]] = []
        for obs_index, raw_point in enumerate(observation_points, start=1):
            if not isinstance(raw_point, dict):
                raise ValueError("each observation_point must be a mapping")
            observation_counter += 1
            observation_id = _normalize_identifier(
                raw_point.get("observation_id"),
                fallback=f"observation_{observation_counter}",
            )
            if observation_id in seen_observation_ids:
                suffix = 2
                while f"{observation_id}_{suffix}" in seen_observation_ids:
                    suffix += 1
                observation_id = f"{observation_id}_{suffix}"
            seen_observation_ids.add(observation_id)
            anchors = raw_point.get("anchors")
            if not isinstance(anchors, dict):
                raise ValueError("anchors must be a mapping")
            normalized_points.append(
                {
                    "observation_id": observation_id,
                    "name": str(raw_point.get("name") or f"观测点 {observation_counter}").strip(),
                    "focus": str(raw_point.get("focus") or "").strip(),
                    "anchors": {
                        "excellent": str(anchors.get("excellent") or "").strip(),
                        "good": str(anchors.get("good") or "").strip(),
                        "pass": str(anchors.get("pass") or "").strip(),
                        "needs_improvement": str(
                            anchors.get("needs_improvement") or ""
                        ).strip(),
                    },
                }
            )

        normalized_indicators.append(
            {
                "source_dimension_code": str(
                    raw_indicator.get("source_dimension_code") or ""
                ).strip(),
                "source_dimension_name": str(
                    raw_indicator.get("source_dimension_name") or ""
                ).strip(),
                "source_indicator_id": str(raw_indicator.get("source_indicator_id") or "").strip(),
                "source_indicator_name": str(
                    raw_indicator.get("source_indicator_name") or ""
                ).strip(),
                "selection_reason": str(raw_indicator.get("selection_reason") or "").strip(),
                "observation_points": normalized_points,
            }
        )

    if observation_counter <= 0:
        raise ValueError("task setup draft must contain at least one observation point")

    return {
        "task_setup_draft": {
            "task_id": canonical_task_id,
            "task_name": str(draft.get("task_name") or "工程能力评价任务").strip(),
            "task_brief_summary": str(draft.get("task_brief_summary") or "").strip(),
            "selected_indicators": normalized_indicators,
        }
    }


def _flatten_observation_points(draft: dict[str, Any]) -> list[dict[str, Any]]:
    task_setup_draft = draft["task_setup_draft"]
    flattened: list[dict[str, Any]] = []
    for indicator in task_setup_draft["selected_indicators"]:
        for point in indicator["observation_points"]:
            flattened.append(
                {
                    "observation_id": point["observation_id"],
                    "name": point["name"],
                    "focus": point["focus"],
                    "anchors": dict(point["anchors"]),
                    "source_dimension_code": indicator["source_dimension_code"],
                    "source_dimension_name": indicator["source_dimension_name"],
                    "source_indicator_id": indicator["source_indicator_id"],
                    "source_indicator_name": indicator["source_indicator_name"],
                }
            )
    return flattened


def _compile_task_rubric(draft: dict[str, Any]) -> dict[str, Any]:
    task_setup_draft = draft["task_setup_draft"]
    task_id = task_setup_draft["task_id"]
    observation_points = _flatten_observation_points(draft)

    dimensions: list[dict[str, Any]] = []
    for point in observation_points:
        facet_id = f"{point['observation_id']}_evidence"
        dimensions.append(
            {
                "dimension_id": point["observation_id"],
                "code": point["source_indicator_id"] or point["observation_id"],
                "name": point["name"],
                "scale_ref": "ordinal_5",
                "description": point["focus"],
                "observation_schema": {
                    "required_facets": [facet_id],
                    "facet_descriptions": {
                        facet_id: point["focus"],
                    },
                },
                "evidence_requirements": {
                    "minimum_evidence_units": 1,
                    "allowed_evidence_scope": ["span", "global"],
                    "require_textual_grounding": True,
                },
                "levels": _levels_from_anchors(point["anchors"]),
                "metadata": {
                    "task_id": task_id,
                    "source_dimension_code": point["source_dimension_code"],
                    "source_dimension_name": point["source_dimension_name"],
                    "source_indicator_id": point["source_indicator_id"],
                    "source_indicator_name": point["source_indicator_name"],
                    "anchor_ladder": dict(point["anchors"]),
                },
            }
        )

    return {
        "schema_version": "2.0",
        "rubric_core": {
            "rubric_id": f"{task_id}_rubric",
            "rubric_version": "v1",
            "rubric_name": task_setup_draft["task_name"],
            "description": (
                task_setup_draft["task_brief_summary"]
                or "Task-specific frozen rubric generated by task setup."
            ),
            "scales": [
                {
                    "scale_id": "ordinal_5",
                    "type": "ordinal",
                    "min": 1,
                    "max": 5,
                    "canonical_score_type": "integer",
                    "display_overlays_allowed": True,
                }
            ],
            "dimensions": dimensions,
            "validation_rules": [
                {
                    "rule_id": "task_dimensions_non_empty",
                    "type": "task_setup",
                    "description": "Task rubric must contain at least one frozen observation point.",
                }
            ],
        },
    }


def _levels_from_anchors(anchors: dict[str, str]) -> list[dict[str, Any]]:
    excellent = anchors.get("excellent") or "表现优秀。"
    good = anchors.get("good") or "表现良好。"
    passed = anchors.get("pass") or "达到合格要求。"
    needs_improvement = anchors.get("needs_improvement") or "仍需明显改进。"
    return [
        {
            "rank": 1,
            "summary": "待改进",
            "descriptors": [needs_improvement],
        },
        {
            "rank": 2,
            "summary": "接近合格",
            "descriptors": [
                f"{needs_improvement} 但已出现少量可取迹象，若补足关键缺口可接近合格。"
            ],
        },
        {
            "rank": 3,
            "summary": "合格",
            "descriptors": [passed],
        },
        {
            "rank": 4,
            "summary": "良好",
            "descriptors": [good],
        },
        {
            "rank": 5,
            "summary": "优秀",
            "descriptors": [excellent],
        },
    ]


def _build_minimal_scoring_context(canonical_task_id: str) -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "scoring_context": {
            "context_id": f"{canonical_task_id}_scoring_context",
            "role_description": "",
            "dataset_notes": "",
            "score_anchors": [],
            "calibration_notes": "",
            "metadata": {
                "task_id": canonical_task_id,
                "status": "cold_start",
            },
        },
    }


def _update_bundle_for_task(
    bundle_doc: dict[str, Any],
    canonical_task_id: str,
    draft: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(bundle_doc)
    artifact_bundle = dict(updated.get("artifact_bundle") or {})
    artifact_bundle["rubric_core_ref"] = f"rubric://{canonical_task_id}_rubric/v1"
    artifact_bundle["rubric_source_file"] = f"rubrics/tasks/{canonical_task_id}_rubric.yaml"
    artifact_bundle["scoring_context_ref"] = f"context://{canonical_task_id}_scoring_context/v1"
    artifact_bundle["scoring_context_source_file"] = (
        f"prompts/tasks/{canonical_task_id}_scoring_context.yaml"
    )

    metadata = dict(artifact_bundle.get("metadata") or {})
    task_setup_draft = draft["task_setup_draft"]
    metadata["active_task_id"] = canonical_task_id.removeprefix("task_")
    metadata["selected_indicator_ids"] = [
        indicator["source_indicator_id"]
        for indicator in task_setup_draft["selected_indicators"]
        if indicator.get("source_indicator_id")
    ]
    metadata["observation_point_ids"] = [
        point["observation_id"] for point in _flatten_observation_points(draft)
    ]
    artifact_bundle["metadata"] = metadata
    updated["artifact_bundle"] = artifact_bundle
    return updated


def _update_aggregation_for_task(
    aggregation_doc: dict[str, Any],
    draft: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(aggregation_doc)
    policy = dict(updated.get("aggregation_policy") or {})
    observation_points = _flatten_observation_points(draft)
    weights = {point["observation_id"]: 1 for point in observation_points}

    outputs = list(policy.get("outputs") or [])
    if outputs:
        outputs[0] = dict(outputs[0])
        outputs[0]["description"] = f"{len(observation_points)} 个观测点的独立得分"
    if len(outputs) > 1:
        outputs[1] = dict(outputs[1])
        outputs[1]["description"] = (
            f"{len(observation_points)} 个观测点加权平均后的任务聚合得分（量表保持 1-5）"
        )
        outputs[1]["type"] = "weighted_average"
    policy["outputs"] = outputs

    formulae = []
    for raw_variant in list(policy.get("composite_formula") or []):
        variant = dict(raw_variant)
        variant["weights"] = dict(weights)
        if variant.get("applies_when") == "resolution_used":
            variant["aggregation_method"] = "direct_weighted_average"
            weighted_terms = " + ".join(
                f"{point['observation_id']}_R3*{weights[point['observation_id']]}"
                for point in observation_points
            )
        else:
            variant["aggregation_method"] = "average_per_trait_then_weighted_average"
            weighted_terms = " + ".join(
                f"(({point['observation_id']}_R1+{point['observation_id']}_R2)/2)*{weights[point['observation_id']]}"
                for point in observation_points
            )
        denominator = sum(weights.values()) or 1
        variant["formula_representation"] = f"({weighted_terms}) / ({denominator})"
        formulae.append(variant)
    policy["composite_formula"] = formulae
    policy["description"] = (
        f"{len(observation_points)} 个观测点加权平均得到任务聚合得分，量表保持 1-5。"
    )
    policy["notes"] = [
        f"最终聚合得分采用加权平均，保持原始量表 1-5"
    ] + [
        f"{point['observation_id']} = {point['source_indicator_id']}（{point['name']}）"
        for point in observation_points
    ]
    updated["aggregation_policy"] = policy
    return updated


__all__ = ["TaskConfirmResult", "TaskSetupManager"]
