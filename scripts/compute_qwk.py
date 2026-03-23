#!/usr/bin/env python3
"""Compute QWK and inter-agent consistency for batch MAS evaluation results.

Reads MAS scores from artifacts/eval/*/feedback.json, pairs them with human
scores from the TSV, then computes two sets of metrics:

  1. MAS vs human QWK  — how well MAS agrees with a human rater (per dimension)
  2. Agent vs agent QWK — how well rater_1 and rater_2 agree with each other
                          (requires hypotheses.json produced by run_batch_eval.py
                          or evaluate_essay.py)

Usage:
  # Default: compare MAS against rater1
  python scripts/compute_qwk.py

  # Use average of rater1+rater2 as ground truth; write JSON report
  python scripts/compute_qwk.py --rater average --output results/qwk_report.json

  # Only look at a subdirectory
  python scripts/compute_qwk.py --eval-dir artifacts/eval
"""

import csv
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import typer

from src.evaluation.consistency import compute_consistency
from src.evaluation.export import export_run
from src.evaluation.qwk import qwk_for_dimension

app = typer.Typer(
    name="compute-qwk",
    help="Compute QWK and inter-agent consistency from batch evaluation results.",
)

_DEFAULT_EVAL_DIR = _PROJECT_ROOT / "artifacts" / "eval"
_DEFAULT_SOURCE = _PROJECT_ROOT / "data" / "training_set_8.tsv"

# Fixed dimension order (matches rubric definition order)
_DIM_ORDER = [
    "ideas_content",
    "organization",
    "voice",
    "word_choice",
    "sentence_fluency",
    "conventions",
]
_DIM_LABELS = {
    "ideas_content":    "Ideas & Content",
    "organization":     "Organization",
    "voice":            "Voice",
    "word_choice":      "Word Choice",
    "sentence_fluency": "Sentence Fluency",
    "conventions":      "Conventions",
}


# ── Data loaders ──────────────────────────────────────────────────────────────

def _load_human_scores(
    tsv_path: Path,
) -> dict[str, dict[str, tuple[int | None, int | None]]]:
    """Return {essay_id: {dimension_id: (rater1_score, rater2_score)}}.

    Trait column mapping (ASAP Set 8):
      rater{1,2}_trait1 → ideas_content
      rater{1,2}_trait2 → organization
      rater{1,2}_trait3 → voice
      rater{1,2}_trait4 → word_choice
      rater{1,2}_trait5 → sentence_fluency
      rater{1,2}_trait6 → conventions
    """
    result: dict[str, dict[str, tuple[int | None, int | None]]] = {}
    with open(tsv_path, encoding="latin-1") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            eid = row.get("essay_id", "").strip()
            if not eid:
                continue
            scores: dict[str, tuple[int | None, int | None]] = {}
            for idx, dim_id in enumerate(_DIM_ORDER, 1):
                v1 = row.get(f"rater1_trait{idx}", "").strip()
                v2 = row.get(f"rater2_trait{idx}", "").strip()
                r1: int | None = int(v1) if v1.isdigit() else None
                r2: int | None = int(v2) if v2.isdigit() else None
                scores[dim_id] = (r1, r2)
            result[eid] = scores
    return result


def _load_mas_scores(
    eval_dir: Path,
) -> dict[str, dict[str, int]]:
    """Return {essay_id: {dimension_id: mas_score}} from feedback.json files.

    Uses src/evaluation/export.py (export_run) which handles both the real
    provider format (canonical_score) and mock format (final_score).
    """
    result: dict[str, dict[str, int]] = {}
    for essay_dir in sorted(eval_dir.iterdir()):
        if not essay_dir.is_dir():
            continue
        if not (essay_dir / "feedback.json").exists():
            continue
        if not (essay_dir / "run_trace.json").exists():
            continue
        try:
            run_export = export_run(essay_dir, essay_id=essay_dir.name)
            if run_export.status != "completed":
                continue
            result[essay_dir.name] = run_export.scores_by_dimension()
        except Exception as exc:
            typer.echo(f"  [warn] 跳过 {essay_dir.name}: {exc}", err=True)
    return result


def _load_agent_hypotheses(
    eval_dir: Path,
) -> dict[str, dict[str, list[int]]]:
    """Return {essay_id: {dimension_id: [rater_1_score, rater_2_score, ...]}}
    from hypotheses.json files.

    Only includes essays where hypotheses.json was produced by
    run_batch_eval.py or evaluate_essay.py.
    """
    result: dict[str, dict[str, list[int]]] = {}
    for essay_dir in sorted(eval_dir.iterdir()):
        if not essay_dir.is_dir():
            continue
        hyps_path = essay_dir / "hypotheses.json"
        if not hyps_path.exists():
            continue
        try:
            data = json.loads(hyps_path.read_text(encoding="utf-8"))
            # Group scores by dimension, ordered by rater_id
            by_dim: dict[str, dict[str, int]] = {}
            for h in data.get("hypotheses", []):
                dim_id = h["dimension_id"]
                rater_id = h["rater_id"]
                score = h["score"]["canonical_score"]
                if dim_id not in by_dim:
                    by_dim[dim_id] = {}
                by_dim[dim_id][rater_id] = score

            # Convert to ordered lists: [rater_1, rater_2, ...]
            dim_scores: dict[str, list[int]] = {}
            for dim_id, rater_map in by_dim.items():
                ordered = [
                    rater_map[rid]
                    for rid in sorted(rater_map)  # rater_1 < rater_2 < rater_3
                    if rid in rater_map
                ]
                if ordered:
                    dim_scores[dim_id] = ordered
            if dim_scores:
                result[essay_dir.name] = dim_scores
        except Exception as exc:
            typer.echo(f"  [warn] hypotheses {essay_dir.name}: {exc}", err=True)
    return result


# ── y_true selection ──────────────────────────────────────────────────────────

def _pick_y_true(r1: int | None, r2: int | None, rater: str) -> int | None:
    """Return the human ground-truth score according to --rater strategy."""
    if rater == "rater1":
        return r1
    if rater == "rater2":
        return r2
    # average: round to nearest integer; require both raters
    if r1 is not None and r2 is not None:
        return round((r1 + r2) / 2)
    return r1 if r1 is not None else r2


# ── Report printing ───────────────────────────────────────────────────────────

def _fmt(val: float | None, decimals: int = 4) -> str:
    if val is None:
        return "  N/A "
    return f"{val:.{decimals}f}"


# ── Main ──────────────────────────────────────────────────────────────────────

@app.command()
def main(
    eval_dir: Path = typer.Option(
        _DEFAULT_EVAL_DIR,
        "--eval-dir", "-e",
        help="Root directory containing per-essay artifact subdirectories.",
    ),
    source: Path = typer.Option(
        _DEFAULT_SOURCE,
        "--source", "-s",
        help="Path to ASAP training TSV file (human scores).",
    ),
    rater: str = typer.Option(
        "rater1",
        "--rater", "-r",
        help="Human ground-truth source: rater1 | rater2 | average",
    ),
    output: Path = typer.Option(
        None,
        "--output", "-o",
        help="Optional path to write full JSON report.",
    ),
) -> None:
    """Compute per-dimension QWK (MAS vs human) and inter-agent QWK (rater_1 vs rater_2)."""

    if rater not in ("rater1", "rater2", "average"):
        typer.echo("错误：--rater 必须是 rater1 / rater2 / average", err=True)
        raise typer.Exit(code=1)

    # ── Load data ─────────────────────────────────────────────────────────────
    typer.echo(f"[1/4] 加载 MAS 评分结果: {eval_dir}")
    mas_scores = _load_mas_scores(eval_dir)
    typer.echo(f"      找到 {len(mas_scores)} 篇有效 MAS 结果（status=completed）")

    typer.echo(f"[2/4] 加载人工评分: {source}")
    human_scores = _load_human_scores(source)
    typer.echo(f"      TSV 共 {len(human_scores)} 条记录")

    typer.echo(f"[3/4] 加载评审员假设分数: {eval_dir}/*/hypotheses.json")
    agent_hyps = _load_agent_hypotheses(eval_dir)
    typer.echo(f"      找到 {len(agent_hyps)} 篇 hypotheses.json")

    common_ids = sorted(set(mas_scores) & set(human_scores))
    typer.echo(f"      MAS ∩ 人工评分 可对比样本：{len(common_ids)} 篇")

    if not common_ids:
        typer.echo("错误：没有可对比的样本，请先运行 run_batch_eval.py", err=True)
        raise typer.Exit(code=1)

    # ── Section 1: MAS vs human QWK ──────────────────────────────────────────
    typer.echo(f"[4/4] 计算 QWK ...")
    qwk_rows: list[dict] = []

    for dim_id in _DIM_ORDER:
        y_true: list[int] = []
        y_pred: list[int] = []

        for eid in common_ids:
            mas = mas_scores[eid].get(dim_id)
            if mas is None:
                continue
            r1, r2 = human_scores[eid].get(dim_id, (None, None))
            h = _pick_y_true(r1, r2, rater)
            if h is None:
                continue
            y_true.append(h)
            y_pred.append(mas)

        if len(y_true) < 2:
            qwk_rows.append({
                "dimension_id": dim_id,
                "label": _DIM_LABELS[dim_id],
                "n": len(y_true),
                "qwk": None,
                "human_mean": None,
                "mas_mean": None,
                "mean_diff": None,
            })
            continue

        res = qwk_for_dimension(dim_id, y_true, y_pred, min_score=1, max_score=6)
        human_mean = sum(y_true) / len(y_true)
        mas_mean = sum(y_pred) / len(y_pred)
        qwk_rows.append({
            "dimension_id": dim_id,
            "label": _DIM_LABELS[dim_id],
            "n": len(y_true),
            "qwk": round(res.qwk, 4),
            "human_mean": round(human_mean, 2),
            "mas_mean": round(mas_mean, 2),
            "mean_diff": round(mas_mean - human_mean, 2),
        })

    valid_qwks = [r["qwk"] for r in qwk_rows if r["qwk"] is not None]
    macro_qwk = round(sum(valid_qwks) / len(valid_qwks), 4) if valid_qwks else None

    # ── Section 2: Agent vs agent QWK + consistency ───────────────────────────
    agent_qwk_rows: list[dict] = []
    agent_ids_with_hyps = sorted(agent_hyps)

    for dim_id in _DIM_ORDER:
        r1_list: list[int] = []
        r2_list: list[int] = []
        for eid in agent_ids_with_hyps:
            scores_for_dim = agent_hyps[eid].get(dim_id, [])
            if len(scores_for_dim) >= 2:
                r1_list.append(scores_for_dim[0])
                r2_list.append(scores_for_dim[1])

        if len(r1_list) < 2:
            agent_qwk_rows.append({
                "dimension_id": dim_id,
                "label": _DIM_LABELS[dim_id],
                "n_pairs": len(r1_list),
                "agent_qwk": None,
                "spread_mean": None,
                "agreement_ratio": None,
            })
            continue

        res = qwk_for_dimension(
            f"{dim_id}_a2a", r1_list, r2_list, min_score=1, max_score=6
        )
        # Per-essay spread (|rater1 - rater2|) average
        spreads = [abs(a - b) for a, b in zip(r1_list, r2_list)]
        agreement_ratio = sum(1 for s in spreads if s <= 1) / len(spreads)
        agent_qwk_rows.append({
            "dimension_id": dim_id,
            "label": _DIM_LABELS[dim_id],
            "n_pairs": len(r1_list),
            "agent_qwk": round(res.qwk, 4),
            "spread_mean": round(sum(spreads) / len(spreads), 2),
            "agreement_ratio": round(agreement_ratio, 4),
        })

    valid_aa_qwks = [r["agent_qwk"] for r in agent_qwk_rows if r["agent_qwk"] is not None]
    macro_aa_qwk = round(sum(valid_aa_qwks) / len(valid_aa_qwks), 4) if valid_aa_qwks else None

    # ── Section 3: Per-essay inter-agent consistency (ConsistencyReport) ──────
    consistency_summaries: list[dict] = []
    for eid in sorted(agent_hyps):
        report = compute_consistency(eid, agent_hyps[eid])
        consistency_summaries.append({
            "essay_id": eid,
            "total_conflict_count": report.total_conflict_count,
            "dimensions_with_conflict": report.dimensions_with_conflict,
            "overall_agreement_ratio": round(report.overall_agreement_ratio, 4),
        })

    n_clean = sum(1 for c in consistency_summaries if c["total_conflict_count"] == 0)
    n_conflict = len(consistency_summaries) - n_clean

    # ── Print Section 1: MAS vs human ────────────────────────────────────────
    typer.echo("")
    typer.echo("=" * 72)
    typer.echo(
        f"  MAS vs 人工评分 QWK"
        f"  (y_true={rater}, n={len(common_ids)} 篇)"
    )
    typer.echo("=" * 72)
    typer.echo(
        f"  {'维度':<20}  {'N':>4}  {'QWK':>7}  {'人类均值':>6}  {'MAS均值':>6}  {'偏差':>5}"
    )
    typer.echo("  " + "-" * 60)
    for r in qwk_rows:
        typer.echo(
            f"  {r['label']:<20}  {r['n']:>4}  "
            f"{_fmt(r['qwk']):>7}  "
            f"{_fmt(r['human_mean'], 2):>6}  "
            f"{_fmt(r['mas_mean'], 2):>6}  "
            f"{('+' if (r['mean_diff'] or 0) >= 0 else '') + _fmt(r['mean_diff'], 2):>5}"
        )
    typer.echo("  " + "-" * 60)
    typer.echo(f"  {'Macro-avg QWK':<20}         {_fmt(macro_qwk):>7}")

    # ── Print Section 2: Agent vs agent ──────────────────────────────────────
    typer.echo("")
    typer.echo("=" * 72)
    typer.echo(
        f"  AI 评审员间 QWK  (rater_1 vs rater_2, "
        f"n={len(agent_ids_with_hyps)} 篇)"
    )
    typer.echo("=" * 72)
    typer.echo(
        f"  {'维度':<20}  {'N对':>4}  {'A2A-QWK':>8}  {'平均分差':>6}  {'邻近率':>6}"
    )
    typer.echo("  " + "-" * 56)
    for r in agent_qwk_rows:
        typer.echo(
            f"  {r['label']:<20}  {r['n_pairs']:>4}  "
            f"{_fmt(r['agent_qwk']):>8}  "
            f"{_fmt(r['spread_mean'], 2):>6}  "
            f"{_fmt(r['agreement_ratio'], 4):>6}"
        )
    typer.echo("  " + "-" * 56)
    typer.echo(f"  {'Macro-avg A2A-QWK':<20}         {_fmt(macro_aa_qwk):>8}")

    # ── Print Section 3: Consistency summary ─────────────────────────────────
    if consistency_summaries:
        typer.echo("")
        typer.echo("=" * 72)
        typer.echo(f"  评审员间一致性摘要  ({len(consistency_summaries)} 篇)")
        typer.echo("=" * 72)
        typer.echo(f"  无分歧（全部维度相邻）: {n_clean} 篇")
        typer.echo(f"  存在非相邻分歧:         {n_conflict} 篇")
        if consistency_summaries:
            avg_agree = sum(c["overall_agreement_ratio"] for c in consistency_summaries) / len(
                consistency_summaries
            )
            typer.echo(f"  整体平均邻近一致率:     {avg_agree:.4f}")

    typer.echo("")

    # ── Write JSON report ─────────────────────────────────────────────────────
    report_data = {
        "n_essays_compared": len(common_ids),
        "rater_baseline": rater,
        "mas_vs_human": {
            "dimensions": qwk_rows,
            "macro_qwk": macro_qwk,
        },
        "agent_vs_agent": {
            "n_essays_with_hypotheses": len(agent_ids_with_hyps),
            "dimensions": agent_qwk_rows,
            "macro_qwk": macro_aa_qwk,
        },
        "consistency_summary": {
            "n_essays": len(consistency_summaries),
            "n_no_conflict": n_clean,
            "n_with_conflict": n_conflict,
            "per_essay": consistency_summaries,
        },
    }

    if output:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        typer.echo(f"报告已写入: {output}")


if __name__ == "__main__":
    app()
