#!/usr/bin/env python3
"""MAS 统一评估入口。

自动判断模式：
  - 提供 --essay-id  → 单篇评估（详细报告 + 人工评分对比）
  - 提供 --limit / --essay-ids / 不加任何筛选 → 批量评估

单篇示例：
  python scripts/eval.py --essay-id 20757

批量示例：
  python scripts/eval.py --limit 10
  python scripts/eval.py --essay-ids 20716,20717,20718
  python scripts/eval.py --force          # 全量重跑
  python scripts/eval.py --limit 20 --delay 2 --verbose
"""

import csv
import json
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import typer

from src.agents.mock_config_resolver import run as resolve_bundle
from src.contracts.artifact_bundle import ProviderEntryConfig
from src.contracts.request_models import EvaluationRequest
from src.pipeline.runner import PipelineRunner
from src.providers.factory import build_provider, build_provider_map
from src.providers.logging_provider import LoggingProvider
from src.providers.prompt_loader import PromptLoader

app = typer.Typer(name="eval", help="MAS 统一评估入口（单篇 / 批量）。")

_DEFAULT_SOURCE = _PROJECT_ROOT / "data" / "training_set_8.tsv"
_DEFAULT_BUNDLE = _PROJECT_ROOT / "configs" / "bundles" / "asap_set8_baseline.bundle.yaml"
_DEFAULT_OUTPUT_BASE = _PROJECT_ROOT / "artifacts" / "eval"

_DIM_ORDER = [
    ("ideas_content",    "Ideas & Content",    "💡"),
    ("organization",     "Organization",       "📐"),
    ("voice",            "Voice",              "🎤"),
    ("word_choice",      "Word Choice",        "📝"),
    ("sentence_fluency", "Sentence Fluency",   "🔄"),
    ("conventions",      "Conventions",        "✏️"),
]

_NODE_LABELS = {
    "node_preprocess":          "文档预处理",
    "node_coverage":            "维度覆盖确认",
    "node_extractor":           "证据抽取",
    "node_observer":            "证据整理",
    "node_scorer":              "双评审打分",
    "node_consistency_checker": "一致性检验",
    "node_adjudicator":         "裁决",
    "node_feedback":            "反馈生成",
}


# ── 工具函数 ───────────────────────────────────────────────────────────────────

def _duration(started: str, finished: str) -> str:
    fmt_candidates = ["%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"]
    for fmt in fmt_candidates:
        try:
            s = datetime.strptime(started, fmt)
            f = datetime.strptime(finished, fmt)
            secs = int((f - s).total_seconds())
            return f"{secs // 60}分{secs % 60}秒" if secs >= 60 else f"{secs}秒"
        except Exception:
            continue
    try:
        s = datetime.fromisoformat(started)
        f = datetime.fromisoformat(finished)
        secs = int((f - s).total_seconds())
        return f"{secs // 60}分{secs % 60}秒" if secs >= 60 else f"{secs}秒"
    except Exception:
        return "N/A"


def _bar(score: int, max_score: int = 6) -> str:
    filled = round(score / max_score * 10)
    return "█" * filled + "░" * (10 - filled)


def _grade(score: int) -> str:
    return {6: "优秀", 5: "良好+", 4: "良好", 3: "中等", 2: "需改进", 1: "薄弱"}.get(score, "?")


def _score(dim: dict) -> int:
    return dim.get("canonical_score") or dim.get("final_score") or 0


def _get_composite_info(feedback: dict):
    """从 feedback['composite'] 提取 (score, max_score, weights)，无 composite 时返回 None。"""
    c = feedback.get("composite")
    if not c:
        return None
    score = c.get("composite_score", {}).get("canonical_score")
    weights = c.get("aggregation_detail", {}).get("weights", {})
    if score is None or not weights:
        return None
    max_score = 6 * sum(w for w in weights.values() if w > 0)
    return score, max_score, weights


def _human_composite(human_r1: dict, human_r2: dict | None, weights: dict) -> float | None:
    """用 weights 计算人类评审员加权总分（avg(R1,R2) * weight）。"""
    if not human_r1:
        return None
    total = 0.0
    for dim_id, w in weights.items():
        if w == 0:
            continue
        h1 = human_r1.get(dim_id)
        h2 = human_r2.get(dim_id) if human_r2 else None
        if isinstance(h1, int) and isinstance(h2, int):
            total += (h1 + h2) / 2 * w
        elif isinstance(h1, int):
            total += h1 * w
    return total


def _load_tsv(tsv_path: Path) -> dict[str, dict]:
    """返回 {essay_id: row_dict}"""
    result = {}
    with open(tsv_path, encoding="latin-1") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            eid = row.get("essay_id", "").strip()
            if eid:
                result[eid] = row
    return result


def _get_human_scores(tsv_row: dict | None) -> tuple[dict, dict]:
    r1, r2 = {}, {}
    if not tsv_row:
        return r1, r2
    for i, (key, _, _) in enumerate(_DIM_ORDER):
        v1 = tsv_row.get(f"rater1_trait{i+1}", "")
        v2 = tsv_row.get(f"rater2_trait{i+1}", "")
        if str(v1).strip().isdigit():
            r1[key] = int(v1)
        if str(v2).strip().isdigit():
            r2[key] = int(v2)
    return r1, r2


def _snapshot_stats(log_providers: list) -> tuple[int, int, float]:
    calls = sum(p.call_count for p in log_providers)
    tokens = sum(p.total_tokens for p in log_providers)
    elapsed = sum(p.total_elapsed for p in log_providers)
    return calls, tokens, elapsed


def _wrap_providers(default_provider, rater_providers, stage_providers):
    log_list = []
    if default_provider is not None:
        wp = LoggingProvider(default_provider, label="default")
        log_list.append(wp)
    else:
        wp = None
    wr = {}
    for rid, p in rater_providers.items():
        lp = LoggingProvider(p, label=rid)
        wr[rid] = lp
        log_list.append(lp)
    ws = {}
    for stage, p in stage_providers.items():
        lp = LoggingProvider(p, label=stage)
        ws[stage] = lp
        log_list.append(lp)
    return wp, wr, ws, log_list


# ── 内部信息展示 ────────────────────────────────────────────────────────────────

def _print_node_timeline(trace: dict) -> None:
    typer.echo("")
    typer.echo("  ── 执行过程 " + "─" * 50)
    typer.echo(f"  {'节点':<16} {'状态':^6} {'耗时':>8}   {'输入':^20} → {'输出'}")
    typer.echo("  " + "─" * 70)
    for node in trace.get("node_traces", []):
        nid = node["node_id"]
        if nid.startswith("__"):
            continue
        label = _NODE_LABELS.get(nid, nid)
        icon = "✅" if node["status"] == "success" else "❌"
        dur = _duration(node.get("started_at", ""), node.get("finished_at", ""))
        in_ref = (node.get("input_ref") or "")[:20]
        out_ref = node.get("output_ref") or ""
        typer.echo(f"  {icon} {label:<14} {node['status']:^6} {dur:>8}   {in_ref:<20} → {out_ref}")

    # 一致性结论
    checker = next((n for n in trace["node_traces"] if n["node_id"] == "node_consistency_checker"), None)
    if checker:
        ref = checker.get("output_ref", "conflicts:0")
        n_conflicts = int(ref.split(":")[1]) if ":" in ref else 0
        if n_conflicts == 0:
            typer.echo("  ✅ 两位评审员完全一致，无需裁决")
        else:
            typer.echo(f"  ⚠️  发现 {n_conflicts} 个分歧，已触发裁决")


def _print_agent_hypotheses(hypotheses_path: Path) -> None:
    """展示各评审员原始打分假设。"""
    if not hypotheses_path.exists():
        return
    data = json.loads(hypotheses_path.read_text(encoding="utf-8"))
    hyps = data.get("hypotheses", [])
    if not hyps:
        return

    # 按维度聚合：{dim_id: {rater_id: score}}
    by_dim: dict[str, dict] = {}
    for h in hyps:
        did = h["dimension_id"]
        rid = h["rater_id"]
        sc = h["score"].get("canonical_score") or h["score"].get("score_value") or "?"
        by_dim.setdefault(did, {})[rid] = sc

    typer.echo("")
    typer.echo("  ── 评审员原始假设分数 " + "─" * 42)
    # 收集所有 rater_id 排序
    rater_ids = sorted({rid for dim_scores in by_dim.values() for rid in dim_scores})
    header_raters = "  ".join(f"{r:>10}" for r in rater_ids)
    typer.echo(f"  {'维度':<20}  {header_raters}  {'分歧'}")
    typer.echo("  " + "─" * 56)
    for key, name, icon in _DIM_ORDER:
        if key not in by_dim:
            continue
        dim_scores = by_dim[key]
        scores_str = "  ".join(f"{dim_scores.get(r, '?'):>10}" for r in rater_ids)
        vals = [dim_scores[r] for r in rater_ids if r in dim_scores and isinstance(dim_scores[r], int)]
        diff = max(vals) - min(vals) if len(vals) >= 2 else 0
        diff_str = f"  ⚠️ Δ={diff}" if diff > 1 else ""
        typer.echo(f"  {icon} {name:<18}  {scores_str}{diff_str}")


def _print_llm_stats(log_providers: list) -> None:
    """展示 LLM 调用统计。"""
    if not log_providers:
        return
    total_calls, total_tokens, total_elapsed = _snapshot_stats(log_providers)
    if total_calls == 0:
        return
    typer.echo("")
    typer.echo("  ── LLM 调用统计 " + "─" * 56)
    typer.echo(f"  {'角色':<22}  {'模型':<22}  {'调用次数':>6}  {'Token':>10}  {'耗时(s)':>8}")
    typer.echo("  " + "─" * 76)
    for lp in log_providers:
        if lp.call_count > 0:
            typer.echo(
                f"  {lp._label:<22}  {lp.model_id:<22}  {lp.call_count:>6}  "
                f"{lp.total_tokens:>10,}  {lp.total_elapsed:>8.1f}"
            )
    typer.echo("  " + "─" * 76)
    typer.echo(
        f"  {'合计':<22}  {'':22}  {total_calls:>6}  "
        f"{total_tokens:>10,}  {total_elapsed:>8.1f}"
    )


def _print_score_table(trace: dict, feedback: dict, tsv_row: dict | None) -> None:
    dims = feedback.get("dimensions", {})
    total_mas = sum(_score(dims[k]) for k, _, _ in _DIM_ORDER if k in dims)
    human_r1, human_r2 = _get_human_scores(tsv_row)
    has_human = bool(human_r1)
    cinfo = _get_composite_info(feedback)

    typer.echo("")
    typer.echo("  ── 综合评分 " + "─" * 52)
    if has_human:
        typer.echo(f"  {'维度':<18} {'MAS':>4}  {'进度条':<12}  {'等级':<5}  {'人R1':>4}  {'人R2':>4}  {'偏差':>5}")
    else:
        typer.echo(f"  {'维度':<18} {'MAS':>4}  {'进度条':<12}  {'等级':<5}")
    typer.echo("  " + "─" * 64)

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
            if isinstance(h1, int): total_h1 += h1
            if isinstance(h2, int): total_h2 += h2
            typer.echo(f"  {label:<18} {mas:>4}  {bar:<12}  {grade:<5}  {str(h1):>4}  {str(h2):>4}  {diff:>5}")
        else:
            typer.echo(f"  {label:<18} {mas:>4}  {bar:<12}  {grade:<5}")

    typer.echo("  " + "─" * 64)
    if has_human:
        total_avg = (total_h1 + total_h2) / 2
        diff_total = f"{total_mas - total_avg:+.1f}"
        typer.echo(f"  {'合计':<18} {total_mas:>4}  {'':12}  {'':5}  {total_h1:>4}  {total_h2:>4}  {diff_total:>5}")
        if cinfo:
            c_score, c_max, c_weights = cinfo
            h_comp = _human_composite(human_r1, human_r2, c_weights)
            if h_comp is not None:
                typer.echo(f"  ASAP加权总分: {c_score}/{c_max} ({c_score/c_max*100:.0f}%)  |  人类均值: {h_comp:.1f}/{c_max} ({h_comp/c_max*100:.0f}%)")
            else:
                typer.echo(f"  ASAP加权总分: {c_score}/{c_max} ({c_score/c_max*100:.0f}%)")
        else:
            typer.echo(f"  满分: 36  |  MAS: {total_mas} ({total_mas/36*100:.0f}%)  |  人类均值: {total_avg:.1f} ({total_avg/36*100:.0f}%)")
    else:
        typer.echo(f"  {'合计':<18} {total_mas:>4}  满分 36 分（{total_mas/36*100:.0f}%）")
        if cinfo:
            c_score, c_max, _ = cinfo
            typer.echo(f"  ASAP加权总分: {c_score}/{c_max} ({c_score/c_max*100:.0f}%)")


def _print_dimension_feedback(feedback: dict) -> None:
    dims = feedback.get("dimensions", {})
    typer.echo("")
    typer.echo("  ── 各维度详细反馈 " + "─" * 46)
    for key, name, icon in _DIM_ORDER:
        if key not in dims:
            continue
        dim = dims[key]
        mas = _score(dim)
        typer.echo(f"\n  {icon} {name}  —  {mas}/6  {_grade(mas)}")
        for desc in (dim.get("descriptor_refs") or [])[:3]:
            typer.echo(f"     • {desc}")
        text = dim.get("feedback_text", "")
        if text:
            for line in textwrap.wrap(text, width=68):
                typer.echo(f"    {line}")


def _save_report_md(essay_id, trace: dict, feedback: dict, tsv_row: dict | None, output_dir: Path) -> Path:
    dims = feedback.get("dimensions", {})
    total_mas = sum(_score(dims[k]) for k, _, _ in _DIM_ORDER if k in dims)
    cinfo = _get_composite_info(feedback)
    started = trace.get("started_at", "")
    finished = trace.get("finished_at", "")
    human_r1, human_r2 = _get_human_scores(tsv_row)
    has_human = bool(human_r1)

    lines = [
        f"# 文章评价报告 — 样本 {essay_id}", "",
        "| 项目 | 内容 |", "|------|------|",
        f"| 评价时间 | {started[:19].replace('T', ' ')} |",
        f"| 耗时 | {_duration(started, finished)} |",
        f"| 运行 ID | `{trace.get('run_id', '')}` |",
        f"| 量规版本 | `{trace.get('bundle_id', '')}@{trace.get('bundle_version', '')}` |",
        "",
    ]

    lines += ["## 执行过程", "", "| 步骤 | 状态 | 耗时 | 输出 |", "|------|------|------|------|"]
    for node in trace.get("node_traces", []):
        nid = node["node_id"]
        if nid.startswith("__"): continue
        label = _NODE_LABELS.get(nid, nid)
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

    lines += ["## 综合得分", ""]
    total_h1 = total_h2 = 0
    if has_human:
        lines += ["| 维度 | MAS | 等级 | 人类R1 | 人类R2 | 偏差 |", "|------|:---:|:---:|:---:|:---:|:---:|"]
    else:
        lines += ["| 维度 | MAS | 等级 |", "|------|:---:|:---:|"]

    for key, name, icon in _DIM_ORDER:
        if key not in dims: continue
        mas = _score(dims[key])
        grade = _grade(mas)
        label = f"{icon} {name}"
        if has_human:
            h1 = human_r1.get(key, "-")
            h2 = human_r2.get(key, "-")
            avg = (h1 + h2) / 2 if isinstance(h1, int) and isinstance(h2, int) else None
            diff = f"{mas - avg:+.1f}" if avg is not None else "N/A"
            if isinstance(h1, int): total_h1 += h1
            if isinstance(h2, int): total_h2 += h2
            lines.append(f"| {label} | **{mas}** | {grade} | {h1} | {h2} | {diff} |")
        else:
            lines.append(f"| {label} | **{mas}** | {grade} |")

    if has_human:
        total_avg = (total_h1 + total_h2) / 2
        lines.append(f"| **合计** | **{total_mas}** | | {total_h1} | {total_h2} | {total_mas - total_avg:+.1f} |")
        if cinfo:
            c_score, c_max, c_weights = cinfo
            h_comp = _human_composite(human_r1, human_r2, c_weights)
            if h_comp is not None:
                lines += ["", f"**ASAP加权总分：{c_score}/{c_max}（{c_score/c_max*100:.0f}%）** | 人类均值：{h_comp:.1f}/{c_max}（{h_comp/c_max*100:.0f}%）", ""]
            else:
                lines += ["", f"**ASAP加权总分：{c_score}/{c_max}（{c_score/c_max*100:.0f}%）**", ""]
        else:
            lines += ["", f"**满分：36 分** | MAS：{total_mas} 分（{total_mas/36*100:.0f}%）| 人类均值：{total_avg:.1f} 分（{total_avg/36*100:.0f}%）", ""]
    else:
        lines.append(f"| **合计** | **{total_mas}** | 满分 36 分（{total_mas/36*100:.0f}%） |")
        if cinfo:
            c_score, c_max, _ = cinfo
            lines += ["", f"**ASAP加权总分：{c_score}/{c_max}（{c_score/c_max*100:.0f}%）**", ""]
        else:
            lines.append("")

    lines.append("## 各维度详细反馈")
    for key, name, icon in _DIM_ORDER:
        if key not in dims: continue
        dim = dims[key]
        mas = _score(dim)
        lines += ["", f"### {icon} {name} — {mas}/6　{_grade(mas)}", ""]
        for desc in (dim.get("descriptor_refs") or [])[:3]:
            lines.append(f"- {desc}")
        text = dim.get("feedback_text", "")
        if text:
            lines += ["", text, ""]

    lines += ["---", f"*由 MAS 自动评价系统生成 | 运行 ID: `{trace.get('run_id', '')}`*", ""]
    report_path = output_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


# ── 核心流水线执行 ──────────────────────────────────────────────────────────────

def _init_providers(resolved, verbose: bool):
    """初始化 provider，返回 (default, raters, stages, log_providers)。"""
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
        typer.echo(f"[init] provider_config: {len(rater_providers)} rater(s), {len(stage_providers)} stage(s)")
    else:
        legacy_entry = ProviderEntryConfig(api_key_env="LLM_API_KEY")
        try:
            default_provider = build_provider(legacy_entry)
        except ValueError as exc:
            typer.echo(f"错误：{exc}", err=True)
            raise typer.Exit(code=1)
        typer.echo("[init] 使用全局 LLM_* 环境变量（无 bundle provider_config）")

    # 始终用 LoggingProvider 包装以收集统计信息
    default_provider, rater_providers, stage_providers, log_providers = _wrap_providers(
        default_provider, rater_providers, stage_providers
    )

    # 打印各角色绑定的模型
    typer.echo("[init] LLM 分配：")
    for lp in log_providers:
        typer.echo(f"         {lp._label:<22} → {lp.model_id}")

    return default_provider, rater_providers, stage_providers, log_providers


def _load_prompt_templates() -> dict:
    loader = PromptLoader()
    templates = {}
    configs_prompts = _PROJECT_ROOT / "configs" / "prompts"
    for name, filename in [
        ("evidence_extraction", "evidence_extraction.yaml"),
        ("scoring", "scoring.yaml"),
        ("explanation", "explanation.yaml"),
    ]:
        tpl_path = configs_prompts / filename
        if tpl_path.exists():
            templates[name] = loader.load(tpl_path)
    return templates


def _run_single(
    essay_id: str,
    essay_text: str,
    tsv_row: dict | None,
    resolved,
    default_provider,
    rater_providers: dict,
    stage_providers: dict,
    log_providers: list,
    prompt_templates: dict,
    output_dir: Path,
    verbose: bool,
) -> bool:
    """执行单篇评估，打印内部信息，返回是否成功。"""
    output_dir.mkdir(parents=True, exist_ok=True)

    stats_before = _snapshot_stats(log_providers)

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

    # 保存产出
    trace_path = output_dir / "run_trace.json"
    feedback_path = output_dir / "feedback.json"
    hypotheses_path = output_dir / "hypotheses.json"
    trace_path.write_text(json.dumps(run_trace.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    feedback_path.write_text(json.dumps(feedback, indent=2, ensure_ascii=False), encoding="utf-8")
    hypotheses_path.write_text(
        json.dumps(
            {"run_id": run_trace.run_id, "hypotheses": [h.to_dict() for h in runner.last_hypotheses]},
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    ok = run_trace.status.value == "completed"

    trace_dict = json.loads(trace_path.read_text(encoding="utf-8"))
    feedback_dict = json.loads(feedback_path.read_text(encoding="utf-8"))

    if verbose:
        typer.echo("")
        typer.echo("=" * 68)
        typer.echo(f"  评价报告  —  样本 {essay_id}")
        typer.echo("=" * 68)
        started = trace_dict.get("started_at", "")
        finished = trace_dict.get("finished_at", "")
        typer.echo(f"  评价时间：{started[:19].replace('T', ' ')}  |  耗时：{_duration(started, finished)}")
        typer.echo(f"  运行 ID ：{run_trace.run_id}")
        typer.echo(f"  量规版本：{trace_dict.get('bundle_id', '')}@{trace_dict.get('bundle_version', '')}")
        typer.echo(f"  状态    ：{'✅ completed' if ok else '❌ ' + run_trace.status.value}")

        _print_node_timeline(trace_dict)
        _print_agent_hypotheses(hypotheses_path)

        # LLM 统计（本次增量）
        c0, t0, e0 = stats_before
        c1, t1, e1 = _snapshot_stats(log_providers)
        typer.echo("")
        typer.echo("  ── 本次 LLM 调用汇总 " + "─" * 52)
        typer.echo(f"  总调用: {c1-c0}  |  总 Token: {t1-t0:,}  |  LLM 耗时: {e1-e0:.1f}s")
        typer.echo(f"  {'角色':<22}  {'模型':<22}  {'调用':>4}  {'Token':>10}  {'耗时(s)':>8}")
        typer.echo("  " + "─" * 72)
        for lp in log_providers:
            if lp.call_count > 0:
                typer.echo(
                    f"  {lp._label:<22}  {lp.model_id:<22}  {lp.call_count:>4}  "
                    f"{lp.total_tokens:>10,}  {lp.total_elapsed:>8.1f}"
                )

        if ok:
            _print_score_table(trace_dict, feedback_dict, tsv_row)
            _print_dimension_feedback(feedback_dict)
        else:
            typer.echo("\n  流水线未完成，失败节点：")
            for node in run_trace.node_traces:
                if node.status.value != "success":
                    typer.echo(f"    ❌ {node.node_id} — {node.error_message}")

    else:
        # 非 verbose：仅打印分数一行
        dims = feedback_dict.get("dimensions", {})
        scores = " ".join(str(_score(dims[k])) if k in dims else "?" for k, _, _ in _DIM_ORDER)
        cinfo = _get_composite_info(feedback_dict)
        if cinfo:
            c_score, c_max, _ = cinfo
            total_str = f"{c_score}/{c_max}"
        else:
            total_str = f"{sum(_score(dims[k]) for k, _, _ in _DIM_ORDER if k in dims)}/36"
        status = "✅" if ok else "❌"
        typer.echo(f"  {status}  [{scores}]  合计={total_str}")

    if ok and verbose:
        report_path = _save_report_md(essay_id, trace_dict, feedback_dict, tsv_row, output_dir)
        typer.echo("")
        typer.echo("  产出文件：")
        for p in [trace_path, feedback_path, hypotheses_path, report_path]:
            typer.echo(f"    {p}")
        typer.echo("=" * 68)

    return ok


# ── 主命令 ─────────────────────────────────────────────────────────────────────

@app.command()
def main(
    essay_id: int = typer.Option(
        None, "--essay-id", "-e",
        help="单篇模式：评估指定 essay_id。",
    ),
    essay_ids: str = typer.Option(
        "", "--essay-ids",
        help="批量模式：逗号分隔的 essay_id 列表，如 '20716,20717'。",
    ),
    limit: int = typer.Option(
        0, "--limit", "-n",
        help="批量模式：最多处理篇数（0 = 全部）。",
    ),
    source: Path = typer.Option(
        _DEFAULT_SOURCE, "--source", "-s",
        help="TSV 数据文件路径。",
    ),
    bundle: Path = typer.Option(
        _DEFAULT_BUNDLE, "--bundle", "-b",
        help="配置 bundle 文件路径。",
    ),
    output_dir: Path = typer.Option(
        None, "--output-dir", "-o",
        help="产出目录（单篇默认 artifacts/eval/{essay_id}，批量默认 artifacts/eval）。",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="批量模式：覆盖已有结果重新评估。",
    ),
    delay: float = typer.Option(
        5.0, "--delay",
        help="批量模式：每篇间隔秒数（降低 API 限速风险）。",
    ),
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose", "-v",
        help="显示详细内部信息（节点轨迹、评审员假设、LLM 统计）。",
    ),
) -> None:
    """MAS 统一评估入口。提供 --essay-id 为单篇模式，否则为批量模式。"""

    single_mode = essay_id is not None

    # ── 加载 TSV ──────────────────────────────────────────────────────────────
    typer.echo(f"[init] 读取 TSV: {source}")
    all_rows = _load_tsv(source)
    typer.echo(f"[init] TSV 共 {len(all_rows)} 条记录")

    # ── 解析目标列表 ──────────────────────────────────────────────────────────
    if single_mode:
        targets = [(str(essay_id), all_rows.get(str(essay_id), {}).get("essay", ""))]
        if not targets[0][1]:
            typer.echo(f"错误：essay_id {essay_id} 不在 TSV 中", err=True)
            raise typer.Exit(code=1)
    else:
        id_filter = {x.strip() for x in essay_ids.split(",") if x.strip().isdigit()} if essay_ids else None
        targets = []
        for eid, row in all_rows.items():
            if id_filter and eid not in id_filter:
                continue
            targets.append((eid, row.get("essay", "")))
            if limit > 0 and len(targets) >= limit:
                break
        if not targets:
            typer.echo("错误：未找到符合条件的文章", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"[init] 共 {len(targets)} 篇文章待处理")

    # ── 加载 bundle ───────────────────────────────────────────────────────────
    typer.echo(f"[init] 加载 bundle: {bundle}")
    resolved = resolve_bundle(bundle)
    typer.echo(f"[init] {resolved.get_version_info()}")

    # ── 初始化 providers ──────────────────────────────────────────────────────
    default_provider, rater_providers, stage_providers, log_providers = _init_providers(resolved, verbose)

    # ── 加载 prompt 模板 ──────────────────────────────────────────────────────
    prompt_templates = _load_prompt_templates()
    typer.echo(f"[init] 加载 {len(prompt_templates)} 个 prompt 模板")

    # ── 确定输出根目录 ────────────────────────────────────────────────────────
    output_base = output_dir if output_dir else _DEFAULT_OUTPUT_BASE

    # ── 执行评估 ──────────────────────────────────────────────────────────────
    results: dict[str, list] = {"success": [], "skipped": [], "failed": []}
    total = len(targets)

    typer.echo("=" * 68)

    for i, (eid, essay_text) in enumerate(targets, 1):
        essay_out = Path(output_base) / eid

        # 批量模式下的幂等跳过
        if not single_mode and (essay_out / "feedback.json").exists() and not force:
            typer.echo(f"[{i:>4}/{total}] SKIP  {eid}  (已存在)")
            results["skipped"].append(eid)
            continue

        typer.echo(f"[{i:>4}/{total}] 开始  {eid}  ({len(essay_text)} 字符)")

        tsv_row = all_rows.get(eid) if all_rows else None

        try:
            ok = _run_single(
                essay_id=eid,
                essay_text=essay_text,
                tsv_row=tsv_row,
                resolved=resolved,
                default_provider=default_provider,
                rater_providers=rater_providers,
                stage_providers=stage_providers,
                log_providers=log_providers,
                prompt_templates=prompt_templates,
                output_dir=essay_out,
                verbose=verbose,
            )
            if ok:
                results["success"].append(eid)
            else:
                results["failed"].append(eid)
        except Exception as exc:
            typer.echo(f"  ❌ 异常: {exc}", err=True)
            results["failed"].append(eid)

        if not single_mode and i < total and delay > 0:
            time.sleep(delay)

    # ── 汇总（批量模式） ──────────────────────────────────────────────────────
    if not single_mode:
        typer.echo("=" * 68)
        typer.echo(
            f"完成  总计: {total}  "
            f"✅ 成功: {len(results['success'])}  "
            f"⏭  跳过: {len(results['skipped'])}  "
            f"❌ 失败: {len(results['failed'])}"
        )
        _print_llm_stats(log_providers)

        if results["failed"]:
            typer.echo(f"失败列表: {', '.join(results['failed'])}", err=True)
            raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
