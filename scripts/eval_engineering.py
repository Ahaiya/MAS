#!/usr/bin/env python3
"""工程能力评估入口：直接评估 .md 格式的工程项目对话记录。

用法：
  python scripts/eval_engineering.py --input "data/1组—虚拟故居重建计划.md"
  python scripts/eval_engineering.py --input data/xxx.md --verbose
  python scripts/eval_engineering.py --input data/xxx.md --debug-bundle
"""

import json
import sys
import textwrap
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import typer

from src.agents.config_resolver import run as resolve_bundle
from src.contracts.artifact_bundle import ProviderEntryConfig
from src.outer_loop.experiments.batch_runner import run_single_eval
from src.providers.factory import build_provider, build_provider_map
from src.providers.logging_provider import LoggingProvider
from src.providers.prompt_loader import PromptLoader

app = typer.Typer(name="eval_engineering", help="工程能力评估入口（读取 .md 项目记录）。")

_DEFAULT_BUNDLE = _PROJECT_ROOT / "configs" / "bundles" / "engineering_eval_baseline.bundle.yaml"
_DEFAULT_OUTPUT_BASE = _PROJECT_ROOT / "artifacts" / "eval_engineering"

_DIM_ORDER = [
    ("problem_analysis",        "问题认知与分析能力", "A"),
    ("solution_design",         "方案设计与创新能力", "B"),
    ("project_execution",       "项目实施与决策能力", "C"),
    ("technical_proficiency",   "技术使用与掌控能力", "D"),
    ("communication_teamwork",  "沟通与团队协同能力", "E"),
    ("professional_responsibility", "职业发展与责任意识", "F"),
]


# ── 工具函数 ───────────────────────────────────────────────────────────────────

def _duration(started: str, finished: str) -> str:
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            s = datetime.strptime(started, fmt)
            f = datetime.strptime(finished, fmt)
            secs = int((f - s).total_seconds())
            return f"{secs // 60}分{secs % 60}秒" if secs >= 60 else f"{secs}秒"
        except Exception:
            continue
    try:
        secs = int((datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds())
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


def _snapshot_stats(log_providers: list) -> tuple[int, int, float]:
    calls = sum(p.call_count for p in log_providers)
    tokens = sum(p.total_tokens for p in log_providers)
    elapsed = sum(p.total_elapsed for p in log_providers)
    return calls, tokens, elapsed


def _wrap_providers(default_provider, rater_providers, stage_providers):
    log_list = []
    wp = None
    if default_provider is not None:
        wp = LoggingProvider(default_provider, label="default")
        log_list.append(wp)
    wr, ws = {}, {}
    for rid, p in rater_providers.items():
        lp = LoggingProvider(p, label=rid)
        wr[rid] = lp
        log_list.append(lp)
    for stage, p in stage_providers.items():
        lp = LoggingProvider(p, label=stage)
        ws[stage] = lp
        log_list.append(lp)
    return wp, wr, ws, log_list


def _load_prompt_templates() -> dict:
    loader = PromptLoader()
    templates = {}
    configs_prompts = _PROJECT_ROOT / "configs" / "prompts"
    for name, filename in [
        ("evidence_extraction", "evidence_extraction.yaml"),
        ("scoring", "scoring.yaml"),
        ("explanation", "explanation.yaml"),
        ("chunking", "chunking.yaml"),
        ("dimension_relevance", "dimension_relevance.yaml"),
    ]:
        tpl_path = configs_prompts / filename
        if tpl_path.exists():
            templates[name] = loader.load(tpl_path)
    return templates


# ── 展示函数 ───────────────────────────────────────────────────────────────────

def _print_node_timeline(trace: dict) -> None:
    _NODE_LABELS = {
        "node_preprocess":          "文档预处理",
        "node_coverage":            "维度覆盖确认",
        "node_extractor":           "证据抽取",
        "node_observer":            "证据整理",
        "node_scorer":              "双评审打分",
        "node_resolution_scorer":   "冲突重评分",
        "node_consistency_checker": "一致性检验",
        "node_adjudicator":         "裁决",
        "node_feedback":            "反馈生成",
    }
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

    checker = next(
        (n for n in trace["node_traces"] if n["node_id"] == "node_consistency_checker"), None
    )
    if checker:
        ref = checker.get("output_ref", "conflicts:0")
        n_conflicts = int(ref.split(":")[1]) if ":" in ref else 0
        if n_conflicts == 0:
            typer.echo("  ✅ 两位评审员完全一致，无需裁决")
        else:
            typer.echo(f"  ⚠️  发现 {n_conflicts} 个分歧，已触发裁决")


def _print_agent_hypotheses(hypotheses_path: Path) -> None:
    if not hypotheses_path.exists():
        return
    data = json.loads(hypotheses_path.read_text(encoding="utf-8"))
    hyps = data.get("hypotheses", [])
    if not hyps:
        return

    by_dim: dict[str, dict] = {}
    for h in hyps:
        did = h["dimension_id"]
        rid = h["rater_id"]
        sc = h["score"].get("canonical_score") or h["score"].get("score_value") or "?"
        by_dim.setdefault(did, {})[rid] = sc

    typer.echo("")
    typer.echo("  ── 评审员原始假设分数 " + "─" * 42)
    rater_ids = sorted({rid for dim_scores in by_dim.values() for rid in dim_scores})
    header_raters = "  ".join(f"{r:>10}" for r in rater_ids)
    typer.echo(f"  {'维度':<24}  {header_raters}  {'分歧'}")
    typer.echo("  " + "─" * 60)
    for key, name, code in _DIM_ORDER:
        if key not in by_dim:
            continue
        dim_scores = by_dim[key]
        scores_str = "  ".join(f"{dim_scores.get(r, '?'):>10}" for r in rater_ids)
        vals = [dim_scores[r] for r in rater_ids if r in dim_scores and isinstance(dim_scores[r], int)]
        diff = max(vals) - min(vals) if len(vals) >= 2 else 0
        diff_str = f"  ⚠️ Δ={diff}" if diff > 1 else ""
        typer.echo(f"  [{code}] {name:<20}  {scores_str}{diff_str}")


def _print_score_table(feedback: dict) -> None:
    dims = feedback.get("dimensions", {})
    total_mas = sum(_score(dims[k]) for k, _, _ in _DIM_ORDER if k in dims)
    cinfo = _get_composite_info(feedback)

    typer.echo("")
    typer.echo("  ── 综合评分 " + "─" * 52)
    typer.echo(f"  {'维度':<24} {'得分':>4}  {'进度条':<12}  {'等级':<5}")
    typer.echo("  " + "─" * 56)
    for key, name, code in _DIM_ORDER:
        if key not in dims:
            continue
        mas = _score(dims[key])
        typer.echo(f"  [{code}] {name:<20} {mas:>4}  {_bar(mas):<12}  {_grade(mas):<5}")
    typer.echo("  " + "─" * 56)
    typer.echo(f"  {'合计':<24} {total_mas:>4}  满分 36 分（{total_mas / 36 * 100:.0f}%）")
    if cinfo:
        c_score, c_max, _ = cinfo
        typer.echo(f"  综合能力得分: {c_score}/{c_max} ({c_score / c_max * 100:.0f}%)")


def _get_composite_info(feedback: dict):
    c = feedback.get("composite")
    if not c:
        return None
    score = c.get("composite_score", {}).get("canonical_score")
    weights = c.get("aggregation_detail", {}).get("weights", {})
    if score is None or not weights:
        return None
    max_score = 6 * sum(w for w in weights.values() if w > 0)
    return score, max_score, weights


def _print_dimension_feedback(feedback: dict) -> None:
    dims = feedback.get("dimensions", {})
    typer.echo("")
    typer.echo("  ── 各维度详细反馈 " + "─" * 46)
    for key, name, code in _DIM_ORDER:
        if key not in dims:
            continue
        dim = dims[key]
        mas = _score(dim)
        typer.echo(f"\n  [{code}] {name}  —  {mas}/6  {_grade(mas)}")
        for desc in (dim.get("descriptor_refs") or [])[:3]:
            typer.echo(f"     • {desc}")
        text = dim.get("feedback_text", "")
        if text:
            for line in textwrap.wrap(text, width=70):
                typer.echo(f"    {line}")


# ── 主命令 ─────────────────────────────────────────────────────────────────────

@app.command()
def main(
    input_file: Path = typer.Option(
        ..., "--input", "-i",
        help="待评估的工程项目对话记录文件（.md 或 .txt）。",
    ),
    sample_id: str = typer.Option(
        "", "--id",
        help="样本 ID（默认使用文件名 stem）。",
    ),
    bundle: Path = typer.Option(
        _DEFAULT_BUNDLE, "--bundle", "-b",
        help="配置 bundle 文件路径。",
    ),
    output_dir: Path = typer.Option(
        None, "--output-dir", "-o",
        help="产出目录（默认 artifacts/eval_engineering/{id}）。",
    ),
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose", "-v",
        help="显示详细内部信息。",
    ),
    mock_provider: bool = typer.Option(
        False, "--mock-provider",
        help="使用 mock 模式（不调用 LLM）。",
    ),
    debug_bundle: bool = typer.Option(
        False, "--debug-bundle",
        help="输出调试 bundle（事件流、LLM 请求响应）。",
    ),
) -> None:
    """工程能力评估入口：读取 .md 项目记录文件，输出六维度评分与反馈。"""

    if not input_file.exists():
        typer.echo(f"错误：文件不存在: {input_file}", err=True)
        raise typer.Exit(code=1)

    essay_id = sample_id.strip() if sample_id.strip() else input_file.stem
    essay_text = input_file.read_text(encoding="utf-8")
    typer.echo(f"[init] 读取文件: {input_file}  ({len(essay_text):,} 字符)")

    # 加载 bundle
    typer.echo(f"[init] 加载 bundle: {bundle}")
    resolved = resolve_bundle(bundle)
    typer.echo(f"[init] {resolved.get_version_info()}")
    typer.echo(f"[init] 量规维度: {[d['dimension_id'] for d in resolved.rubric_snapshot.dimensions]}")

    # 初始化 providers
    if mock_provider:
        typer.echo("[init] mock provider 模式：不调用 LLM")
        default_provider, rater_providers, stage_providers, log_providers = None, {}, {}, []
    elif resolved.provider_config is not None:
        try:
            default_provider, rater_providers, stage_providers = build_provider_map(
                resolved.provider_config
            )
        except ValueError as exc:
            typer.echo(f"错误：Provider 配置失败 — {exc}", err=True)
            raise typer.Exit(code=1)
        default_provider, rater_providers, stage_providers, log_providers = _wrap_providers(
            default_provider, rater_providers, stage_providers
        )
    else:
        try:
            default_provider = build_provider(ProviderEntryConfig(api_key_env="LLM_API_KEY"))
        except ValueError as exc:
            typer.echo(f"错误：{exc}", err=True)
            raise typer.Exit(code=1)
        default_provider, rater_providers, stage_providers, log_providers = _wrap_providers(
            default_provider, {}, {}
        )

    if log_providers:
        typer.echo("[init] LLM 分配：")
        for lp in log_providers:
            typer.echo(f"         {lp._label:<24} → {lp.model_id}")

    # 加载 prompt 模板
    prompt_templates = _load_prompt_templates()
    typer.echo(f"[init] 加载 {len(prompt_templates)} 个 prompt 模板")

    # 确定输出目录
    out_dir = output_dir if output_dir else (_DEFAULT_OUTPUT_BASE / essay_id)

    typer.echo("=" * 68)
    typer.echo(f"[开始评估] 样本 ID: {essay_id}")

    # 执行评估
    try:
        result = run_single_eval(
            essay_id=essay_id,
            essay_text=essay_text,
            tsv_row=None,
            resolved=resolved,
            default_provider=default_provider,
            rater_providers=rater_providers,
            stage_providers=stage_providers,
            log_providers=log_providers,
            prompt_templates=prompt_templates,
            output_dir=out_dir,
            verbose=verbose,
            debug_bundle=debug_bundle,
        )
    except Exception as exc:
        typer.echo(f"❌ 评估失败: {exc}", err=True)
        raise typer.Exit(code=1)

    ok = result.success
    trace = result.trace_dict
    feedback = result.feedback_dict

    started = trace.get("started_at", "")
    finished = trace.get("finished_at", "")
    status_value = trace.get("status", "unknown")

    typer.echo("")
    typer.echo("=" * 68)
    typer.echo(f"  评价报告  —  样本 {essay_id}")
    typer.echo("=" * 68)
    typer.echo(f"  评价时间：{started[:19].replace('T', ' ')}  |  耗时：{_duration(started, finished)}")
    typer.echo(f"  运行 ID ：{trace.get('run_id', '')}")
    typer.echo(f"  量规版本：{trace.get('bundle_id', '')}@{trace.get('bundle_version', '')}")
    typer.echo(f"  状态    ：{'✅ completed' if ok else '❌ ' + status_value}")

    if verbose and ok:
        _print_node_timeline(trace)
        _print_agent_hypotheses(out_dir / "hypotheses.json")
        _print_score_table(feedback)
        _print_dimension_feedback(feedback)

    elif not ok:
        typer.echo("\n  流水线未完成，失败节点：")
        for node in trace.get("node_traces", []):
            if node.get("status") != "success":
                typer.echo(f"    ❌ {node.get('node_id')} — {node.get('error_message')}")

    # LLM 统计
    if log_providers:
        total_calls = sum(p.call_count for p in log_providers)
        total_tokens = sum(p.total_tokens for p in log_providers)
        total_elapsed = sum(p.total_elapsed for p in log_providers)
        typer.echo("")
        typer.echo("  ── LLM 调用统计 " + "─" * 56)
        typer.echo(f"  {'角色':<24}  {'模型':<22}  {'调用次数':>6}  {'Token':>10}  {'耗时(s)':>8}")
        typer.echo("  " + "─" * 80)
        for lp in log_providers:
            if lp.call_count > 0:
                typer.echo(
                    f"  {lp._label:<24}  {lp.model_id:<22}  {lp.call_count:>6}  "
                    f"{lp.total_tokens:>10,}  {lp.total_elapsed:>8.1f}"
                )
        typer.echo("  " + "─" * 80)
        typer.echo(
            f"  {'合计':<24}  {'':22}  {total_calls:>6}  "
            f"{total_tokens:>10,}  {total_elapsed:>8.1f}"
        )

    typer.echo("")
    typer.echo("  产出文件：")
    for fname in [
        "run_trace.json", "feedback.json", "hypotheses.json",
        "evidence_spans.json", "observations.json",
        "conflicts.json", "adjudication_records.json",
    ]:
        p = out_dir / fname
        if p.exists():
            typer.echo(f"    {p}")
    if debug_bundle:
        run_id = trace.get("run_id", "")
        viewer = out_dir / "_debug" / run_id / "viewer" / "index.html"
        if viewer.exists():
            typer.echo(f"    {viewer}")
    typer.echo("=" * 68)

    if not ok:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
