"""Experiment log storage for outer-loop iterations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

_NO_IMPROVEMENT_KEYWORDS = (
    "no-improvement",
    "no improvement",
    "no_improvement",
    "无改善",
    "无改进",
    "无提升",
)


@dataclass
class IterationRecord:
    iteration: int
    timestamp: str
    changed_unit: str
    change_description: str
    target_file: str
    target_path: str
    probe_used: list[str]
    probe_results: dict[str, Any]
    verdict: str
    next_hypothesis: str
    config_snapshot_path: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> IterationRecord:
        return cls(
            iteration=int(payload["iteration"]),
            timestamp=str(payload["timestamp"]),
            changed_unit=str(payload["changed_unit"]),
            change_description=str(payload["change_description"]),
            target_file=str(payload["target_file"]),
            target_path=str(payload["target_path"]),
            probe_used=[str(item) for item in list(payload.get("probe_used") or [])],
            probe_results=dict(payload.get("probe_results") or {}),
            verdict=str(payload["verdict"]),
            next_hypothesis=str(payload["next_hypothesis"]),
            config_snapshot_path=str(payload["config_snapshot_path"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExperimentLog:
    """Persistent experiment log backed by YAML."""

    def __init__(
        self,
        path: Path,
        records: list[IterationRecord] | None = None,
        forbidden_units: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.path = Path(path)
        self._records = list(records or [])
        self._forbidden_units: dict[str, dict[str, Any]] = dict(forbidden_units or {})

    @classmethod
    def load(cls, path: Path) -> ExperimentLog:
        path = Path(path)
        if not path.exists():
            return cls(path=path, records=[], forbidden_units={})

        raw_text = path.read_text(encoding="utf-8")
        payload = yaml.safe_load(raw_text) if raw_text.strip() else []
        records_data: list[dict[str, Any]]
        forbidden_units: dict[str, dict[str, Any]]

        if payload is None:
            records_data = []
            forbidden_units = {}
        elif isinstance(payload, list):
            records_data = [item for item in payload if isinstance(item, dict)]
            forbidden_units = {}
        elif isinstance(payload, dict):
            records_data = [
                item for item in list(payload.get("records") or []) if isinstance(item, dict)
            ]
            raw_forbidden = payload.get("forbidden_units") or {}
            forbidden_units = (
                {
                    str(unit): dict(meta)
                    for unit, meta in raw_forbidden.items()
                    if isinstance(unit, str) and isinstance(meta, dict)
                }
                if isinstance(raw_forbidden, dict)
                else {}
            )
        else:
            raise ValueError(f"Unsupported experiment log format in {path}")

        records = [IterationRecord.from_dict(item) for item in records_data]
        return cls(path=path, records=records, forbidden_units=forbidden_units)

    def append(self, record: IterationRecord) -> None:
        self._records.append(record)
        self._write()

    def latest(self) -> IterationRecord | None:
        if not self._records:
            return None
        return self._records[-1]

    def last_n(self, n: int) -> list[IterationRecord]:
        if n <= 0:
            return []
        return list(self._records[-n:])

    def count(self) -> int:
        return len(self._records)

    def count_consecutive_no_improvement(self, unit: str) -> int:
        unit = str(unit).strip()
        if not unit:
            return 0

        count = 0
        for record in reversed(self._records):
            if record.changed_unit != unit:
                break
            if not _is_no_improvement(record):
                break
            count += 1
        return count

    def is_forbidden_unit(self, unit: str) -> bool:
        return str(unit).strip() in self._forbidden_units

    def mark_forbidden(self, unit: str, reason: str) -> None:
        cleaned_unit = str(unit).strip()
        if not cleaned_unit:
            return
        self._forbidden_units[cleaned_unit] = {
            "reason": str(reason),
            "marked_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._write()

    def consecutive_no_improvement_global(self) -> int:
        count = 0
        for record in reversed(self._records):
            if not _is_no_improvement(record):
                break
            count += 1
        return count

    def next_iteration_id(self) -> int:
        if not self._records:
            return 1
        return max(record.iteration for record in self._records) + 1

    @property
    def records(self) -> list[IterationRecord]:
        return list(self._records)

    @property
    def forbidden_units(self) -> dict[str, dict[str, Any]]:
        return dict(self._forbidden_units)

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        records_payload = [record.to_dict() for record in self._records]
        if self._forbidden_units:
            payload: dict[str, Any] | list[dict[str, Any]] = {
                "records": records_payload,
                "forbidden_units": self._forbidden_units,
            }
        else:
            payload = records_payload
        serialized = yaml.safe_dump(
            payload,
            sort_keys=False,
            allow_unicode=True,
        )
        self.path.write_text(serialized, encoding="utf-8")


def _is_no_improvement(record: IterationRecord) -> bool:
    verdict = record.verdict.strip().lower()
    return any(keyword in verdict for keyword in _NO_IMPROVEMENT_KEYWORDS)


__all__ = ["ExperimentLog", "IterationRecord"]
