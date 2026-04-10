"""Unified outer-loop probe interfaces over evaluation artifacts."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from src.outer_loop.metrics.consistency import compute_consistency
from src.outer_loop.metrics.qwk import qwk_for_dimension
from src.utils.compute_coverage_metrics import compute_metrics_for_essay

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
    sample_count: int
    metrics: dict[str, float | int | None]
    per_sample: dict[str, dict] | None = None


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


def _looks_like_sample_dir(path: Path) -> bool:
    return any((path / filename).exists() for filename in _PROBE_ARTIFACT_HINTS)


def _collect_sample_dirs(artifacts_dir: Path) -> list[tuple[str, Path]]:
    artifacts_dir = Path(artifacts_dir)
    if not artifacts_dir.exists() or not artifacts_dir.is_dir():
        return []

    if _looks_like_sample_dir(artifacts_dir):
        return [(artifacts_dir.name, artifacts_dir)]

    level_1 = sorted(p for p in artifacts_dir.iterdir() if p.is_dir())
    direct = [(p.name, p) for p in level_1 if _looks_like_sample_dir(p)]
    if direct:
        return direct

    nested: list[tuple[str, Path]] = []
    for parent in level_1:
        for child in sorted(p for p in parent.iterdir() if p.is_dir()):
            if _looks_like_sample_dir(child):
                nested.append((child.name, child))
    return nested


def _empty_probe(probe_name: str) -> ProbeResult:
    return ProbeResult(
        probe_name=probe_name,
        sample_count=0,
        metrics={},
        per_sample={},
    )


def coverage_probe(artifacts_dir: Path, **_: Any) -> ProbeResult:
    per_sample: dict[str, dict[str, Any]] = {}
    recall_values: list[float] = []
    precision_values: list[float] = []
    boundary_values: list[float] = []

    for sample_id, sample_dir in _collect_sample_dirs(artifacts_dir):
        try:
            payload = compute_metrics_for_essay(sample_dir)
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

        per_sample[sample_id] = {
            "coverage_recall_rate": recall,
            "coverage_precision_rate": precision,
            "chunk_boundary_quality": boundary,
        }

    if not per_sample:
        return _empty_probe("coverage_probe")

    return ProbeResult(
        probe_name="coverage_probe",
        sample_count=len(per_sample),
        metrics={
            "coverage_recall_rate": _mean(recall_values),
            "coverage_precision_rate": _mean(precision_values),
            "chunk_boundary_quality": _mean(boundary_values),
        },
        per_sample=per_sample,
    )


def evidence_quality_probe(artifacts_dir: Path, **_: Any) -> ProbeResult:
    per_sample: dict[str, dict[str, Any]] = {}
    quote_alignment_values: list[float] = []
    facet_completeness_values: list[float] = []

    for sample_id, sample_dir in _collect_sample_dirs(artifacts_dir):
        spans_data = _load_json(sample_dir / "evidence_spans.json")
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

        observations_data = _load_json(sample_dir / "observations.json")
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

        per_sample[sample_id] = {
            "quote_alignment_rate": quote_alignment_rate,
            "facet_completeness_rate": facet_completeness_rate,
            "span_count": total_spans,
        }

    if not per_sample:
        return _empty_probe("evidence_quality_probe")

    return ProbeResult(
        probe_name="evidence_quality_probe",
        sample_count=len(per_sample),
        metrics={
            "quote_alignment_rate": _mean(quote_alignment_values),
            "facet_completeness_rate": _mean(facet_completeness_values),
        },
        per_sample=per_sample,
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
    per_sample: dict[str, dict[str, Any]] = {}
    mean_values: list[float] = []
    low_ratio_values: list[float] = []

    for sample_id, sample_dir in _collect_sample_dirs(artifacts_dir):
        observations_data = _load_json(sample_dir / "observations.json")
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

        per_sample[sample_id] = {
            "observation_count": len(observations),
            "mean_confidence": mean_confidence,
            "low_confidence_ratio": low_confidence_ratio,
        }

    if not per_sample:
        return _empty_probe("observation_confidence_probe")

    return ProbeResult(
        probe_name="observation_confidence_probe",
        sample_count=len(per_sample),
        metrics={
            "mean_confidence": _mean(mean_values),
            "low_confidence_ratio": _mean(low_ratio_values),
        },
        per_sample=per_sample,
    )


def _load_hypotheses_by_dimension(sample_dir: Path) -> tuple[str, dict[str, list[int]]] | None:
    data = _load_json(sample_dir / "hypotheses.json")
    if data is None:
        return None
    run_id = _as_non_empty_str(data.get("run_id")) or sample_dir.name
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
    per_sample: dict[str, dict[str, Any]] = {}
    overall_values: list[float] = []
    per_dimension_values: dict[str, list[float]] = {}

    for sample_id, sample_dir in _collect_sample_dirs(artifacts_dir):
        loaded = _load_hypotheses_by_dimension(sample_dir)
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

        per_sample[sample_id] = {
            "overall_disagreement_rate": overall_disagreement,
            "dimensions_with_conflict": report.dimensions_with_conflict,
            "total_conflict_count": report.total_conflict_count,
            "per_dimension_disagreement": per_dim,
        }

    if not per_sample:
        return _empty_probe("rater_consistency_probe")

    metrics: dict[str, float | int | None] = {
        "overall_disagreement_rate": _mean(overall_values),
        "avg_dimensions_with_conflict": _mean(
            [
                _as_float(item.get("dimensions_with_conflict"))
                for item in per_sample.values()
            ]
        ),
    }
    for dim_id, values in per_dimension_values.items():
        metrics[f"dim_{dim_id}_disagreement_rate"] = _mean(values)

    return ProbeResult(
        probe_name="rater_consistency_probe",
        sample_count=len(per_sample),
        metrics=metrics,
        per_sample=per_sample,
    )


def conflict_pattern_probe(artifacts_dir: Path, **_: Any) -> ProbeResult:
    per_sample: dict[str, dict[str, Any]] = {}
    total_conflicts = 0
    samples_with_conflicts = 0
    type_counts: dict[str, int] = {}

    for sample_id, sample_dir in _collect_sample_dirs(artifacts_dir):
        data = _load_json(sample_dir / "conflicts.json")
        if data is None:
            continue

        conflicts = list(data.get("conflicts") or [])
        conflict_count = len(conflicts)
        if conflict_count > 0:
            samples_with_conflicts += 1
        total_conflicts += conflict_count

        local_counts: dict[str, int] = {}
        for conflict in conflicts:
            conflict_type = _as_non_empty_str(conflict.get("conflict_type")) or "unknown"
            local_counts[conflict_type] = local_counts.get(conflict_type, 0) + 1
            type_counts[conflict_type] = type_counts.get(conflict_type, 0) + 1

        per_sample[sample_id] = {
            "conflict_count": conflict_count,
            "conflict_types": local_counts,
            "triggered": conflict_count > 0,
        }

    if not per_sample:
        return _empty_probe("conflict_pattern_probe")

    metrics: dict[str, float | int | None] = {
        "total_conflicts": total_conflicts,
        "samples_with_conflicts": samples_with_conflicts,
        "conflict_trigger_rate": _safe_div(samples_with_conflicts, len(per_sample)),
    }
    for conflict_type, count in type_counts.items():
        metrics[f"conflict_type_{conflict_type}_ratio"] = _safe_div(count, total_conflicts)

    return ProbeResult(
        probe_name="conflict_pattern_probe",
        sample_count=len(per_sample),
        metrics=metrics,
        per_sample=per_sample,
    )


def resolution_cost_probe(artifacts_dir: Path, **_: Any) -> ProbeResult:
    per_sample: dict[str, dict[str, Any]] = {}
    third_rates: list[float] = []
    fallback_rates: list[float] = []
    total_records = 0

    for sample_id, sample_dir in _collect_sample_dirs(artifacts_dir):
        data = _load_json(sample_dir / "adjudication_records.json")
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

        feedback = _load_json(sample_dir / "feedback.json")
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

        per_sample[sample_id] = {
            "adjudication_count": record_count,
            "third_rater_count": third_rater_count,
            "fallback_count": fallback_count,
            "third_rater_trigger_rate": third_rater_trigger_rate,
            "fallback_rate": fallback_rate,
        }

    if not per_sample:
        return _empty_probe("resolution_cost_probe")

    return ProbeResult(
        probe_name="resolution_cost_probe",
        sample_count=len(per_sample),
        metrics={
            "total_adjudication_records": total_records,
            "third_rater_trigger_rate": _mean(third_rates),
            "fallback_rate": _mean(fallback_rates),
        },
        per_sample=per_sample,
    )


def feedback_grounding_probe(artifacts_dir: Path, **_: Any) -> ProbeResult:
    per_sample: dict[str, dict[str, Any]] = {}
    closure_values: list[float] = []
    violations_values: list[float] = []
    samples_with_violations = 0

    for sample_id, sample_dir in _collect_sample_dirs(artifacts_dir):
        feedback = _load_json(sample_dir / "feedback.json")
        if feedback is None:
            continue

        dimensions = feedback.get("dimensions") or {}
        if not isinstance(dimensions, dict):
            continue

        total_dimensions = len(dimensions)
        closed = 0
        for dim_data in dimensions.values():
            descriptors = list(dim_data.get("descriptor_refs") or [])
            evidence = list(dim_data.get("evidence") or dim_data.get("evidence_span_ids") or [])
            if descriptors and evidence:
                closed += 1

        closure_rate = _safe_div(closed, total_dimensions)
        violations = list(((feedback.get("audit") or {}).get("violations")) or feedback.get("violations") or [])
        violation_count = len(violations)
        if violation_count > 0:
            samples_with_violations += 1

        if closure_rate is not None:
            closure_values.append(closure_rate)
        violations_values.append(float(violation_count))

        per_sample[sample_id] = {
            "descriptor_evidence_closure_rate": closure_rate,
            "violation_count": violation_count,
            "dimension_count": total_dimensions,
        }

    if not per_sample:
        return _empty_probe("feedback_grounding_probe")

    return ProbeResult(
        probe_name="feedback_grounding_probe",
        sample_count=len(per_sample),
        metrics={
            "descriptor_evidence_closure_rate": _mean(closure_values),
            "avg_violations_per_sample": _mean(violations_values),
            "violations_trigger_rate": _safe_div(samples_with_violations, len(per_sample)),
        },
        per_sample=per_sample,
    )


def _first_present_int(row: dict[str, Any], keys: list[str]) -> int | None:
    for key in keys:
        value = _as_int(row.get(key))
        if value is not None:
            return value
    return None


def _sample_id_from_row(row: dict[str, Any]) -> str:
    return (
        _as_non_empty_str(row.get("sample_id"))
        or _as_non_empty_str(row.get("essay_id"))
        or ""
    )


def _score_range(scores: list[int]) -> tuple[int, int] | None:
    if not scores:
        return None
    min_score = min(scores)
    max_score = max(scores)
    if min_score >= max_score:
        return None
    return min_score, max_score


def _explicit_dimension_ids_from_row(row: dict[str, Any]) -> list[str]:
    reserved = {
        "essay",
        "essay_id",
        "sample_id",
        "domain1_score",
        "composite_score",
        "total_score",
        "human_total_score",
    }
    discovered: list[str] = []

    for raw_key in row.keys():
        if not isinstance(raw_key, str):
            continue
        key = raw_key.strip()
        if not key or key in reserved:
            continue

        candidate: str | None = None
        for prefix in ("rater1_", "rater2_", "human_rater1_", "human_rater2_", "gold_", "label_", "human_"):
            if key.startswith(prefix):
                candidate = key[len(prefix):]
                break
        if candidate is None and key.endswith("_rater1"):
            candidate = key[: -len("_rater1")]
        if candidate is None and key.endswith("_rater2"):
            candidate = key[: -len("_rater2")]
        if candidate is None and key not in reserved:
            candidate = key

        cleaned = str(candidate or "").strip()
        if (
            cleaned
            and cleaned not in reserved
            and cleaned not in discovered
            and cleaned not in {"rater1", "rater2"}
        ):
            discovered.append(cleaned)

    return discovered


def _load_reference_scores(
    tsv_path: Path,
) -> tuple[dict[str, dict[str, tuple[int | None, int | None]]], list[str]]:
    result: dict[str, dict[str, tuple[int | None, int | None]]] = {}
    if not tsv_path.exists():
        return result, []

    rows: list[dict[str, Any]] = []
    dimension_ids: list[str] = []
    seen_dim_ids: set[str] = set()

    with tsv_path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            rows.append(row)
            for dim_id in _explicit_dimension_ids_from_row(row):
                if dim_id not in seen_dim_ids:
                    seen_dim_ids.add(dim_id)
                    dimension_ids.append(dim_id)

    for row in rows:
        sample_id = _sample_id_from_row(row)
        if not sample_id:
            continue

        scores: dict[str, tuple[int | None, int | None]] = {}
        for dim_id in dimension_ids:
            r1 = _first_present_int(
                row,
                [
                    f"rater1_{dim_id}",
                    f"human_rater1_{dim_id}",
                    f"{dim_id}_rater1",
                ],
            )
            r2 = _first_present_int(
                row,
                [
                    f"rater2_{dim_id}",
                    f"human_rater2_{dim_id}",
                    f"{dim_id}_rater2",
                ],
            )
            if r1 is None and r2 is None:
                single_score = _first_present_int(
                    row,
                    [
                        dim_id,
                        f"human_{dim_id}",
                        f"gold_{dim_id}",
                        f"label_{dim_id}",
                    ],
                )
                r1 = single_score
                r2 = single_score
            scores[dim_id] = (r1, r2)

        scores["_total"] = (
            _first_present_int(
                row,
                [
                    "composite_score",
                    "total_score",
                    "human_total_score",
                    "domain1_score",
                ],
            ),
            None,
        )
        result[sample_id] = scores
    return result, dimension_ids


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
    sample_dir: Path,
) -> tuple[dict[str, int], int | None] | None:
    feedback = _load_json(sample_dir / "feedback.json")
    if feedback is None:
        return None

    trace = _load_json(sample_dir / "run_trace.json")
    if trace is not None and trace.get("status") not in (None, "completed"):
        return None

    dimensions = feedback.get("dimensions") or {}
    if not isinstance(dimensions, dict):
        return None

    dim_scores: dict[str, int] = {}
    for raw_dim_id, raw_dim_data in dimensions.items():
        dim_id = _as_non_empty_str(raw_dim_id)
        dim_data = raw_dim_data if isinstance(raw_dim_data, dict) else {}
        if dim_id is None:
            continue
        score = _as_int(dim_data.get("score"))
        if score is None:
            score = _as_int(dim_data.get("canonical_score"))
        if score is None:
            score = _as_int(dim_data.get("final_score"))
        if score is not None:
            dim_scores[dim_id] = score

    composite = feedback.get("indicator_score") or feedback.get("composite") or {}
    composite_score = _as_int(composite.get("score"))
    if composite_score is None:
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

    reference_scores, reference_dimension_ids = _load_reference_scores(Path(tsv_path))
    if not reference_scores:
        return _empty_probe("qwk_probe")

    per_sample: dict[str, dict[str, Any]] = {}
    dimension_ids = list(reference_dimension_ids)
    seen_dimension_ids = set(dimension_ids)
    y_true_by_dim: dict[str, list[int]] = {dim_id: [] for dim_id in dimension_ids}
    y_pred_by_dim: dict[str, list[int]] = {dim_id: [] for dim_id in dimension_ids}
    composite_true: list[int] = []
    composite_pred: list[int] = []

    for sample_id, sample_dir in _collect_sample_dirs(artifacts_dir):
        if sample_id not in reference_scores:
            continue
        loaded = _load_mas_scores_for_qwk(sample_dir)
        if loaded is None:
            continue
        mas_scores, mas_composite = loaded
        reference = reference_scores[sample_id]
        for dim_id in mas_scores:
            if dim_id not in seen_dimension_ids:
                seen_dimension_ids.add(dim_id)
                dimension_ids.append(dim_id)
                y_true_by_dim[dim_id] = []
                y_pred_by_dim[dim_id] = []
        for dim_id in reference:
            if dim_id == "_total" or dim_id in seen_dimension_ids:
                continue
            seen_dimension_ids.add(dim_id)
            dimension_ids.append(dim_id)
            y_true_by_dim[dim_id] = []
            y_pred_by_dim[dim_id] = []

        dim_pairs: dict[str, dict[str, int | None]] = {}
        for dim_id in dimension_ids:
            reference_pair = reference.get(dim_id, (None, None))
            y_true = _pick_human_score(reference_pair[0], reference_pair[1], rater)
            y_pred = mas_scores.get(dim_id)
            dim_pairs[dim_id] = {"reference": y_true, "mas": y_pred}
            if y_true is not None and y_pred is not None:
                y_true_by_dim[dim_id].append(y_true)
                y_pred_by_dim[dim_id].append(y_pred)

        reference_total, _ = reference.get("_total", (None, None))
        if reference_total is not None and mas_composite is not None:
            composite_true.append(reference_total)
            composite_pred.append(mas_composite)

        per_sample[sample_id] = {
            "dimensions": dim_pairs,
            "reference_composite": reference_total,
            "mas_composite": mas_composite,
        }

    if not per_sample:
        return _empty_probe("qwk_probe")

    metrics: dict[str, float | int | None] = {}
    for dim_id in dimension_ids:
        y_true = y_true_by_dim[dim_id]
        y_pred = y_pred_by_dim[dim_id]
        metrics[f"n_{dim_id}"] = len(y_true)
        if len(y_true) >= 2:
            score_range = _score_range(y_true + y_pred)
            if score_range is None:
                metrics[f"qwk_{dim_id}"] = None
            else:
                result = qwk_for_dimension(
                    dim_id,
                    y_true,
                    y_pred,
                    min_score=score_range[0],
                    max_score=score_range[1],
                )
                metrics[f"qwk_{dim_id}"] = result.qwk
        else:
            metrics[f"qwk_{dim_id}"] = None

    metrics["n_composite"] = len(composite_true)
    if len(composite_true) >= 2:
        composite_range = _score_range(composite_true + composite_pred)
        if composite_range is None:
            metrics["qwk_composite"] = None
        else:
            composite = qwk_for_dimension(
                "composite",
                composite_true,
                composite_pred,
                min_score=composite_range[0],
                max_score=composite_range[1],
            )
            metrics["qwk_composite"] = composite.qwk
    else:
        metrics["qwk_composite"] = None

    return ProbeResult(
        probe_name="qwk_probe",
        sample_count=len(per_sample),
        metrics=metrics,
        per_sample=per_sample,
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
    per_sample: dict[str, dict[str, Any]] = {}
    latency_values: list[float] = []
    retry_values: list[float] = []
    token_values: list[float] = []
    stage_latencies: dict[str, list[float]] = {}

    for sample_id, sample_dir in _collect_sample_dirs(artifacts_dir):
        trace = _load_json(sample_dir / "run_trace.json")
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

        per_sample[sample_id] = {
            "total_latency_seconds": total_latency,
            "retry_count": retry_count,
            "retry_rate": retry_rate,
            "total_tokens": total_tokens,
            "node_count": len(node_traces),
        }

    if not per_sample:
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
        sample_count=len(per_sample),
        metrics=metrics,
        per_sample=per_sample,
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
