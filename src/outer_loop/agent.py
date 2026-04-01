"""Outer-loop agent orchestration for iterative config optimization."""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Template

from src.outer_loop.experiments.batch_runner import RunResult, batch_eval
from src.outer_loop.experiments.experiment_log import ExperimentLog, IterationRecord
from src.outer_loop.optimization.config_patcher import ChangeProposal, ConfigPatcher
from src.outer_loop.optimization.search_policy import SearchPolicy
from src.outer_loop.probes import ProbeResult, run_probes
from src.providers.base import (
    BaseProvider,
    LLMRequest,
    LLMResponse,
    ProviderCapability,
    TokenUsage,
)

_DIM_ORDER = [
    "ideas_content",
    "organization",
    "voice",
    "word_choice",
    "sentence_fluency",
    "conventions",
]

_DEFAULT_PROBE_DESCRIPTIONS: dict[str, str] = {
    "coverage_probe": "Chunking/Coverage quality: recall, precision, boundary quality.",
    "evidence_quality_probe": "Evidence span quality: quote alignment and facet completeness.",
    "observation_confidence_probe": "Observation confidence distribution and low-confidence ratio.",
    "rater_consistency_probe": "Rater disagreement profile per dimension.",
    "conflict_pattern_probe": "Conflict type distribution and trigger rate.",
    "resolution_cost_probe": "Adjudication cost: third-rater and fallback rates.",
    "feedback_grounding_probe": "Descriptor-evidence grounding closure and violations.",
    "qwk_probe": "Per-dimension + composite QWK against labeled TSV.",
    "cost_probe": "Token, latency, and retry statistics from run trace.",
}


BatchEvalFn = Callable[..., list[RunResult]]
RunProbesFn = Callable[..., dict[str, ProbeResult]]


@dataclass(frozen=True)
class ReviewResult:
    verdict: str
    next_hypothesis: str


class RuleBasedOuterLoopProvider(BaseProvider):
    """Deterministic provider used by outer-loop smoke tests and local demos."""

    @property
    def name(self) -> str:
        return "outer_loop_rule_based"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset(
            {
                ProviderCapability.TEXT_COMPLETION,
                ProviderCapability.STRUCTURED_OUTPUT,
            }
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        phase = str(request.metadata.get("outer_loop_phase") or "decision").strip().lower()
        if phase in {"review", "phase4_review"}:
            content = (
                "```yaml\n"
                "verdict: \"no-improvement\"\n"
                "next_hypothesis: "
                "\"Shift to a nearby scoring or extraction unit with clearer probe signal.\"\n"
                "```"
            )
        else:
            content = (
                "```yaml\n"
                "change_proposal:\n"
                "  change_unit: \"scoring.calibration_notes.ideas_content\"\n"
                "  change_type: \"field_patch\"\n"
                "  target_file: \"configs/prompts/scoring_context.yaml\"\n"
                "  target_path: \"scoring_context.calibration_notes\"\n"
                "  new_value: "
                "\"Calibration update: reward clear controlling idea and relevant evidence "
                "before style polish.\"\n"
                "  rationale: \"Baseline scaffold proposal for smoke runs.\"\n"
                "selected_probes:\n"
                "  - \"rater_consistency_probe\"\n"
                "  - \"qwk_probe\"\n"
                "```"
            )

        prompt_tokens = max(1, len(request.prompt.split()))
        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=max(1, prompt_tokens // 3),
            total_tokens=prompt_tokens + max(1, prompt_tokens // 3),
        )
        return LLMResponse(
            content=content,
            structured_data=None,
            usage=usage,
            provider_name=self.name,
            model_id=request.model_id or "outer-loop-rule-based-v1",
        )


class OuterLoopAgent:
    """Main outer-loop optimization agent with 5-phase iteration flow."""

    def __init__(
        self,
        *,
        experiment_log: ExperimentLog,
        config_patcher: ConfigPatcher,
        search_policy: SearchPolicy,
        provider: BaseProvider,
        bundle_path: Path | str,
        training_set_path: Path | str,
        artifacts_output_base: Path | str = "artifacts/eval",
        experiments_dir: Path | str = "experiments",
        prompts_dir: Path | str = "src/outer_loop/prompts",
        system_prompt_path: Path | str | None = None,
        user_prompt_template_path: Path | str | None = None,
        training_sample_ids: list[str] | None = None,
        target_qwk: float = 0.8,
        max_log_records: int = 8,
        mock_eval_provider: bool = False,
        batch_eval_fn: BatchEvalFn = batch_eval,
        run_probes_fn: RunProbesFn = run_probes,
        probe_descriptions: dict[str, str] | None = None,
    ) -> None:
        self.experiment_log = experiment_log
        self.config_patcher = config_patcher
        self.search_policy = search_policy
        self.provider = provider

        self.bundle_path = Path(bundle_path)
        self.training_set_path = Path(training_set_path)
        self.artifacts_output_base = Path(artifacts_output_base)
        self.experiments_dir = Path(experiments_dir)
        self.probes_dir = self.experiments_dir / "probes"
        self.cold_start_notes_path = self.experiments_dir / "cold_start_notes.md"

        prompts_root = Path(prompts_dir)
        self.system_prompt_path = Path(
            system_prompt_path or (prompts_root / "outer_loop_system.md")
        )
        self.user_prompt_template_path = Path(
            user_prompt_template_path or (prompts_root / "outer_loop_user_template.md")
        )

        if not self.system_prompt_path.exists():
            raise FileNotFoundError(f"System prompt not found: {self.system_prompt_path}")
        if not self.user_prompt_template_path.exists():
            raise FileNotFoundError(
                f"User prompt template not found: {self.user_prompt_template_path}"
            )

        self.system_prompt = self.system_prompt_path.read_text(encoding="utf-8")
        self.user_prompt_template = Template(
            self.user_prompt_template_path.read_text(encoding="utf-8")
        )

        self._training_sample_ids = (
            [str(item).strip() for item in training_sample_ids if str(item).strip()]
            if training_sample_ids is not None
            else None
        )
        self.target_qwk = float(target_qwk)
        self.max_log_records = max(1, int(max_log_records))
        self.mock_eval_provider = bool(mock_eval_provider)
        self.batch_eval_fn = batch_eval_fn
        self.run_probes_fn = run_probes_fn
        self.probe_descriptions = dict(probe_descriptions or _DEFAULT_PROBE_DESCRIPTIONS)

    def run_one_iteration(self, iter_id: str) -> IterationRecord:
        """Run one complete outer-loop iteration and persist its record."""
        normalized_iter = self._normalize_iter_id(iter_id)
        iteration_index = self._iteration_index(normalized_iter)
        latest = self.experiment_log.latest()

        decision_prompt = self._build_user_prompt(
            phase="decision",
            last_verdict=latest.verdict if latest is not None else "N/A",
            next_hypothesis=latest.next_hypothesis if latest is not None else "N/A",
            probe_results_this_round="N/A",
        )
        decision_resp = self.provider.complete(
            LLMRequest(
                prompt=decision_prompt,
                system=self.system_prompt,
                metadata={
                    "outer_loop_phase": "decision",
                    "iter_id": normalized_iter,
                },
            )
        )
        proposal, selected_probes = self._parse_decision_response(decision_resp.content)
        selected_probes = self._normalize_probe_names(selected_probes)
        if not selected_probes:
            selected_probes = self._infer_default_probes(proposal.change_unit)

        probe_results_payload: dict[str, Any] = {}
        policy_ok, policy_message = self._validate_search_policy(proposal)
        apply_ok = False
        apply_message = ""

        if not policy_ok:
            probe_results_payload["_execution"] = {
                "status": "policy_rejected",
                "message": policy_message,
            }
        else:
            apply_ok, apply_message = self.config_patcher.apply(proposal, normalized_iter)
            probe_results_payload["_execution"] = {
                "status": "patch_applied" if apply_ok else "patch_failed",
                "message": apply_message,
            }

            if apply_ok:
                sample_ids = self._get_training_sample_ids()
                run_results = self.batch_eval_fn(
                    sample_ids=sample_ids,
                    bundle_path=self.bundle_path,
                    tsv_path=self.training_set_path,
                    output_base=self.artifacts_output_base,
                    iter_id=normalized_iter,
                    mock_provider=self.mock_eval_provider,
                )
                probe_results_payload["_batch_eval"] = {
                    "sample_count": len(run_results),
                    "success_count": sum(1 for item in run_results if item.success),
                }

                artifacts_dir = self._iter_artifacts_dir(normalized_iter)
                probes = self.run_probes_fn(
                    selected_probes,
                    artifacts_dir,
                    tsv_path=self.training_set_path,
                )
                serialized_probes = self._serialize_probe_results(probes)
                probe_results_payload.update(serialized_probes)
                self._archive_probe_results(normalized_iter, probe_results_payload)

        prev_qwk = (
            self._extract_qwk_composite(latest.probe_results) if latest is not None else None
        )
        new_qwk = self._extract_qwk_composite(probe_results_payload)
        rollback_triggered = False
        if (
            apply_ok
            and prev_qwk is not None
            and new_qwk is not None
            and self.search_policy.should_rollback(prev_qwk, new_qwk)
        ):
            rollback_triggered = self.config_patcher.rollback(normalized_iter)
            if rollback_triggered:
                reason = (
                    "QWK composite dropped from "
                    f"{prev_qwk:.4f} to {new_qwk:.4f}, rollback triggered."
                )
                self.experiment_log.mark_forbidden(proposal.change_unit, reason)
                probe_results_payload["_rollback"] = {
                    "triggered": True,
                    "reason": reason,
                }
            else:
                probe_results_payload["_rollback"] = {
                    "triggered": False,
                    "reason": "Rollback requested but snapshot was missing.",
                }

        default_review = self._default_review(
            policy_ok=policy_ok,
            apply_ok=apply_ok,
            prev_qwk=prev_qwk,
            new_qwk=new_qwk,
            rollback_triggered=rollback_triggered,
            policy_message=policy_message,
            apply_message=apply_message,
            proposal=proposal,
        )
        review_prompt = self._build_user_prompt(
            phase="review",
            last_verdict=latest.verdict if latest is not None else "N/A",
            next_hypothesis=latest.next_hypothesis if latest is not None else "N/A",
            probe_results_this_round=json.dumps(
                probe_results_payload, indent=2, ensure_ascii=False
            ),
        )
        review_resp = self.provider.complete(
            LLMRequest(
                prompt=review_prompt,
                system=self.system_prompt,
                metadata={
                    "outer_loop_phase": "review",
                    "iter_id": normalized_iter,
                },
            )
        )
        review = self._parse_review_response(
            review_resp.content,
            default=default_review,
        )

        snapshot_dir = self.config_patcher.snapshots_root / normalized_iter
        snapshot_value = str(snapshot_dir) if snapshot_dir.exists() else ""

        record = IterationRecord(
            iteration=iteration_index,
            timestamp=datetime.now().isoformat(timespec="seconds"),
            changed_unit=proposal.change_unit,
            change_description=proposal.rationale,
            target_file=proposal.target_file,
            target_path=proposal.target_path,
            probe_used=selected_probes,
            probe_results=probe_results_payload,
            verdict=review.verdict,
            next_hypothesis=review.next_hypothesis,
            config_snapshot_path=snapshot_value,
        )
        self.experiment_log.append(record)
        self.config_patcher._prune_old_snapshots()
        return record

    def run_loop(
        self,
        max_iterations: int,
        stop_on_target: bool = True,
    ) -> list[IterationRecord]:
        """Run the outer-loop optimization for up to max_iterations."""
        if max_iterations <= 0:
            return []

        if self.experiment_log.count() == 0:
            self.run_cold_start()

        records: list[IterationRecord] = []
        for _ in range(max_iterations):
            next_iter_num = self.experiment_log.next_iteration_id()
            iter_id = f"{next_iter_num:03d}"
            try:
                record = self.run_one_iteration(iter_id=iter_id)
            except KeyboardInterrupt:
                break

            records.append(record)
            if stop_on_target and self._is_target_reached(record.probe_results):
                break
        return records

    def run_cold_start(self) -> Path:
        """Run cold-start diagnostics and generate the first proposal file."""
        sample_ids = self._get_training_sample_ids()
        if not sample_ids:
            raise ValueError("Cold start requires at least one training sample id.")

        self.batch_eval_fn(
            sample_ids=sample_ids,
            bundle_path=self.bundle_path,
            tsv_path=self.training_set_path,
            output_base=self.artifacts_output_base,
            iter_id="cold_start",
            mock_provider=self.mock_eval_provider,
        )

        cold_artifacts = self._iter_artifacts_dir("cold_start")
        probe_names = list(self.probe_descriptions.keys())
        cold_probes = self.run_probes_fn(
            probe_names,
            cold_artifacts,
            tsv_path=self.training_set_path,
        )
        serialized_probes = self._serialize_probe_results(cold_probes)

        self.probes_dir.mkdir(parents=True, exist_ok=True)
        diagnostics_path = self.probes_dir / "cold_start_diagnostics.json"
        diagnostics_payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "iter_id": "cold_start",
            "probe_results": serialized_probes,
        }
        diagnostics_path.write_text(
            json.dumps(diagnostics_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        notes_text = ""
        if self.cold_start_notes_path.exists():
            notes_text = self.cold_start_notes_path.read_text(encoding="utf-8").strip()

        decision_prompt = self._build_user_prompt(
            phase="decision",
            last_verdict="N/A",
            next_hypothesis=(notes_text or "N/A"),
            probe_results_this_round=json.dumps(
                diagnostics_payload, indent=2, ensure_ascii=False
            ),
        )
        decision_resp = self.provider.complete(
            LLMRequest(
                prompt=decision_prompt,
                system=self.system_prompt,
                metadata={
                    "outer_loop_phase": "cold_start",
                    "iter_id": "cold_start",
                },
            )
        )
        proposal, selected_probes = self._parse_decision_response(decision_resp.content)
        selected_probes = self._normalize_probe_names(selected_probes)
        if not selected_probes:
            selected_probes = self._infer_default_probes(proposal.change_unit)

        first_proposal_path = self.experiments_dir / "iter_001_proposal.yaml"
        first_proposal_path.parent.mkdir(parents=True, exist_ok=True)
        first_proposal_payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "change_proposal": self._proposal_to_dict(proposal),
            "selected_probes": selected_probes,
            "diagnostics_path": str(diagnostics_path),
            "notes_included": bool(notes_text),
        }
        first_proposal_path.write_text(
            yaml.safe_dump(first_proposal_payload, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return first_proposal_path

    def _build_user_prompt(
        self,
        *,
        phase: str,
        last_verdict: str,
        next_hypothesis: str,
        probe_results_this_round: str,
    ) -> str:
        return self.user_prompt_template.render(
            phase=phase,
            experiment_log_summary=self._summarize_experiment_log(),
            current_config_snapshot=self._summarize_current_config(),
            available_probes=self._format_available_probes(),
            last_verdict=last_verdict,
            next_hypothesis=next_hypothesis,
            probe_results_this_round=probe_results_this_round,
        )

    def _summarize_experiment_log(self) -> str:
        records = self.experiment_log.last_n(self.max_log_records)
        if not records:
            return "- no previous iterations."

        lines = []
        for record in records:
            lines.append(
                "- iter {idx}: unit={unit}; verdict={verdict}; probes={probes}".format(
                    idx=record.iteration,
                    unit=record.changed_unit,
                    verdict=record.verdict,
                    probes=", ".join(record.probe_used) if record.probe_used else "none",
                )
            )
        if self.experiment_log.forbidden_units:
            blocked = ", ".join(sorted(self.experiment_log.forbidden_units.keys()))
            lines.append(f"- forbidden_units: {blocked}")
        return "\n".join(lines)

    def _summarize_current_config(self) -> str:
        configs_root = self.config_patcher.configs_root
        focus_files = [
            ("prompts/scoring_context.yaml", "scoring_context"),
            ("prompts/scoring.yaml", "scoring"),
            ("prompts/evidence_extraction.yaml", "evidence_extraction"),
            ("policies/adjudication/asap_set8_default.yaml", "adjudication_policy"),
            ("bundles/asap_set8_baseline.bundle.yaml", "bundle"),
        ]

        lines: list[str] = []
        for rel_path, label in focus_files:
            path = configs_root / rel_path
            if not path.exists():
                continue
            loaded = self._safe_load_yaml(path)
            if not isinstance(loaded, dict):
                lines.append(f"- {label}: present but not a YAML mapping")
                continue
            if label == "bundle":
                provider_cfg = (
                    loaded.get("artifact_bundle", {}).get("provider_config", {}).get("default", {})
                )
                op = loaded.get("artifact_bundle", {}).get("operational_params", {})
                lines.append(
                    "- bundle: temperature={temp}, max_tokens={max_tokens}, "
                    "thinking_budget={thinking_budget}, max_retries={max_retries}".format(
                        temp=provider_cfg.get("params", {}).get("temperature"),
                        max_tokens=provider_cfg.get("params", {}).get("max_tokens"),
                        thinking_budget=provider_cfg.get("params", {}).get("thinking_budget"),
                        max_retries=op.get("max_retries"),
                    )
                )
                continue

            prompt_text = loaded.get("prompt_template")
            if isinstance(prompt_text, str):
                lines.append(f"- {label}: prompt_template chars={len(prompt_text)}")
                continue

            if label == "scoring_context":
                calibration = (
                    loaded.get("scoring_context", {}).get("calibration_notes")
                    if isinstance(loaded.get("scoring_context"), dict)
                    else None
                )
                cal_len = len(calibration) if isinstance(calibration, str) else 0
                lines.append(f"- scoring_context: calibration_notes chars={cal_len}")
                continue

            lines.append(f"- {label}: keys={', '.join(sorted(loaded.keys())[:6])}")

        if not lines:
            return "- no readable config summary."
        return "\n".join(lines)

    def _format_available_probes(self) -> str:
        lines = []
        for probe_name, desc in self.probe_descriptions.items():
            lines.append(f"- {probe_name}: {desc}")
        return "\n".join(lines)

    def _parse_decision_response(self, response_text: str) -> tuple[ChangeProposal, list[str]]:
        try:
            payload = self._parse_yaml_payload(response_text)
        except ValueError as exc:
            return self._fallback_decision(f"decision parse failed: {exc}")

        raw_proposal = payload.get("change_proposal")
        if isinstance(raw_proposal, dict):
            proposal_payload = raw_proposal
        else:
            proposal_payload = payload

        proposal = self._proposal_from_payload(proposal_payload)
        if proposal is None:
            return self._fallback_decision("missing required change_proposal fields")

        selected_probes_raw = payload.get("selected_probes")
        selected_probes = (
            [str(item).strip() for item in selected_probes_raw if str(item).strip()]
            if isinstance(selected_probes_raw, list)
            else []
        )
        return proposal, selected_probes

    def _parse_review_response(
        self,
        response_text: str,
        *,
        default: ReviewResult,
    ) -> ReviewResult:
        try:
            payload = self._parse_yaml_payload(response_text)
        except ValueError:
            return default

        verdict = str(payload.get("verdict") or "").strip() or default.verdict
        next_hypothesis = (
            str(payload.get("next_hypothesis") or "").strip() or default.next_hypothesis
        )
        return ReviewResult(verdict=verdict, next_hypothesis=next_hypothesis)

    def _default_review(
        self,
        *,
        policy_ok: bool,
        apply_ok: bool,
        prev_qwk: float | None,
        new_qwk: float | None,
        rollback_triggered: bool,
        policy_message: str,
        apply_message: str,
        proposal: ChangeProposal,
    ) -> ReviewResult:
        if not policy_ok:
            return ReviewResult(
                verdict=f"failed: {policy_message}",
                next_hypothesis="Switch to another allowed unit that satisfies policy constraints.",
            )
        if not apply_ok:
            return ReviewResult(
                verdict=f"failed: {apply_message}",
                next_hypothesis="Use a simpler field_patch on a nearby path and rerun probes.",
            )
        if rollback_triggered:
            return ReviewResult(
                verdict="regression (rollback triggered)",
                next_hypothesis=(
                    f"Do not revisit {proposal.change_unit}; "
                    "transfer to an adjacent non-forbidden unit."
                ),
            )
        if prev_qwk is not None and new_qwk is not None:
            if new_qwk > prev_qwk:
                return ReviewResult(
                    verdict="effective",
                    next_hypothesis="Continue with a small adjacent change in the same layer.",
                )
            if new_qwk < prev_qwk:
                return ReviewResult(
                    verdict="regression",
                    next_hypothesis="Revert hypothesis and try a different unit in the same layer.",
                )
        return ReviewResult(
            verdict="no-improvement",
            next_hypothesis="Transfer to another unit with higher expected probe sensitivity.",
        )

    def _validate_search_policy(self, proposal: ChangeProposal) -> tuple[bool, str]:
        unit = proposal.change_unit
        if self.search_policy.is_forbidden_unit(self.experiment_log, unit):
            return False, f"change unit is forbidden: {unit}"
        if self.search_policy.should_force_transfer(self.experiment_log, unit):
            return False, f"force-transfer triggered for unit: {unit}"

        latest = self.experiment_log.latest()
        latest_probes = latest.probe_results if latest is not None else {}
        if self.search_policy.should_escalate_layer(self.experiment_log, latest_probes):
            layer = self.search_policy.get_priority_layer(unit)
            if layer != 2:
                return (
                    False,
                    "upstream probe signals are not converged; "
                    f"require layer-2 unit, got layer {layer}",
                )

        return True, "ok"

    def _fallback_decision(self, reason: str) -> tuple[ChangeProposal, list[str]]:
        proposal = ChangeProposal(
            change_unit="scoring.calibration_notes.ideas_content",
            change_type="field_patch",
            target_file="configs/prompts/scoring_context.yaml",
            target_path="scoring_context.calibration_notes",
            new_value=(
                "Fallback calibration note: prioritize controlling idea clarity, "
                "relevant supporting evidence, and penalties only when weakness is consistent."
            ),
            rationale=f"Fallback proposal generated because {reason}.",
        )
        return proposal, ["rater_consistency_probe", "qwk_probe"]

    def _proposal_from_payload(self, payload: dict[str, Any]) -> ChangeProposal | None:
        if not isinstance(payload, dict):
            return None

        change_unit = str(payload.get("change_unit") or "").strip()
        change_type = str(payload.get("change_type") or "").strip()
        target_file = str(payload.get("target_file") or "").strip()
        target_path = str(payload.get("target_path") or "").strip()
        rationale = str(payload.get("rationale") or "").strip()
        if not all((change_unit, change_type, target_file, rationale)):
            return None
        if change_type not in {"field_patch", "file_overwrite"}:
            return None
        if change_type == "field_patch" and not target_path:
            return None
        return ChangeProposal(
            change_unit=change_unit,
            change_type=change_type,
            target_file=target_file,
            target_path=target_path,
            new_value=payload.get("new_value"),
            rationale=rationale,
        )

    def _parse_yaml_payload(self, raw_text: str) -> dict[str, Any]:
        text = raw_text.strip()
        if not text:
            raise ValueError("empty response")

        block_match = re.search(
            r"```(?:yaml|yml)?\s*(.*?)```",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        yaml_text = block_match.group(1).strip() if block_match else text
        loaded = yaml.safe_load(yaml_text)
        if not isinstance(loaded, dict):
            raise ValueError("YAML payload must decode to dict")
        return loaded

    def _get_training_sample_ids(self) -> list[str]:
        if self._training_sample_ids is not None:
            return list(self._training_sample_ids)

        if not self.training_set_path.exists():
            raise FileNotFoundError(f"Training TSV not found: {self.training_set_path}")

        ids: list[str] = []
        with self.training_set_path.open(encoding="latin-1") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            for row in reader:
                essay_id = (row.get("essay_id") or "").strip()
                if essay_id:
                    ids.append(essay_id)
        if not ids:
            raise ValueError(f"No essay_id found in TSV: {self.training_set_path}")

        self._training_sample_ids = ids
        return list(ids)

    def _normalize_probe_names(self, probe_names: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        known = set(self.probe_descriptions.keys())
        for name in probe_names:
            cleaned = str(name).strip()
            if not cleaned or cleaned not in known or cleaned in seen:
                continue
            normalized.append(cleaned)
            seen.add(cleaned)
        return normalized

    def _infer_default_probes(self, change_unit: str) -> list[str]:
        lowered = str(change_unit).lower()
        if "scoring" in lowered:
            return ["rater_consistency_probe", "qwk_probe"]
        if "coverage" in lowered or "chunk" in lowered:
            return ["coverage_probe", "qwk_probe"]
        if "extraction" in lowered or "evidence" in lowered:
            return ["evidence_quality_probe", "qwk_probe"]
        if "adjudication" in lowered or "conflict" in lowered:
            return ["conflict_pattern_probe", "resolution_cost_probe", "qwk_probe"]
        if "feedback" in lowered:
            return ["feedback_grounding_probe", "qwk_probe"]
        return ["qwk_probe", "cost_probe"]

    def _serialize_probe_results(self, probes: dict[str, ProbeResult]) -> dict[str, Any]:
        serialized: dict[str, Any] = {}
        for name, result in probes.items():
            serialized[name] = {
                "essay_count": result.essay_count,
                "metrics": dict(result.metrics),
            }
        return serialized

    def _archive_probe_results(self, iter_id: str, payload: dict[str, Any]) -> Path:
        self.probes_dir.mkdir(parents=True, exist_ok=True)
        path = self.probes_dir / f"{iter_id}_probes.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def _extract_qwk_composite(self, probe_results: dict[str, Any]) -> float | None:
        qwk_payload = probe_results.get("qwk_probe")
        if isinstance(qwk_payload, dict):
            metrics = qwk_payload.get("metrics")
            if isinstance(metrics, dict):
                return self._as_float(metrics.get("qwk_composite"))
            return self._as_float(qwk_payload.get("qwk_composite"))
        return self._as_float(probe_results.get("qwk_composite"))

    def _is_target_reached(self, probe_results: dict[str, Any]) -> bool:
        qwk_payload = probe_results.get("qwk_probe")
        if not isinstance(qwk_payload, dict):
            return False
        metrics = qwk_payload.get("metrics")
        if not isinstance(metrics, dict):
            return False

        qwk_composite = self._as_float(metrics.get("qwk_composite"))
        if qwk_composite is None or qwk_composite < self.target_qwk:
            return False

        for dim_id in _DIM_ORDER:
            qwk_dim = self._as_float(metrics.get(f"qwk_{dim_id}"))
            if qwk_dim is None or qwk_dim < self.target_qwk:
                return False
        return True

    def _proposal_to_dict(self, proposal: ChangeProposal) -> dict[str, Any]:
        return {
            "change_unit": proposal.change_unit,
            "change_type": proposal.change_type,
            "target_file": proposal.target_file,
            "target_path": proposal.target_path,
            "new_value": proposal.new_value,
            "rationale": proposal.rationale,
        }

    def _safe_load_yaml(self, path: Path) -> Any:
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _normalize_iter_id(self, iter_id: str) -> str:
        text = str(iter_id).strip()
        if not text:
            raise ValueError("iter_id must be non-empty")
        if text.startswith("iter_"):
            return text
        return f"iter_{text}"

    def _iteration_index(self, normalized_iter: str) -> int:
        match = re.search(r"(\d+)$", normalized_iter)
        if match is None:
            return self.experiment_log.next_iteration_id()
        return int(match.group(1))

    def _iter_artifacts_dir(self, iter_id: str) -> Path:
        normalized = self._normalize_iter_id(iter_id)
        return self.artifacts_output_base / normalized

    def _as_float(self, value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return float(text)
            except ValueError:
                return None
        return None


__all__ = [
    "OuterLoopAgent",
    "ReviewResult",
    "RuleBasedOuterLoopProvider",
]
