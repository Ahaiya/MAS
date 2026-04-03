#!/usr/bin/env python3
"""Compute coverage diagnostics from evaluation artifacts.

Recommended unified entry:
  python -m scripts metrics coverage --eval-dir ...

Outputs three metrics per essay:
1) coverage_recall_rate
2) coverage_precision_rate
3) chunk_boundary_quality

Expected inputs per essay directory:
- evidence_spans.json
- observations.json

The script writes coverage_metrics.json into each essay directory by default.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import typer

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

app = typer.Typer(
    name="compute-coverage-metrics",
    help="Compute coverage recall/precision/boundary metrics from eval artifacts.",
)

_DEFAULT_EVAL_DIR = _PROJECT_ROOT / "artifacts" / "eval"


def _safe_div(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _as_non_empty_str(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
    return None


def compute_metrics_for_essay(essay_dir: Path) -> Dict[str, Any]:
    spans_path = essay_dir / "evidence_spans.json"
    observations_path = essay_dir / "observations.json"
    if not spans_path.exists():
        raise FileNotFoundError(f"Missing file: {spans_path}")
    if not observations_path.exists():
        raise FileNotFoundError(f"Missing file: {observations_path}")

    spans_data = _load_json(spans_path)
    obs_data = _load_json(observations_path)

    spans = list(spans_data.get("evidence_spans") or [])
    observations = list(obs_data.get("observations") or [])
    coverage_plans = list(obs_data.get("coverage_plans") or [])
    text_units = list(obs_data.get("text_units") or [])

    spans_by_dim: Dict[str, List[Dict[str, Any]]] = {}
    for span in spans:
        dim = _as_non_empty_str(span.get("dimension_id"))
        if dim is None:
            continue
        spans_by_dim.setdefault(dim, []).append(span)

    obs_by_dim: Dict[str, Dict[str, Any]] = {}
    for obs in observations:
        dim = _as_non_empty_str(obs.get("dimension_id"))
        if dim is None:
            continue
        obs_by_dim[dim] = obs

    plans_by_dim: Dict[str, Dict[str, Any]] = {}
    for plan in coverage_plans:
        dim = _as_non_empty_str(plan.get("dimension_id"))
        if dim is None:
            continue
        plans_by_dim[dim] = plan

    unit_text_by_id: Dict[str, str] = {}
    for unit in text_units:
        unit_id = _as_non_empty_str(unit.get("unit_id"))
        text = _as_non_empty_str(unit.get("text"))
        if unit_id is not None and text is not None:
            unit_text_by_id[unit_id] = text

    dimension_ids = sorted(set(spans_by_dim) | set(obs_by_dim) | set(plans_by_dim))

    per_dimension: Dict[str, Dict[str, Any]] = {}

    total_recall_in_target = 0
    total_recall_assessable = 0
    total_precision_used_target_units = 0
    total_precision_target_units = 0
    total_boundary_cross_chunk = 0
    total_boundary_unmatched = 0
    total_boundary_assessable = 0

    for dim_id in dimension_ids:
        dim_spans = spans_by_dim.get(dim_id, [])
        obs = obs_by_dim.get(dim_id, {})
        plan = plans_by_dim.get(dim_id, {})

        target_unit_ids = [
            uid for uid in (plan.get("target_unit_ids") or [])
            if _as_non_empty_str(uid) is not None
        ]
        target_unit_set = set(target_unit_ids)
        miss_span_ids = set(obs.get("coverage_miss_span_ids") or [])

        span_with_unit: List[Dict[str, Any]] = []
        spans_without_unit = 0
        for span in dim_spans:
            if _as_non_empty_str(span.get("unit_id")) is not None:
                span_with_unit.append(span)
            else:
                spans_without_unit += 1

        if target_unit_set:
            in_target_spans = 0
            out_target_spans = 0
            for span in span_with_unit:
                unit_id = _as_non_empty_str(span.get("unit_id"))
                if unit_id in target_unit_set:
                    in_target_spans += 1
                else:
                    out_target_spans += 1
        else:
            out_target_spans = sum(
                1 for span in span_with_unit
                if span.get("span_id") in miss_span_ids
            )
            in_target_spans = len(span_with_unit) - out_target_spans

        recall_assessable = len(span_with_unit)
        recall_rate = _safe_div(in_target_spans, recall_assessable)
        total_recall_in_target += in_target_spans
        total_recall_assessable += recall_assessable

        used_target_units = {
            _as_non_empty_str(span.get("unit_id"))
            for span in span_with_unit
            if _as_non_empty_str(span.get("unit_id")) in target_unit_set
        }
        used_target_units.discard(None)
        precision_rate = _safe_div(len(used_target_units), len(target_unit_set))
        if target_unit_set:
            total_precision_used_target_units += len(used_target_units)
            total_precision_target_units += len(target_unit_set)

        quote_spans = [
            span for span in dim_spans
            if _as_non_empty_str(span.get("text_quote")) is not None
        ]
        boundary_cross_chunk = 0
        boundary_unmatched = 0
        boundary_ok = 0
        for span in quote_spans:
            quote = _as_non_empty_str(span.get("text_quote")) or ""
            span_unit_id = _as_non_empty_str(span.get("unit_id"))
            containing_unit_ids = [
                uid for uid, unit_text in unit_text_by_id.items()
                if quote in unit_text
            ]
            if not containing_unit_ids:
                boundary_unmatched += 1
            elif span_unit_id in containing_unit_ids:
                boundary_ok += 1
            else:
                boundary_cross_chunk += 1

        boundary_assessable = len(quote_spans)
        cross_chunk_ratio = _safe_div(boundary_cross_chunk, boundary_assessable)
        boundary_failure_ratio = _safe_div(
            boundary_cross_chunk + boundary_unmatched, boundary_assessable
        )
        total_boundary_cross_chunk += boundary_cross_chunk
        total_boundary_unmatched += boundary_unmatched
        total_boundary_assessable += boundary_assessable

        per_dimension[dim_id] = {
            "coverage_recall_rate": {
                "rate": recall_rate,
                "in_target_spans": in_target_spans,
                "out_target_spans": out_target_spans,
                "assessable_spans": recall_assessable,
                "unknown_unit_spans": spans_without_unit,
            },
            "coverage_precision_rate": {
                "rate": precision_rate,
                "used_target_units": len(used_target_units),
                "target_unit_count": len(target_unit_set),
            },
            "chunk_boundary_quality": {
                "cross_chunk_span_ratio": cross_chunk_ratio,
                "boundary_failure_ratio": boundary_failure_ratio,
                "cross_chunk_spans": boundary_cross_chunk,
                "unmatched_quote_spans": boundary_unmatched,
                "boundary_ok_spans": boundary_ok,
                "assessable_quote_spans": boundary_assessable,
            },
        }

    overall = {
        "coverage_recall_rate": {
            "rate": _safe_div(total_recall_in_target, total_recall_assessable),
            "in_target_spans": total_recall_in_target,
            "assessable_spans": total_recall_assessable,
        },
        "coverage_precision_rate": {
            "rate": _safe_div(
                total_precision_used_target_units, total_precision_target_units
            ),
            "used_target_units": total_precision_used_target_units,
            "target_unit_count": total_precision_target_units,
        },
        "chunk_boundary_quality": {
            "cross_chunk_span_ratio": _safe_div(
                total_boundary_cross_chunk, total_boundary_assessable
            ),
            "boundary_failure_ratio": _safe_div(
                total_boundary_cross_chunk + total_boundary_unmatched,
                total_boundary_assessable,
            ),
            "assessable_quote_spans": total_boundary_assessable,
        },
    }

    return {
        "essay_id": essay_dir.name,
        "run_id": spans_data.get("run_id") or obs_data.get("run_id"),
        "dimension_count": len(dimension_ids),
        "per_dimension": per_dimension,
        "overall": overall,
    }


@app.command()
def main(
    eval_dir: Path = typer.Option(
        _DEFAULT_EVAL_DIR,
        "--eval-dir", "-e",
        help="Directory containing per-essay artifact subdirectories.",
    ),
    essay_id: str = typer.Option(
        "",
        "--essay-id",
        help="Compute for one essay_id only. Empty means compute all subdirectories.",
    ),
    write: bool = typer.Option(
        True,
        "--write/--no-write",
        help="Write coverage_metrics.json under each essay directory.",
    ),
) -> None:
    if not eval_dir.exists():
        typer.echo(f"错误：目录不存在 {eval_dir}", err=True)
        raise typer.Exit(code=1)

    if essay_id:
        essay_dirs = [eval_dir / essay_id]
    else:
        essay_dirs = sorted(p for p in eval_dir.iterdir() if p.is_dir())

    if not essay_dirs:
        typer.echo("错误：未找到可计算的样本目录", err=True)
        raise typer.Exit(code=1)

    ok_count = 0
    for essay_dir in essay_dirs:
        if not essay_dir.exists():
            typer.echo(f"[skip] {essay_dir.name}: 目录不存在", err=True)
            continue
        try:
            metrics = compute_metrics_for_essay(essay_dir)
            if write:
                out_path = essay_dir / "coverage_metrics.json"
                out_path.write_text(
                    json.dumps(metrics, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            overall = metrics["overall"]
            recall = overall["coverage_recall_rate"]["rate"]
            precision = overall["coverage_precision_rate"]["rate"]
            boundary = overall["chunk_boundary_quality"]["cross_chunk_span_ratio"]
            typer.echo(
                f"[ok] {essay_dir.name}  "
                f"recall={recall if recall is not None else 'N/A'}  "
                f"precision={precision if precision is not None else 'N/A'}  "
                f"cross_chunk={boundary if boundary is not None else 'N/A'}"
            )
            ok_count += 1
        except Exception as exc:
            typer.echo(f"[warn] {essay_dir.name}: {exc}", err=True)

    if ok_count == 0:
        typer.echo("错误：没有成功计算任何样本", err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
