#!/usr/bin/env python3
"""Evaluate a single essay end-to-end.

Steps performed automatically:
  1. Extract essay text from TSV file by essay_id
  2. Run the full MAS pipeline with real LLM
  3. Validate the run directory (schema + trace closure)
  4. Print a formatted scoring report to the terminal
  5. Optionally compare against human scores in the TSV

Usage:
  python scripts/evaluate_essay.py \\
    --essay-id 20722 \\
    --source data/training_set_8.tsv \\
    --bundle configs/bundles/asap_set8_baseline.bundle.yaml \\
    --output-dir artifacts/eval_20722
"""

import csv
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import typer

from src.agents.mock_config_resolver import run as resolve_bundle
from src.contracts.request_models import EvaluationRequest
from src.pipeline.runner import PipelineRunner
from src.providers.factory import build_provider, build_provider_map
from src.contracts.artifact_bundle import ProviderEntryConfig
from src.providers.prompt_loader import PromptLoader

app = typer.Typer(name="evaluate-essay", help="Extract, evaluate, validate, and report a single essay.")

# Trait column names in ASAP Set 8 TSV (rater1_trait1 … rater2_trait6)
_TRAIT_KEYS = [f"rater{r}_trait{t}" for r in (1, 2) for t in range(1, 7)]

# Mapping from feedback.json dimension keys → display info
_DIM_ORDER = [
    ("ideas_content",    "Ideas & Content",    "💡"),
    ("organization",     "Organization",       "📐"),
    ("voice",            "Voice",              "🎤"),
    ("word_choice",      "Word Choice",        "📝"),
    ("sentence_fluency", "Sentence Fluency",   "🔄"),
    ("conventions",      "Conventions",        "✏️"),
]

# ── helpers ────────────────────────────────────────────────────────────────────

def _extract_essay(tsv_path: Path, essay_id: int) -> tuple[str, dict]:
    """Return (essay_text, row_dict) for the given essay_id."""
    with open(tsv_path, encoding="latin-1") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            if row.get("essay_id", "").strip() == str(essay_id):
                return row["essay"], row
    raise ValueError(f"Essay ID {essay_id} not found in {tsv_path}")


def _validate_trace(trace: dict) -> list[str]:
    """Return list of validation failure messages (empty = pass)."""
    failures = []
    required = {
        "run_id", "bundle_id", "status", "started_at",
        "finished_at", "node_traces", "terminal_validation_passed",
    }
    missing = required - set(trace.keys())
    if missing:
        failures.append(f"run_trace missing fields: {sorted(missing)}")
    if trace.get("status") != "completed":
        failures.append(f"status = '{trace.get('status')}', expected 'completed'")
    if not trace.get("terminal_validation_passed"):
        failures.append("terminal_validation_passed is not True")
    for node in trace.get("node_traces", []):
        nid = node.get("node_id", "?")
        if node.get("status") != "success":
            failures.append(f"node '{nid}' status = '{node.get('status')}'")
        if not node.get("input_ref"):
            failures.append(f"node '{nid}' has empty input_ref")
        if not node.get("output_ref"):
            failures.append(f"node '{nid}' has empty output_ref")
    if "dimensions" not in (trace.get("feedback") or {}):
        pass  # feedback validated separately
    return failures


def _validate_feedback(feedback: dict) -> list[str]:
    failures = []
    if not isinstance(feedback.get("dimensions"), dict):
        failures.append("feedback.json missing 'dimensions' object")
        return failures
    for dim_id, dim in feedback["dimensions"].items():
        if "final_score" not in dim and "canonical_score" not in dim:
            failures.append(f"dimension '{dim_id}' missing score field")
    return failures


def _score(dim: dict) -> int:
    return dim.get("canonical_score") or dim.get("final_score") or 0


def _duration(started: str, finished: str) -> str:
    from datetime import datetime, timezone
    fmt = "%Y-%m-%dT%H:%M:%S.%f%z"
    try:
        s = datetime.fromisoformat(started)
        f = datetime.fromisoformat(finished)
        secs = int((f - s).total_seconds())
        if secs < 60:
            return f"{secs}秒"
        return f"{secs // 60}分{secs % 60}秒"
    except Exception:
        return "N/A"


def _bar(score: int, max_score: int = 6) -> str:
    filled = round(score / max_score * 10)
    return "█" * filled + "░" * (10 - filled)


def _grade(score: int) -> str:
    return {6: "优秀", 5: "良好+", 4: "良好", 3: "中等", 2: "需改进", 1: "薄弱"}.get(score, "")


def _get_human_scores(tsv_row: dict | None) -> tuple[dict, dict]:
    """Parse rater1/rater2 trait scores from a TSV row. Returns (r1, r2) dicts keyed by dim key."""
    r1, r2 = {}, {}
    if not tsv_row:
        return r1, r2
    for i, (key, _, _) in enumerate(_DIM_ORDER):
        v1 = tsv_row.get(f"rater1_trait{i+1}", "")
        v2 = tsv_row.get(f"rater2_trait{i+1}", "")
        if v1.strip().isdigit():
            r1[key] = int(v1)
        if v2.strip().isdigit():
            r2[key] = int(v2)
    return r1, r2


def _build_report_md(essay_id: int, trace: dict, feedback: dict, tsv_row: dict | None) -> str:
    """Build a Markdown report string suitable for saving to report.md."""
    dims = feedback.get("dimensions", {})
    total_mas = sum(_score(dims[k]) for k, _, _ in _DIM_ORDER if k in dims)
    started = trace.get("started_at", "")
    finished = trace.get("finished_at", "")
    human_r1, human_r2 = _get_human_scores(tsv_row)
    has_human = bool(human_r1)

    node_labels = {
        "node_preprocess":          "文档预处理",
        "node_coverage":            "维度覆盖确认",
        "node_extractor":           "证据抽取",
        "node_observer":            "证据整理",
        "node_scorer":              "双评审打分",
        "node_consistency_checker": "一致性检验",
        "node_adjudicator":         "裁决",
        "node_feedback":            "反馈生成",
    }

    lines: list[str] = []

    # ── header ──────────────────────────────────────────────────────────────
    lines += [
        f"# 文章评价报告 — 样本 {essay_id}",
        "",
        f"| 项目 | 内容 |",
        f"|------|------|",
        f"| 评价时间 | {started[:19].replace('T', ' ')} |",
        f"| 耗时 | {_duration(started, finished)} |",
        f"| 运行 ID | `{trace.get('run_id', '')}` |",
        f"| 量规版本 | `{trace.get('bundle_id', '')}@{trace.get('bundle_version', '')}` |",
        "",
    ]

    # ── execution timeline ───────────────────────────────────────────────────
    lines += ["## 执行过程", "", "| 步骤 | 状态 | 耗时 | 输出 |", "|------|------|------|------|"]
    for node in trace.get("node_traces", []):
        nid = node["node_id"]
        if nid.startswith("__"):
            continue
        label = node_labels.get(nid, nid)
        icon = "✅" if node["status"] == "success" else "❌"
        dur = _duration(node.get("started_at", ""), node.get("finished_at", ""))
        out = node.get("output_ref") or ""
        lines.append(f"| {icon} {label} | {node['status']} | {dur} | `{out}` |")

    checker = next((n for n in trace["node_traces"] if n["node_id"] == "node_consistency_checker"), None)
    if checker:
        ref = checker.get("output_ref", "conflicts:0")
        n_conflicts = int(ref.split(":")[1]) if ":" in ref else 0
        verdict = "两位评审员完全一致，无需裁决" if n_conflicts == 0 else f"发现 {n_conflicts} 个分歧，已触发裁决"
        lines += ["", f"> **一致性结论：** {verdict}", ""]

    # ── score table ──────────────────────────────────────────────────────────
    lines += ["## 综合得分", ""]

    total_h1 = total_h2 = 0
    if has_human:
        lines.append("| 维度 | MAS | 等级 | 人类评审1 | 人类评审2 | 偏差 |")
        lines.append("|------|:---:|:---:|:---:|:---:|:---:|")
    else:
        lines.append("| 维度 | MAS | 等级 |")
        lines.append("|------|:---:|:---:|")

    for key, name, icon in _DIM_ORDER:
        if key not in dims:
            continue
        mas = _score(dims[key])
        grade = _grade(mas)
        label = f"{icon} {name}"
        if has_human:
            h1 = human_r1.get(key, "-")
            h2 = human_r2.get(key, "-")
            avg = (h1 + h2) / 2 if isinstance(h1, int) and isinstance(h2, int) else None
            diff = f"{mas - avg:+.1f}" if avg is not None else "N/A"
            if isinstance(h1, int):
                total_h1 += h1
            if isinstance(h2, int):
                total_h2 += h2
            lines.append(f"| {label} | **{mas}** | {grade} | {h1} | {h2} | {diff} |")
        else:
            lines.append(f"| {label} | **{mas}** | {grade} |")

    if has_human:
        total_avg = (total_h1 + total_h2) / 2
        diff_total = f"{total_mas - total_avg:+.1f}"
        lines.append(f"| **合计** | **{total_mas}** | | {total_h1} | {total_h2} | {diff_total} |")
        lines += [
            "",
            f"**满分：36 分** &nbsp;|&nbsp; "
            f"MAS：{total_mas} 分（{total_mas/36*100:.0f}%）&nbsp;|&nbsp; "
            f"人类均值：{total_avg:.1f} 分（{total_avg/36*100:.0f}%）",
            "",
        ]
    else:
        lines.append(f"| **合计** | **{total_mas}** | 满分 36 分（{total_mas/36*100:.0f}%） |")
        lines.append("")

    # ── dimension feedback ───────────────────────────────────────────────────
    lines.append("## 各维度详细反馈")
    for key, name, icon in _DIM_ORDER:
        if key not in dims:
            continue
        dim = dims[key]
        mas = _score(dim)
        lines += ["", f"### {icon} {name} — {mas}/6　{_grade(mas)}", ""]
        for desc in (dim.get("descriptor_refs") or [])[:3]:
            lines.append(f"- {desc}")
        text = dim.get("feedback_text", "")
        if text:
            lines += ["", text, ""]

    lines += ["---", f"*由 MAS 自动评价系统生成 | 运行 ID: `{trace.get('run_id', '')}`*", ""]
    return "\n".join(lines)


def _print_report(essay_id: int, trace: dict, feedback: dict, tsv_row: dict | None) -> None:
    dims = feedback.get("dimensions", {})
    total_mas = sum(_score(dims[k]) for k, _, _ in _DIM_ORDER if k in dims)

    # ── header ──────────────────────────────────────────────────────────────
    typer.echo("")
    typer.echo("=" * 64)
    typer.echo(f"  文章评价报告  —  样本 {essay_id}")
    typer.echo("=" * 64)
    started = trace.get("started_at", "")
    finished = trace.get("finished_at", "")
    typer.echo(f"  评价时间：{started[:19].replace('T', ' ')}  |  耗时：{_duration(started, finished)}")
    typer.echo(f"  运行 ID ：{trace.get('run_id', '')}")
    typer.echo(f"  量规版本：{trace.get('bundle_id', '')}@{trace.get('bundle_version', '')}")
    typer.echo("")

    # ── node timeline ────────────────────────────────────────────────────────
    node_labels = {
        "node_preprocess":         "文档预处理",
        "node_coverage":           "维度覆盖确认",
        "node_extractor":          "证据抽取",
        "node_observer":           "证据整理",
        "node_scorer":             "双评审打分",
        "node_consistency_checker":"一致性检验",
        "node_adjudicator":        "裁决",
        "node_feedback":           "反馈生成",
    }
    typer.echo("  执行过程")
    typer.echo("  " + "-" * 60)
    for node in trace.get("node_traces", []):
        nid = node["node_id"]
        if nid.startswith("__"):
            continue
        dur = _duration(node.get("started_at", ""), node.get("finished_at", ""))
        label = node_labels.get(nid, nid)
        status_icon = "✅" if node["status"] == "success" else "❌"
        out = node.get("output_ref") or ""
        typer.echo(f"  {status_icon} {label:<12}  {dur:>8}   → {out}")

    # conflicts
    checker = next((n for n in trace["node_traces"] if n["node_id"] == "node_consistency_checker"), None)
    adjudicator = next((n for n in trace["node_traces"] if n["node_id"] == "node_adjudicator"), None)
    if checker:
        ref = checker.get("output_ref", "conflicts:0")
        n_conflicts = int(ref.split(":")[1]) if ":" in ref else 0
        if n_conflicts == 0:
            typer.echo(f"  ✅ 两位评审员完全一致，无需裁决")
        else:
            typer.echo(f"  ⚠️  发现 {n_conflicts} 个分歧，已触发裁决")

    # ── score table ──────────────────────────────────────────────────────────
    typer.echo("")
    typer.echo("  综合得分")
    typer.echo("  " + "-" * 60)

    # human scores if available
    human_r1, human_r2 = _get_human_scores(tsv_row)
    has_human = bool(human_r1)

    if has_human:
        typer.echo(f"  {'维度':<18} {'MAS':>4}  {'进度条':<12}  {'等级':<6}  {'人类R1':>6}  {'人类R2':>6}  {'偏差':>4}")
    else:
        typer.echo(f"  {'维度':<18} {'MAS':>4}  {'进度条':<12}  {'等级':<6}")
    typer.echo("  " + "-" * 60)

    total_h1 = total_h2 = 0
    for key, name, icon in _DIM_ORDER:
        if key not in dims:
            continue
        mas = _score(dims[key])
        bar = _bar(mas)
        grade = _grade(mas)
        label = f"{icon} {name}"
        if has_human:
            h1 = human_r1.get(key, "-")
            h2 = human_r2.get(key, "-")
            avg = (h1 + h2) / 2 if isinstance(h1, int) and isinstance(h2, int) else None
            diff = f"{mas - avg:+.1f}" if avg is not None else " N/A"
            if isinstance(h1, int):
                total_h1 += h1
            if isinstance(h2, int):
                total_h2 += h2
            typer.echo(f"  {label:<18} {mas:>4}  {bar:<12}  {grade:<6}  {str(h1):>6}  {str(h2):>6}  {diff:>4}")
        else:
            typer.echo(f"  {label:<18} {mas:>4}  {bar:<12}  {grade:<6}")

    typer.echo("  " + "-" * 60)
    if has_human:
        total_avg = (total_h1 + total_h2) / 2
        diff_total = f"{total_mas - total_avg:+.1f}"
        typer.echo(f"  {'合计':<18} {total_mas:>4}  {'':12}  {'':6}  {total_h1:>6}  {total_h2:>6}  {diff_total:>4}")
        typer.echo(f"  满分: 36  |  MAS: {total_mas} ({total_mas/36*100:.0f}%)  |  人类均值: {total_avg:.1f} ({total_avg/36*100:.0f}%)")
    else:
        typer.echo(f"  {'合计':<18} {total_mas:>4}  满分 36 分（{total_mas/36*100:.0f}%）")

    # ── dimension feedback ────────────────────────────────────────────────────
    typer.echo("")
    typer.echo("  各维度反馈")
    typer.echo("  " + "-" * 60)
    for key, name, icon in _DIM_ORDER:
        if key not in dims:
            continue
        dim = dims[key]
        mas = _score(dim)
        typer.echo(f"\n  {icon} {name}  —  {mas}/6  {_grade(mas)}")
        # descriptors
        for desc in (dim.get("descriptor_refs") or [])[:3]:
            typer.echo(f"     • {desc}")
        # feedback text
        text = dim.get("feedback_text", "")
        if text:
            # word-wrap at ~60 chars
            import textwrap
            for line in textwrap.wrap(text, width=62):
                typer.echo(f"    {line}")

    typer.echo("")
    typer.echo("=" * 64)


# ── main ───────────────────────────────────────────────────────────────────────

_DEFAULT_SOURCE = _PROJECT_ROOT / "data" / "training_set_8.tsv"
_DEFAULT_BUNDLE = _PROJECT_ROOT / "configs" / "bundles" / "asap_set8_baseline.bundle.yaml"
_DEFAULT_OUTPUT_BASE = _PROJECT_ROOT / "artifacts" / "eval"


@app.command()
def main(
    essay_id: int = typer.Option(
        ..., "--essay-id", "-e",
        help="Essay ID to extract from the TSV file.",
    ),
    source: Path = typer.Option(
        _DEFAULT_SOURCE, "--source", "-s",
        help="Path to training TSV file.",
    ),
    bundle: Path = typer.Option(
        _DEFAULT_BUNDLE, "--bundle", "-b",
        help="Path to configuration bundle YAML file.",
    ),
    output_dir: Path = typer.Option(
        None, "--output-dir", "-o",
        help="Directory to write run artifacts. Defaults to artifacts/eval_{essay_id}.",
    ),
    compare_human: bool = typer.Option(
        True, "--compare-human/--no-compare-human",
        help="Show human scores from TSV alongside MAS scores (default: on).",
    ),
    verbose: bool = typer.Option(
        True, "--verbose", "-v",
        help="Show detailed pipeline logs.",
    ),
) -> None:
    """Extract essay from TSV, run real LLM evaluation, validate, and report."""

    if output_dir is None:
        output_dir = _DEFAULT_OUTPUT_BASE / str(essay_id)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Extract ──────────────────────────────────────────────────────
    typer.echo(f"[1/4] 从 TSV 提取文章 (ID={essay_id}) ...")
    try:
        essay_text, tsv_row = _extract_essay(source, essay_id)
    except ValueError as exc:
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"      文章长度：{len(essay_text)} 字符")

    # ── Step 2: Resolve bundle ───────────────────────────────────────────────
    typer.echo(f"[2/4] 加载配置 bundle ...")
    resolved = resolve_bundle(bundle)
    if verbose:
        typer.echo(f"      {resolved.get_version_info()}")

    # Build providers
    default_provider = None
    rater_providers: dict = {}
    stage_providers: dict = {}

    if resolved.provider_config is not None:
        try:
            default_provider, rater_providers, stage_providers = build_provider_map(
                resolved.provider_config
            )
        except ValueError as exc:
            typer.echo(f"错误：Provider 配置失败 — {exc}", err=True)
            raise typer.Exit(code=1)
        if verbose:
            typer.echo(f"      bundle provider_config: {len(rater_providers)} rater(s), {len(stage_providers)} stage(s)")
    else:
        legacy_entry = ProviderEntryConfig(api_key_env="LLM_API_KEY")
        try:
            default_provider = build_provider(legacy_entry)
        except ValueError as exc:
            typer.echo(f"错误：{exc}", err=True)
            raise typer.Exit(code=1)

    # Load prompt templates
    loader = PromptLoader()
    configs_prompts = _PROJECT_ROOT / "configs" / "prompts"
    prompt_templates = {}
    for name, filename in [
        ("evidence_extraction", "evidence_extraction.yaml"),
        ("scoring",             "scoring.yaml"),
        ("explanation",         "explanation.yaml"),
    ]:
        tpl_path = configs_prompts / filename
        if tpl_path.exists():
            prompt_templates[name] = loader.load(tpl_path)

    # ── Step 3: Run pipeline ─────────────────────────────────────────────────
    typer.echo(f"[3/4] 执行评价流水线 ...")
    request = EvaluationRequest(
        raw_text=essay_text,
        bundle_ref=f"{resolved.artifact_bundle.bundle_id}@{resolved.artifact_bundle.bundle_version}",
    )
    runner = PipelineRunner(
        resolved,
        provider=default_provider,
        rater_providers=rater_providers,
        stage_providers=stage_providers,
        prompt_templates=prompt_templates,
    )
    run_trace, feedback = runner.run(request)

    # Save artifacts
    trace_path = output_dir / "run_trace.json"
    feedback_path = output_dir / "feedback.json"
    hypotheses_path = output_dir / "hypotheses.json"
    trace_path.write_text(
        json.dumps(run_trace.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    feedback_path.write_text(
        json.dumps(feedback, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    hypotheses_path.write_text(
        json.dumps(
            {
                "run_id": run_trace.run_id,
                "hypotheses": [h.to_dict() for h in runner.last_hypotheses],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    if run_trace.status.value != "completed":
        typer.echo(f"错误：流水线未完成，状态 = {run_trace.status.value}", err=True)
        # Print failed node info
        for node in run_trace.node_traces:
            if node.status.value != "success":
                typer.echo(f"  失败节点：{node.node_id} — {node.error_message}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"      流水线完成，节点数：{len(run_trace.node_traces)}")

    # ── Step 4: Validate ─────────────────────────────────────────────────────
    typer.echo(f"[4/4] 验证结果合法性 ...")
    trace_dict = json.loads(trace_path.read_text(encoding="utf-8"))
    feedback_dict = json.loads(feedback_path.read_text(encoding="utf-8"))

    trace_failures = _validate_trace(trace_dict)
    feedback_failures = _validate_feedback(feedback_dict)
    all_failures = trace_failures + feedback_failures

    if all_failures:
        typer.echo("  验证失败：", err=True)
        for msg in all_failures:
            typer.echo(f"    ✗ {msg}", err=True)
        raise typer.Exit(code=1)
    else:
        typer.echo("      验证通过 ✅")

    # ── Report ────────────────────────────────────────────────────────────────
    tsv_row_for_report = tsv_row if compare_human else None
    _print_report(essay_id, trace_dict, feedback_dict, tsv_row_for_report)

    report_md = _build_report_md(essay_id, trace_dict, feedback_dict, tsv_row_for_report)
    report_path = output_dir / "report.md"
    report_path.write_text(report_md, encoding="utf-8")

    typer.echo(f"产出文件：")
    typer.echo(f"  {trace_path}")
    typer.echo(f"  {feedback_path}")
    typer.echo(f"  {hypotheses_path}")
    typer.echo(f"  {report_path}")


if __name__ == "__main__":
    app()
