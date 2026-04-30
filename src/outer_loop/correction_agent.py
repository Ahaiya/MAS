"""Correction Agent — interprets human corrections and updates task_context.yaml."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from jinja2 import Template

from src.outer_loop.config_patcher import ChangeProposal, ConfigPatcher
from src.outer_loop.correction_models import PendingCorrections
from src.providers.base import BaseProvider, LLMRequest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PROMPTS_DIR = _PROJECT_ROOT / "src" / "outer_loop" / "prompts"
_DEFAULT_TARGET_FILE = "configs/tasks/task_context.yaml"


class CorrectionAgent:
    """Interprets human correction events and applies targeted updates to task_context.yaml."""

    def __init__(
        self,
        *,
        provider: BaseProvider,
        config_patcher: ConfigPatcher,
        prompts_dir: Path | str | None = None,
        target_file: Path | str = _DEFAULT_TARGET_FILE,
    ) -> None:
        self.provider = provider
        self.config_patcher = config_patcher
        self.target_file = str(target_file)

        prompts_root = Path(prompts_dir) if prompts_dir else _DEFAULT_PROMPTS_DIR
        system_path = prompts_root / "correction_system.md"
        user_template_path = prompts_root / "correction_user_template.md"

        if not system_path.exists():
            raise FileNotFoundError(f"Correction system prompt not found: {system_path}")
        if not user_template_path.exists():
            raise FileNotFoundError(f"Correction user template not found: {user_template_path}")

        self.system_prompt = system_path.read_text(encoding="utf-8")
        self.user_template = Template(user_template_path.read_text(encoding="utf-8"))

    def process(self, pending: PendingCorrections, iter_id: str = "correction") -> bool:
        """Process pending corrections, update task_context.yaml, and archive corrections.

        Returns True if corrections were applied successfully, False otherwise.
        """
        if pending.is_empty():
            return False

        target_path = self.config_patcher.resolve_target_path(self.target_file)
        current_content = (
            target_path.read_text(encoding="utf-8")
            if target_path.exists()
            else ""
        )

        user_prompt = self.user_template.render(
            current_task_context=current_content,
            total_events=len(pending.events),
            correction_events=[
                {
                    "sample_id": e.sample_id,
                    "timestamp": e.timestamp,
                    "score_corrections": [
                        {
                            "dimension_code": c.dimension_code,
                            "original_score": c.original_score,
                            "corrected_score": c.corrected_score,
                        }
                        for c in e.score_corrections
                    ],
                    "feedback_corrections": [
                        {
                            "dimension_code": c.dimension_code,
                            "original_feedback": c.original_feedback,
                            "corrected_feedback": c.corrected_feedback,
                        }
                        for c in e.feedback_corrections
                    ],
                    "evidence_additions": [
                        {
                            "dimension_code": c.dimension_code,
                            "added_quotes": c.added_quotes,
                        }
                        for c in e.evidence_additions
                    ],
                }
                for e in pending.events
            ],
        )

        response = self.provider.complete(
            LLMRequest(
                prompt=user_prompt,
                system=self.system_prompt,
                metadata={"stage": "correction_agent", "iter_id": iter_id},
            )
        )

        new_yaml = self._extract_yaml(response.content)
        if not new_yaml:
            return False

        proposal = ChangeProposal(
            change_unit="scoring.task_context",
            change_type="file_overwrite",
            target_file=self.target_file,
            target_path="",
            new_value=new_yaml,
            rationale=f"根据 {len(pending.events)} 条教师批改意见自动更新",
        )

        ok, _ = self.config_patcher.apply(proposal, iter_id)
        if ok:
            pending.archive()

        return ok

    @staticmethod
    def _extract_yaml(text: str) -> str:
        """Extract YAML content from a fenced code block or raw text."""
        match = re.search(
            r"```(?:yaml|yml)?\s*(.*?)```",
            text.strip(),
            flags=re.IGNORECASE | re.DOTALL,
        )
        raw = match.group(1).strip() if match else text.strip()
        # Validate it parses as a dict
        try:
            loaded = yaml.safe_load(raw)
            if not isinstance(loaded, dict):
                return ""
        except yaml.YAMLError:
            return ""
        return raw


def check_and_apply_corrections(
    *,
    provider: BaseProvider,
    configs_root: Path,
    snapshots_root: Path,
    pending_path: Path | None = None,
    prompts_dir: Path | None = None,
    target_file: Path | str = _DEFAULT_TARGET_FILE,
    iter_id: str = "correction",
) -> bool:
    """Check for pending human corrections and apply them before the inner loop.

    Returns True if corrections were found and applied, False if nothing to do.
    """
    pending = PendingCorrections.load(pending_path)
    if pending.is_empty():
        return False

    patcher = ConfigPatcher(configs_root=configs_root, snapshots_root=snapshots_root)
    agent = CorrectionAgent(
        provider=provider,
        config_patcher=patcher,
        prompts_dir=prompts_dir,
        target_file=target_file,
    )
    return agent.process(pending, iter_id=iter_id)


__all__ = ["CorrectionAgent", "check_and_apply_corrections"]
