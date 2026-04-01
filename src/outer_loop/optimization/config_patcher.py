"""Safe config mutation layer for outer-loop optimization."""

from __future__ import annotations

import fnmatch
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml

DEFAULT_ALLOWED_FILE_PATTERNS: tuple[str, ...] = (
    "configs/prompts/scoring_context.yaml",
    "configs/prompts/tasks/*_scoring_context.yaml",
    "configs/prompts/scoring.yaml",
    "configs/prompts/scoring_overrides/*.yaml",
    "configs/prompts/evidence_extraction.yaml",
    "configs/prompts/evidence_extraction_overrides/*.yaml",
    "configs/prompts/explanation.yaml",
    "configs/policies/adjudication/engineering_eval_adjudication.yaml",
    "configs/policies/chunking/engineering_eval_chunking.yaml",
    "configs/policies/aggregation/engineering_eval_aggregation.yaml",
    "configs/bundles/engineering_eval_baseline.bundle.yaml",
)


@dataclass
class ChangeProposal:
    change_unit: str
    change_type: Literal["field_patch", "file_overwrite"]
    target_file: str
    target_path: str
    new_value: Any
    rationale: str


class ConfigPatcher:
    """Validate and apply controlled config changes with snapshot/rollback."""

    def __init__(
        self,
        *,
        configs_root: Path | str = "configs",
        snapshots_root: Path | str = "experiments/snapshots",
        allowed_file_patterns: tuple[str, ...] = DEFAULT_ALLOWED_FILE_PATTERNS,
        protected_bundle_fields: set[str] | None = None,
    ) -> None:
        self.configs_root = Path(configs_root)
        self.snapshots_root = Path(snapshots_root)
        self.allowed_file_patterns = tuple(allowed_file_patterns)
        self.protected_bundle_fields = set(protected_bundle_fields or {"model", "model_id"})

    def validate(self, proposal: ChangeProposal) -> tuple[bool, str]:
        if proposal.change_type not in {"field_patch", "file_overwrite"}:
            return False, f"unsupported change_type: {proposal.change_type}"

        normalized_target = self._normalize_target_file(proposal.target_file)
        if not self._is_allowed_target(normalized_target):
            return False, f"target_file is not in whitelist: {normalized_target}"

        target_path = self._resolve_target_path(normalized_target)
        if not target_path.exists():
            return False, f"target file not found: {target_path}"

        if normalized_target.endswith(".bundle.yaml") and proposal.change_type == "file_overwrite":
            return False, "bundle files do not allow file_overwrite; use field_patch"

        if proposal.change_type == "field_patch" and not proposal.target_path.strip():
            return False, "target_path is required for field_patch"

        if self._touches_protected_bundle_field(
            normalized_target=normalized_target,
            target_path=proposal.target_path,
        ):
            return False, f"target_path includes protected bundle field: {proposal.target_path}"

        return True, "ok"

    def apply(self, proposal: ChangeProposal, iter_id: str) -> tuple[bool, str]:
        valid, message = self.validate(proposal)
        if not valid:
            return False, message

        normalized_target = self._normalize_target_file(proposal.target_file)
        target_path = self._resolve_target_path(normalized_target)
        normalized_iter = self._normalize_iter_id(iter_id)
        snapshot_path = self._snapshot_configs(normalized_iter)

        try:
            self._apply_change(
                proposal=proposal,
                target_path=target_path,
            )

            # Post-write parse guard: ensures the file remains valid YAML.
            yaml.safe_load(target_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.rollback(normalized_iter)
            return False, f"apply failed and rolled back: {exc}"

        self._prune_old_snapshots()
        return True, f"applied with snapshot {snapshot_path.parent}"

    def rollback(self, iter_id: str) -> bool:
        normalized_iter = self._normalize_iter_id(iter_id)
        snapshot_configs = self.snapshots_root / normalized_iter / "configs"
        if not snapshot_configs.exists():
            return False

        if self.configs_root.exists():
            shutil.rmtree(self.configs_root)
        shutil.copytree(snapshot_configs, self.configs_root)
        return True

    def _prune_old_snapshots(self, keep: int = 20) -> None:
        if keep <= 0 or not self.snapshots_root.exists():
            return
        snapshot_dirs = [
            p
            for p in self.snapshots_root.iterdir()
            if p.is_dir() and p.name.startswith("iter_")
        ]
        if len(snapshot_dirs) <= keep:
            return

        snapshot_dirs.sort(key=self._snapshot_sort_key)
        for old_path in snapshot_dirs[:-keep]:
            shutil.rmtree(old_path)

    def _snapshot_sort_key(self, path: Path) -> tuple[int, str]:
        match = re.fullmatch(r"iter_(\d+)", path.name)
        if match is None:
            return (-1, path.name)
        return (int(match.group(1)), path.name)

    def _apply_change(self, proposal: ChangeProposal, target_path: Path) -> None:
        if proposal.change_type == "file_overwrite":
            self._apply_file_overwrite(proposal.new_value, target_path)
            return

        loaded_doc = yaml.safe_load(target_path.read_text(encoding="utf-8"))
        if loaded_doc is None:
            loaded_doc = {}
        if not isinstance(loaded_doc, dict):
            raise ValueError("field_patch requires YAML root object to be a mapping")

        patched_doc = self._patch_field(
            document=loaded_doc,
            target_path=proposal.target_path,
            new_value=proposal.new_value,
        )
        serialized = yaml.safe_dump(
            patched_doc,
            sort_keys=False,
            allow_unicode=True,
        )
        target_path.write_text(serialized, encoding="utf-8")

    def _apply_file_overwrite(self, new_value: Any, target_path: Path) -> None:
        if isinstance(new_value, str):
            target_path.write_text(new_value, encoding="utf-8")
            return
        serialized = yaml.safe_dump(
            new_value,
            sort_keys=False,
            allow_unicode=True,
        )
        target_path.write_text(serialized, encoding="utf-8")

    def _patch_field(
        self,
        *,
        document: dict[str, Any],
        target_path: str,
        new_value: Any,
    ) -> dict[str, Any]:
        parts = [part for part in target_path.split(".") if part]
        if not parts:
            raise ValueError("target_path must be a non-empty dot path")

        cursor: dict[str, Any] = document
        for part in parts[:-1]:
            existing = cursor.get(part)
            if existing is None:
                nested: dict[str, Any] = {}
                cursor[part] = nested
                cursor = nested
                continue
            if not isinstance(existing, dict):
                raise ValueError(f"cannot descend into non-mapping path segment: {part}")
            cursor = existing

        cursor[parts[-1]] = new_value
        return document

    def _normalize_target_file(self, target_file: str) -> str:
        value = str(target_file).strip()
        if not value:
            raise ValueError("target_file must be non-empty")

        posix = str(PurePosixPath(value))
        while posix.startswith("./"):
            posix = posix[2:]
        if not posix.startswith("configs/"):
            posix = f"configs/{posix}"
        return posix

    def _is_allowed_target(self, normalized_target: str) -> bool:
        return any(
            fnmatch.fnmatch(normalized_target, pattern)
            for pattern in self.allowed_file_patterns
        )

    def _resolve_target_path(self, normalized_target: str) -> Path:
        configs_root = self.configs_root.resolve()
        relative_path = normalized_target.removeprefix("configs/")
        candidate = (configs_root / relative_path).resolve()
        candidate.relative_to(configs_root)
        return candidate

    def _touches_protected_bundle_field(
        self,
        *,
        normalized_target: str,
        target_path: str,
    ) -> bool:
        if "/bundles/" not in normalized_target:
            return False
        parts = [
            part.strip()
            for part in re.split(r"[.\[\]]+", target_path)
            if part.strip()
        ]
        return any(part in self.protected_bundle_fields for part in parts)

    def _normalize_iter_id(self, iter_id: str) -> str:
        cleaned = str(iter_id).strip()
        if not cleaned:
            raise ValueError("iter_id must be non-empty")
        if cleaned.startswith("iter_"):
            return cleaned
        return f"iter_{cleaned}"

    def _snapshot_configs(self, normalized_iter: str) -> Path:
        snapshot_root = self.snapshots_root / normalized_iter
        snapshot_configs = snapshot_root / "configs"
        if snapshot_root.exists():
            shutil.rmtree(snapshot_root)
        snapshot_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.configs_root, snapshot_configs)
        return snapshot_configs


__all__ = [
    "ChangeProposal",
    "ConfigPatcher",
    "DEFAULT_ALLOWED_FILE_PATTERNS",
]
