"""Outer-loop search policy and soft guardrail rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.outer_loop.experiments.experiment_log import ExperimentLog

PRIORITY_LAYERS: dict[str, int] = {
    "scoring": 1,
    "coverage": 2,
    "extraction": 2,
    "adjudication": 3,
    "feedback": 4,
}


@dataclass(frozen=True)
class SearchPolicyThresholds:
    qwk_drop_for_rollback: float = 0.03
    force_transfer_after: int = 2
    exploration_after_no_improvement: int = 5
    coverage_recall_min: float = 0.8
    evidence_quality_min: float = 0.8
    low_confidence_ratio_max: float = 0.35


class SearchPolicy:
    """Encapsulates phase-level optimization rules for outer-loop agent decisions."""

    def __init__(
        self,
        *,
        priority_layers: dict[str, int] | None = None,
        thresholds: SearchPolicyThresholds | None = None,
    ) -> None:
        self.priority_layers = dict(priority_layers or PRIORITY_LAYERS)
        self.thresholds = thresholds or SearchPolicyThresholds()

    def should_force_transfer(self, log: ExperimentLog, unit: str) -> bool:
        return (
            log.count_consecutive_no_improvement(unit)
            >= self.thresholds.force_transfer_after
        )

    def should_rollback(self, prev_qwk: float, new_qwk: float) -> bool:
        delta = float(prev_qwk) - float(new_qwk)
        return delta > (self.thresholds.qwk_drop_for_rollback + 1e-9)

    def is_exploration_mode(self, log: ExperimentLog) -> bool:
        return (
            log.consecutive_no_improvement_global()
            >= self.thresholds.exploration_after_no_improvement
        )

    def is_forbidden_unit(self, log: ExperimentLog, unit: str) -> bool:
        return log.is_forbidden_unit(unit)

    def get_priority_layer(self, unit: str) -> int:
        cleaned = str(unit).strip().lower()
        if not cleaned:
            return max(self.priority_layers.values(), default=4) + 1

        head = cleaned.split(".", 1)[0]
        if head in self.priority_layers:
            return self.priority_layers[head]

        for key, layer in self.priority_layers.items():
            if key in cleaned:
                return layer
        return max(self.priority_layers.values(), default=4) + 1

    def should_escalate_layer(self, log: ExperimentLog, probes: dict[str, Any]) -> bool:
        if self.is_exploration_mode(log):
            return False

        coverage_metrics = self._extract_probe_metrics(probes, "coverage_probe")
        recall_rate = self._as_float(coverage_metrics.get("coverage_recall_rate"))
        if (
            recall_rate is not None
            and recall_rate < self.thresholds.coverage_recall_min
        ):
            return True

        evidence_metrics = self._extract_probe_metrics(probes, "evidence_quality_probe")
        quote_alignment = self._as_float(evidence_metrics.get("quote_alignment_rate"))
        facet_completeness = self._as_float(
            evidence_metrics.get("facet_completeness_rate")
        )
        if (
            quote_alignment is not None
            and quote_alignment < self.thresholds.evidence_quality_min
        ):
            return True
        if (
            facet_completeness is not None
            and facet_completeness < self.thresholds.evidence_quality_min
        ):
            return True

        confidence_metrics = self._extract_probe_metrics(
            probes,
            "observation_confidence_probe",
        )
        low_confidence_ratio = self._as_float(
            confidence_metrics.get("low_confidence_ratio")
        )
        if (
            low_confidence_ratio is not None
            and low_confidence_ratio > self.thresholds.low_confidence_ratio_max
        ):
            return True

        return False

    def _extract_probe_metrics(
        self,
        probes: dict[str, Any],
        probe_name: str,
    ) -> dict[str, Any]:
        payload = probes.get(probe_name)
        if payload is None:
            return {}
        metrics = getattr(payload, "metrics", None)
        if isinstance(metrics, dict):
            return metrics
        if isinstance(payload, dict):
            nested = payload.get("metrics")
            if isinstance(nested, dict):
                return nested
            return payload
        return {}

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


__all__ = ["PRIORITY_LAYERS", "SearchPolicy", "SearchPolicyThresholds"]
