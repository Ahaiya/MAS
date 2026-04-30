from __future__ import annotations

from pathlib import Path

from src.outer_loop.config_patcher import ChangeProposal, ConfigPatcher
from src.outer_loop.correction_agent import CorrectionAgent
from src.outer_loop.correction_models import (
    PendingCorrections,
    CorrectionEvent,
    ScoreCorrection,
)
from src.providers.base import BaseProvider, LLMRequest, LLMResponse, ProviderCapability, TokenUsage


class StaticProvider(BaseProvider):
    def __init__(self, content: str) -> None:
        self.content = content

    @property
    def name(self) -> str:
        return "static"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.TEXT_COMPLETION})

    def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            content=self.content,
            structured_data=None,
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            provider_name=self.name,
            model_id="static-model",
        )


def test_config_patcher_allows_active_task_context_path(tmp_path: Path) -> None:
    configs_root = tmp_path / "configs"
    target = configs_root / "tasks" / "physics_experiment" / "task_context.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("task_name: old\n", encoding="utf-8")

    patcher = ConfigPatcher(
        configs_root=configs_root,
        snapshots_root=tmp_path / "snapshots",
    )
    proposal = ChangeProposal(
        change_unit="scoring.task_context",
        change_type="file_overwrite",
        target_file="configs/tasks/physics_experiment/task_context.yaml",
        target_path="",
        new_value="task_name: updated\n",
        rationale="test",
    )

    ok, message = patcher.apply(proposal, "correction")

    assert ok, message
    assert target.read_text(encoding="utf-8") == "task_name: updated\n"


def test_config_patcher_rejects_prompt_files(tmp_path: Path) -> None:
    configs_root = tmp_path / "configs"
    target = configs_root / "prompts" / "scoring.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("template: old\n", encoding="utf-8")

    patcher = ConfigPatcher(
        configs_root=configs_root,
        snapshots_root=tmp_path / "snapshots",
    )
    proposal = ChangeProposal(
        change_unit="prompt.scoring",
        change_type="file_overwrite",
        target_file="configs/prompts/scoring.yaml",
        target_path="",
        new_value="template: new\n",
        rationale="test",
    )

    ok, message = patcher.apply(proposal, "correction")

    assert not ok
    assert "not in whitelist" in message
    assert target.read_text(encoding="utf-8") == "template: old\n"


def test_correction_agent_updates_configured_task_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configs_root = tmp_path / "configs"
    active_target = configs_root / "tasks" / "physics_experiment" / "task_context.yaml"
    active_target.parent.mkdir(parents=True)
    active_target.write_text("task_name: old\n", encoding="utf-8")

    legacy_target = configs_root / "tasks" / "task_context.yaml"
    legacy_target.parent.mkdir(parents=True, exist_ok=True)
    legacy_target.write_text("task_name: legacy\n", encoding="utf-8")

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "correction_system.md").write_text("system", encoding="utf-8")
    (prompts_dir / "correction_user_template.md").write_text(
        "{{ current_task_context }}",
        encoding="utf-8",
    )

    pending_path = tmp_path / "pending_corrections.json"
    pending_path.write_text("{}", encoding="utf-8")
    pending = PendingCorrections(
        events=[
            CorrectionEvent(
                sample_id="sample-1",
                timestamp="2026-04-29T00:00:00",
                score_corrections=[
                    ScoreCorrection(
                        dimension_code="A1",
                        original_score=2,
                        corrected_score=3,
                    )
                ],
            )
        ],
        path=pending_path,
    )
    monkeypatch.setattr(PendingCorrections, "ARCHIVE_DIR", tmp_path / "processed")

    agent = CorrectionAgent(
        provider=StaticProvider("```yaml\ntask_name: active-updated\n```"),
        config_patcher=ConfigPatcher(
            configs_root=configs_root,
            snapshots_root=tmp_path / "snapshots",
        ),
        prompts_dir=prompts_dir,
        target_file="configs/tasks/physics_experiment/task_context.yaml",
    )

    assert agent.process(pending)
    assert active_target.read_text(encoding="utf-8") == "task_name: active-updated"
    assert legacy_target.read_text(encoding="utf-8") == "task_name: legacy\n"
