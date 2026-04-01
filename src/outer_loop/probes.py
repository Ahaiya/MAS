"""Unified outer-loop probe interfaces over evaluation artifacts."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from scripts.compute_coverage_metrics import compute_metrics_for_essay
from src.outer_loop.metrics.consistency import compute_consistency
from src.outer_loop.metrics.qwk import qwk_for_dimension

_DIM_ORDER = [
    "ideas_content",
    "organization",
    "voice",
    "word_choice",
    "sentence_fluency",
    "conventions",
]

_PROBE_ARTIFACT_HINTS = {
    "run_trace.json",
    "feedback.json",
    "observations.json",
    "evidence_spans.json",
    "hypotheses.json",
    "conflicts.json",
    "adjudication_records.json",
}


@dataclass
class ProbeResult:
    probe_name: str
    essay_count: int
    metrics: dict[str, float | int | None]
    per_essay: dict[str, dict] | None = None


def _safe_div(numerator: int | float, denominator: int | float) -> float | None:
    if denominator == 0:
        return None
    return float(numerator) / float(denominator)


def _mean(values: list[float | int | None]) -> float | None:
    valid = [float(v) for v in values if isinstance(v, (int, float))]
    if not valid:
        return None
    return sum(valid) / len(valid)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _as_float(value: Any) -> float | None:
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


def _as_non_empty_str(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
    return None


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(data, dict):
        return data
    return None


def _looks_like_essay_dir(path: Path) -> bool:
    return any((path / filename).exists() for filename in _PROBE_ARTIFACT_HINTS)


def _collect_essay_dirs(artifacts_dir: Path) -> list[tuple[str, Path]]:
    artifacts_dir = Path(artifacts_dir)
    if not artifacts_dir.exists() or not artifacts_dir.is_dir():
        return []

    if _looks_like_essay_dir(artifacts_dir):
        return [(artifacts_dir.name, artifacts_dir)]

    level_1 = sorted(p for p in artifacts_dir.iterdir() if p.is_dir())
    direct = [(p.name, p) for p in level_1 if _looks_like_essay_dir(p)]
    if direct:
        return direct

    nested: list[tuple[str, Path]] = []
    for parent in level_1:
        for child in sorted(p for p in parent.iterdir() if p.is_dir()):
            if _looks_like_essay_dir(child):
                nested.append((child.name, child))
    return nested


def _empty_probe(probe_name: str) -> ProbeResult:
    return ProbeResult(
        probe_name=probe_name,
        essay_count=0,
        metrics={},
        per_essay={},
    )


def coverage_probe(artifacts_dir: Path, **_: Any) -> ProbeResult:
    per_essay: dict[str, dict[str, Any]] = {}
    recall_values: list[float] = []
    precision_values: list[float] = []
    boundary_values: list[float] = []

    for essay_id, essay_dir in _collect_essay_dirs(artifacts_dir):
        try:
            payload = compute_metrics_for_essay(essay_dir)
        except Exception:
            continue

        overall = payload.get("overall", {})
        recall = _as_float(
            ((overall.get("coverage_recall_rate") or {}).get("rate"))
        )
        precision = _as_float(
            ((overall.get("coverage_precision_rate") or {}).get("rate"))
        )
        boundary = _as_float(
            ((overall.get("chunk_boundary_quality") or {}).get("cross_chunk_span_ratio"))
        )

        if recall is not None:
            recall_values.append(recall)
        if precision is not None:
            precision_values.append(precision)
        if boundary is not None:
            boundary_values.append(boundary)

        per_essay[essay_id] = {
            "coverage_recall_rate": recall,
            "coverage_precision_rate": precision,
            "chunk_boundary_quality": boundary,
        }

    if not per_essay:
        return _empty_probe("coverage_probe")

    return ProbeResult(
        probe_name="coverage_probe",
        essay_count=len(per_essay),
        metrics={
            "coverage_recall_rate": _mean(recall_values),
            "coverage_precision_rate": _mean(precision_values),
            "chunk_boundary_quality": _mean(boundary_values),
        },
        per_essay=per_essay,
    )


def evidence_quality_probe(artifacts_dir: Path, **_: Any) -> ProbeResult:
    per_essay: dict[str, dict[str, Any]] = {}
    quote_alignment_values: list[float] = []
    facet_completeness_values: list[float] = []

    for essay_id, essay_dir in _collect_essay_dirs(artifacts_dir):
        spans_data = _load_json(essay_dir / "evidence_spans.json")
        if spans_data is None:
            continue
        spans = list(spans_data.get("evidence_spans") or [])

        total_spans = len(spans)
        aligned_spans = 0
        for span in spans:
            text_quote = _as_non_empty_str(span.get("text_quote"))
            unit_id = _as_non_empty_str(span.get("unit_id"))
            if text_quote is not None and unit_id is not None:
                aligned_spans += 1
        quote_alignment_rate = _safe_div(aligned_spans, total_spans)

        observations_data = _load_json(essay_dir / "observations.json")
        total_facets = 0
        complete_facets = 0
        if observations_data is not None:
            for observation in list(observations_data.get("observations") or []):
                for finding in list(observation.get("facet_findings") or []):
                    total_facets += 1
                    supporting = list(finding.get("supporting_span_ids") or [])
                    counter = list(finding.get("counter_span_ids") or [])
                    if supporting or counter:
                        complete_facets += 1
        facet_completeness_rate = _safe_div(complete_facets, total_facets)

        if quote_alignment_rate is not None:
            quote_alignment_values.append(quote_alignment_rate)
        if facet_completeness_rate is not None:
            facet_completeness_values.append(facet_completeness_rate)

        per_essay[essay_id] = {
            "quote_alignment_rate": quote_alignment_rate,
            "facet_completeness_rate": facet_completeness_rate,
            "span_count": total_spans,
        }

    if not per_essay:
        return _empty_probe("evidence_quality_probe")

    return ProbeResult(
        probe_name="evidence_quality_probe",
        essay_count=len(per_essay),
        metrics={
            "quote_alignment_rate": _mean(quote_alignment_values),
            "facet_completeness_rate": _mean(facet_completeness_values),
        },
        per_essay=per_essay,
    )


def _normalize_confidence(value: Any) -> float | None:
    numeric = _as_float(value)
    if numeric is not None:
        return numeric
    text = _as_non_empty_str(value)
    if text is None:
        return None
    lowered = text.lower()
    mapping = {
        "low": 0.25,
        "medium": 0.6,
        "high": 0.9,
    }
    return mapping.get(lowered)


def observation_confidence_probe(artifacts_dir: Path, **_: Any) -> ProbeResult:
    per_essay: dict[str, dict[str, Any]] = {}
    mean_values: list[float] = []
    low_ratio_values: list[float] = []

    for essay_id, essay_dir in _collect_essay_dirs(artifacts_dir):
        observations_data = _load_json(essay_dir / "observations.json")
        if observations_data is None:
            continue
        observations = list(observations_data.get("observations") or [])
        if not observations:
            continue

        normalized: list[float] = []
        low_count = 0
        for obs in observations:
            raw = obs.get("observation_confidence")
            score = _normalize_confidence(raw)
            if score is not None:
                normalized.append(score)

            raw_text = _as_non_empty_str(raw)
            if raw_text is not None and raw_text.lower() == "low":
                low_count += 1
            elif isinstance(score, float) and score < 0.5:
                low_count += 1

        mean_confidence = _mean([*normalized])
        low_confidence_ratio = _safe_div(low_count, len(observations))

        if mean_confidence is not None:
            mean_values.append(mean_confidence)
        if low_confidence_ratio is not None:
            low_ratio_values.append(low_confidence_ratio)

        per_essay[essay_id] = {
            "observation_count": len(observations),
            "mean_confidence": mean_confidence,
            "low_confidence_ratio": low_confidence_ratio,
        }

    if not per_essay:
        return _empty_probe("observation_confidence_probe")

    return ProbeResult(
        probe_name="observation_confidence_probe",
        essay_count=len(per_essay),
        metrics={
            "mean_confidence": _mean(mean_values),
            "low_confidence_ratio": _mean(low_ratio_values),
        },
        per_essay=per_essay,
    )


def _load_hypotheses_by_dimension(essay_dir: Path) -> tuple[str, dict[str, list[int]]] | None:
    data = _load_json(essay_dir / "hypotheses.json")
    if data is None:
        return None
    run_id = _as_non_empty_str(data.get("run_id")) or essay_dir.name
    grouped: dict[str, list[int]] = {}
    for item in list(data.get("hypotheses") or []):
        dim_id = _as_non_empty_str(item.get("dimension_id"))
        if dim_id is None:
            continue
        score = _as_int(((item.get("score") or {}).get("canonical_score")))
        if score is None:
            score = _as_int(((item.get("score") or {}).get("score_value")))
        if score is None:
            continue
        grouped.setdefault(dim_id, []).append(score)
    if not grouped:
        return None
    return run_id, grouped


def rater_consistency_probe(artifacts_dir: Path, **_: Any) -> ProbeResult:
    per_essay: dict[str, dict[str, Any]] = {}
    overall_values: list[float] = []
    per_dimension_values: dict[str, list[float]] = {}

    for essay_id, essay_dir in _collect_essay_dirs(artifacts_dir):
        loaded = _load_hypotheses_by_dimension(essay_dir)
        if loaded is None:
            continue
        run_id, grouped = loaded
        report = compute_consistency(run_id, grouped)

        per_dim: dict[str, float] = {}
        for dim in report.dimensions:
            disagreement = 1.0 - dim.agreement_ratio
            per_dim[dim.dimension_id] = disagreement
            per_dimension_values.setdefault(dim.dimension_id, []).append(disagreement)

        overall_disagreement = 1.0 - report.overall_agreement_ratio
        overall_values.append(overall_disagreement)

        per_essay[essay_id] = {
            "overall_disagreement_rate": overall_disagreement,
            "dimensions_with_conflict": report.dimensions_with_conflict,
            "total_conflict_count": report.total_conflict_count,
            "per_dimension_disagreement": per_dim,
        }

    if not per_essay:
        return _empty_probe("rater_consistency_probe")

    metrics: dict[str, float | int | None] = {
        "overall_disagreement_rate": _mean(overall_values),
        "avg_dimensions_with_conflict": _mean(
            [
                _as_float(item.get("dimensions_with_conflict"))
                for item in per_essay.values()
            ]
        ),
    }
    for dim_id, values in per_dimension_values.items():
        metrics[f"dim_{dim_id}_disagreement_rate"] = _mean(values)

    return ProbeResult(
        probe_name="rater_consistency_probe",
        essay_count=len(per_essay),
        metrics=metrics,
        per_essay=per_essay,
    )


def conflict_pattern_probe(artifacts_dir: Path, **_: Any) -> ProbeResult:
    per_essay: dict[str, dict[str, Any]] = {}
    total_conflicts = 0
    essays_with_conflicts = 0
    type_counts: dict[str, int] = {}

    for essay_id, essay_dir in _collect_essay_dirs(artifacts_dir):
        data = _load_json(essay_dir / "conflicts.json")
        if data is None:
            continue

        conflicts = list(data.get("conflicts") or [])
        conflict_count = len(conflicts)
        if conflict_count > 0:
            essays_with_conflicts += 1
        total_conflicts += conflict_count

        local_counts: dict[str, int] = {}
        for conflict in conflicts:
            conflict_type = _as_non_empty_str(conflict.get("conflict_type")) or "unknown"
            local_counts[conflict_type] = local_counts.get(conflict_type, 0) + 1
            type_counts[conflict_type] = type_counts.get(conflict_type, 0) + 1

        per_essay[essay_id] = {
            "conflict_count": conflict_count,
            "conflict_types": local_counts,
            "triggered": conflict_count > 0,
        }

    if not per_essay:
        return _empty_probe("conflict_pattern_probe")

    metrics: dict[str, float | int | None] = {
        "total_conflicts": total_conflicts,
        "essays_with_conflicts": essays_with_conflicts,
        "conflict_trigger_rate": _safe_div(essays_with_conflicts, len(per_essay)),
    }
    for conflict_type, count in type_counts.items():
        metrics[f"conflict_type_{conflict_type}_ratio"] = _safe_div(count, total_conflicts)

    return ProbeResult(
        probe_name="conflict_pattern_probe",
        essay_count=len(per_essay),
        metrics=metrics,
        per_essay=per_essay,
    )


def resolution_cost_probe(artifacts_dir: Path, **_: Any) -> ProbeResult:
    per_essay: dict[str, dict[str, Any]] = {}
    third_rates: list[float] = []
    fallback_rates: list[float] = []
    total_records = 0

    for essay_id, essay_dir in _collect_essay_dirs(artifacts_dir):
        data = _load_json(essay_dir / "adjudication_records.json")
        if data is None:
            continue

        records = list(data.get("adjudication_records") or [])
        record_count = len(records)
        total_records += record_count

        third_rater_count = 0
        fallback_count = 0
        for record in records:
            path = (_as_non_empty_str(record.get("resolution_path")) or "").lower()
            if path == "third_rater":
                third_rater_count += 1
            is_resolved = bool(record.get("is_resolved"))
            if (not is_resolved) or ("fallback" in path):
                fallback_count += 1

        feedback = _load_json(essay_dir / "feedback.json")
        dimensions = (feedback or {}).get("dimensions") or {}
        dimension_count = len(dimensions) if isinstance(dimensions, dict) else 0
        if dimension_count <= 0:
            dimension_count = record_count

        third_rater_trigger_rate = _safe_div(third_rater_count, dimension_count)
        fallback_rate = _safe_div(fallback_count, record_count)

        if third_rater_trigger_rate is not None:
            third_rates.append(third_rater_trigger_rate)
        if fallback_rate is not None:
            fallback_rates.append(fallback_rate)

        per_essay[essay_id] = {
            "adjudication_count": record_count,
            "third_rater_count": third_rater_count,
            "fallback_count": fallback_count,
            "third_rater_trigger_rate": third_rater_trigger_rate,
            "fallback_rate": fallback_rate,
        }

    if not per_essay:
        return _empty_probe("resolution_cost_probe")

    return ProbeResult(
        probe_name="resolution_cost_probe",
        essay_count=len(per_essay),
        metrics={
            "total_adjudication_records": total_records,
            "third_rater_trigger_rate": _mean(third_rates),
            "fallback_rate": _mean(fallback_rates),
        },
        per_essay=per_essay,
    )


def feedback_grounding_probe(artifacts_dir: Path, **_: Any) -> ProbeResult:
    per_essay: dict[str, dict[str, Any]] = {}
    closure_values: list[float] = []
    violations_values: list[float] = []
    essays_with_violations = 0

    for essay_id, essay_dir in _collect_essay_dirs(artifacts_dir):
        feedback = _load_json(essay_dir / "feedback.json")
        if feedback is None:
            continue

        dimensions = feedback.get("dimensions") or {}
        if not isinstance(dimensions, dict):
            continue

        total_dimensions = len(dimensions)
        closed = 0
        for dim_data in dimensions.values():
            descriptors = list(dim_data.get("descriptor_refs") or [])
            evidence = list(dim_data.get("evidence_span_ids") or [])
            if descriptors and evidence:
                closed += 1

        closure_rate = _safe_div(closed, total_dimensions)
        violations = list(feedback.get("violations") or [])
        violation_count = len(violations)
        if violation_count > 0:
            essays_with_violations += 1

        if closure_rate is not None:
            closure_values.append(closure_rate)
        violations_values.append(float(violation_count))

        per_essay[essay_id] = {
            "descriptor_evidence_closure_rate": closure_rate,
            "violation_count": violation_count,
            "dimension_count": total_dimensions,
        }

    if not per_essay:
        return _empty_probe("feedback_grounding_probe")

    return ProbeResult(
        probe_name="feedback_grounding_probe",
        essay_count=len(per_essay),
        metrics={
            "descriptor_evidence_closure_rate": _mean(closure_values),
            "avg_violations_per_essay": _mean(violations_values),
            "violations_trigger_rate": _safe_div(essays_with_violations, len(per_essay)),
        },
        per_essay=per_essay,
    )


def _load_human_scores(
    tsv_path: Path,
) -> dict[str, dict[str, tuple[int | None, int | None]]]:
    result: dict[str, dict[str, tuple[int | None, int | None]]] = {}
    if not tsv_path.exists():
        return result

    with tsv_path.open(encoding="latin-1") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            essay_id = (row.get("essay_id") or "").strip()
            if not essay_id:
                continue

            scores: dict[str, tuple[int | None, int | None]] = {}
            for idx, dim_id in enumerate(_DIM_ORDER, start=1):
                r1 = _as_int(row.get(f"rater1_trait{idx}"))
                r2 = _as_int(row.get(f"rater2_trait{idx}"))
                scores[dim_id] = (r1, r2)

            scores["_total"] = (_as_int(row.get("domain1_score")), None)
            result[essay_id] = scores
    return result


def _pick_human_score(
    r1: int | None,
    r2: int | None,
    rater: str,
) -> int | None:
    if rater == "rater1":
        return r1
    if rater == "rater2":
        return r2
    if r1 is not None and r2 is not None:
        return int(round((r1 + r2) / 2))
    return r1 if r1 is not None else r2


def _load_mas_scores_for_qwk(
    essay_dir: Path,
) -> tuple[dict[str, int], int | None] | None:
    feedback = _load_json(essay_dir / "feedback.json")
    if feedback is None:
        return None

    trace = _load_json(essay_dir / "run_trace.json")
    if trace is not None and trace.get("status") not in (None, "completed"):
        return None

    dimensions = feedback.get("dimensions") or {}
    if not isinstance(dimensions, dict):
        return None

    dim_scores: dict[str, int] = {}
    for dim_id in _DIM_ORDER:
        dim_data = dimensions.get(dim_id) or {}
        score = _as_int(dim_data.get("canonical_score"))
        if score is None:
            score = _as_int(dim_data.get("final_score"))
        if score is not None:
            dim_scores[dim_id] = score

    composite = feedback.get("composite") or {}
    composite_score = _as_int(((composite.get("composite_score") or {}).get("canonical_score")))
    return dim_scores, composite_score


def qwk_probe(
    artifacts_dir: Path,
    *,
    tsv_path: Path | None = None,
    rater: str = "rater1",
    **_: Any,
) -> ProbeResult:
    if tsv_path is None:
        return _empty_probe("qwk_probe")
    if rater not in {"rater1", "rater2", "average"}:
        raise ValueError("rater must be one of: rater1, rater2, average")

    human_scores = _load_human_scores(Path(tsv_path))
    if not human_scores:
        return _empty_probe("qwk_probe")

    per_essay: dict[str, dict[str, Any]] = {}
    y_true_by_dim: dict[str, list[int]] = {dim_id: [] for dim_id in _DIM_ORDER}
    y_pred_by_dim: dict[str, list[int]] = {dim_id: [] for dim_id in _DIM_ORDER}
    composite_true: list[int] = []
    composite_pred: list[int] = []

    for essay_id, essay_dir in _collect_essay_dirs(artifacts_dir):
        if essay_id not in human_scores:
            continue
        loaded = _load_mas_scores_for_qwk(essay_dir)
        if loaded is None:
            continue
        mas_scores, mas_composite = loaded
        human = human_scores[essay_id]

        dim_pairs: dict[str, dict[str, int | None]] = {}
        for dim_id in _DIM_ORDER:
            human_pair = human.get(dim_id, (None, None))
            y_true = _pick_human_score(human_pair[0], human_pair[1], rater)
            y_pred = mas_scores.get(dim_id)
            dim_pairs[dim_id] = {"human": y_true, "mas": y_pred}
            if y_true is not None and y_pred is not None:
                y_true_by_dim[dim_id].append(y_true)
                y_pred_by_dim[dim_id].append(y_pred)

        human_total, _ = human.get("_total", (None, None))
        if human_total is not None and mas_composite is not None:
            composite_true.append(human_total)
            composite_pred.append(mas_composite)

        per_essay[essay_id] = {
            "dimensions": dim_pairs,
            "human_composite": human_total,
            "mas_composite": mas_composite,
        }

    if not per_essay:
        return _empty_probe("qwk_probe")

    metrics: dict[str, float | int | None] = {}
    for dim_id in _DIM_ORDER:
        y_true = y_true_by_dim[dim_id]
        y_pred = y_pred_by_dim[dim_id]
        metrics[f"n_{dim_id}"] = len(y_true)
        if len(y_true) >= 2:
            result = qwk_for_dimension(dim_id, y_true, y_pred, min_score=1, max_score=6)
            metrics[f"qwk_{dim_id}"] = result.qwk
        else:
            metrics[f"qwk_{dim_id}"] = None

    metrics["n_composite"] = len(composite_true)
    if len(composite_true) >= 2:
        composite = qwk_for_dimension(
            "composite",
            composite_true,
            composite_pred,
            min_score=10,
            max_score=60,
        )
        metrics["qwk_composite"] = composite.qwk
    else:
        metrics["qwk_composite"] = None

    return ProbeResult(
        probe_name="qwk_probe",
        essay_count=len(per_essay),
        metrics=metrics,
        per_essay=per_essay,
    )


def _parse_iso(ts: Any) -> datetime | None:
    text = _as_non_empty_str(ts)
    if text is None:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _extract_token_count(metadata: dict[str, Any]) -> int:
    if not isinstance(metadata, dict):
        return 0

    total = 0
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "tokens"):
        value = _as_int(metadata.get(key))
        if value is not None:
            total += value

    token_usage = metadata.get("token_usage")
    if isinstance(token_usage, dict):
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = _as_int(token_usage.get(key))
            if value is not None:
                total += value
    return total


def cost_probe(artifacts_dir: Path, **_: Any) -> ProbeResult:
    per_essay: dict[str, dict[str, Any]] = {}
    latency_values: list[float] = []
    retry_values: list[float] = []
    token_values: list[float] = []
    stage_latencies: dict[str, list[float]] = {}

    for essay_id, essay_dir in _collect_essay_dirs(artifacts_dir):
        trace = _load_json(essay_dir / "run_trace.json")
        if trace is None:
            continue

        started = _parse_iso(trace.get("started_at"))
        finished = _parse_iso(trace.get("finished_at"))
        total_latency = None
        if started is not None and finished is not None:
            total_latency = (finished - started).total_seconds()

        node_traces = list(trace.get("node_traces") or [])
        retry_count = 0
        total_tokens = 0
        for node in node_traces:
            fallback_history = list(node.get("fallback_history") or [])
            retry_count += len(fallback_history)
            total_tokens += _extract_token_count(node.get("metadata") or {})

            node_started = _parse_iso(node.get("started_at"))
            node_finished = _parse_iso(node.get("finished_at"))
            if node_started is None or node_finished is None:
                continue
            node_latency = (node_finished - node_started).total_seconds()
            node_id = _as_non_empty_str(node.get("node_id")) or "unknown"
            stage_latencies.setdefault(node_id, []).append(node_latency)

        retry_rate = _safe_div(retry_count, len(node_traces))

        if total_latency is not None:
            latency_values.append(total_latency)
        if retry_rate is not None:
            retry_values.append(retry_rate)
        token_values.append(float(total_tokens))

        per_essay[essay_id] = {
            "total_latency_seconds": total_latency,
            "retry_count": retry_count,
            "retry_rate": retry_rate,
            "total_tokens": total_tokens,
            "node_count": len(node_traces),
        }

    if not per_essay:
        return _empty_probe("cost_probe")

    metrics: dict[str, float | int | None] = {
        "avg_total_latency_seconds": _mean(latency_values),
        "avg_retry_rate": _mean(retry_values),
        "avg_total_tokens": _mean(token_values),
    }
    for node_id, values in stage_latencies.items():
        metrics[f"latency_{node_id}"] = _mean(values)

    return ProbeResult(
        probe_name="cost_probe",
        essay_count=len(per_essay),
        metrics=metrics,
        per_essay=per_essay,
    )


_PROBES: dict[str, Callable[..., ProbeResult]] = {
    "coverage_probe": coverage_probe,
    "evidence_quality_probe": evidence_quality_probe,
    "observation_confidence_probe": observation_confidence_probe,
    "rater_consistency_probe": rater_consistency_probe,
    "conflict_pattern_probe": conflict_pattern_probe,
    "resolution_cost_probe": resolution_cost_probe,
    "feedback_grounding_probe": feedback_grounding_probe,
    "qwk_probe": qwk_probe,
    "cost_probe": cost_probe,
}


def run_probe(probe_name: str, artifacts_dir: Path, **kwargs: Any) -> ProbeResult:
    probe = _PROBES.get(probe_name)
    if probe is None:
        raise ValueError(f"Unknown probe: {probe_name}")
    return probe(Path(artifacts_dir), **kwargs)


def run_probes(
    probe_names: list[str],
    artifacts_dir: Path,
    **kwargs: Any,
) -> dict[str, ProbeResult]:
    return {
        name: run_probe(name, artifacts_dir, **kwargs)
        for name in probe_names
    }


__all__ = [
    "ProbeResult",
    "coverage_probe",
    "evidence_quality_probe",
    "observation_confidence_probe",
    "rater_consistency_probe",
    "conflict_pattern_probe",
    "resolution_cost_probe",
    "feedback_grounding_probe",
    "qwk_probe",
    "cost_probe",
    "run_probe",
    "run_probes",
]
