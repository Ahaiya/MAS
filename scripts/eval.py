#!/usr/bin/env python3
"""工程能力评估入口：直接评估 .md / .txt 格式的工程材料。

用法：
  推荐统一入口：
    python -m scripts eval "data/1组—虚拟故居重建计划.md"
  正式脚本入口：
    python scripts/eval.py "data/1组—虚拟故居重建计划.md"
"""

import json
import re
import sys
import tempfile
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import typer

from src.agents.config_resolver import run as resolve_bundle
from src.contracts.artifact_bundle import ProviderEntryConfig
from src.evaluation.runner import run_single_eval
from src.outer_loop.correction_agent import check_and_apply_corrections
from src.providers.factory import build_provider, build_provider_map
from src.providers.logging_provider import LoggingProvider
from src.providers.prompt_loader import PromptLoader

app = typer.Typer(name="eval", help="工程能力评估入口（读取工程材料）。")

_DEFAULT_BUNDLE = _PROJECT_ROOT / "configs" / "bundles" / "engineering_eval_baseline.bundle.yaml"
_DEFAULT_MODEL_CONFIG = _PROJECT_ROOT / "configs" / "model_config.yaml"
_DEFAULT_OUTPUT_BASE = _PROJECT_ROOT / "artifacts"


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
    safe_max = max(1, max_score)
    filled = round(score / safe_max * 10)
    return "█" * filled + "░" * (10 - filled)


def _grade(score: int, max_score: int = 6) -> str:
    if max_score <= 1:
        return "已评分"
    ratio = score / max_score
    if ratio >= 0.83:
        return "优秀"
    if ratio >= 0.67:
        return "良好"
    if ratio >= 0.5:
        return "合格"
    if ratio >= 0.34:
        return "需改进"
    return "薄弱"


def _score(dim: dict) -> int:
    return dim.get("score") or dim.get("canonical_score") or dim.get("final_score") or 0


def _dimension_rows(rubric_snapshot, feedback: dict) -> list[dict]:
    feedback_dims = feedback.get("dimensions", {})
    feedback_dims = feedback_dims if isinstance(feedback_dims, dict) else {}
    scale_max_by_id = {}
    rows = []
    for dim in rubric_snapshot.dimensions:
        dim_id = dim.get("dimension_id")
        if not isinstance(dim_id, str):
            continue
        scale_ref = dim.get("scale_ref")
        scale = rubric_snapshot.scale_by_id.get(scale_ref, {}) if scale_ref else {}
        scale_max = int(scale.get("max", 6))
        scale_max_by_id[dim_id] = scale_max
        rows.append(
            {
                "dimension_id": dim_id,
                "name": dim.get("name", dim_id),
                "code": dim.get("code", ""),
                "scale_max": scale_max,
            }
        )

    seen = {row["dimension_id"] for row in rows}
    for dim_id in feedback_dims:
        if dim_id in seen:
            continue
        rows.append(
            {
                "dimension_id": dim_id,
                "name": dim_id,
                "code": "",
                "scale_max": 6,
            }
        )
    return rows


def _snapshot_stats(log_providers: list) -> tuple[int, int, float]:
    calls = sum(p.call_count for p in log_providers)
    tokens = sum(p.total_tokens for p in log_providers)
    elapsed = sum(p.total_elapsed for p in log_providers)
    return calls, tokens, elapsed


def _resolve_input_file(input_path: Path | None, input_option: Path | None) -> Path:
    if input_path and input_option:
        if input_path != input_option:
            raise typer.BadParameter("请只传一种输入方式：位置参数或 `--input`，不要同时传两个不同路径。")
        return input_path
    resolved = input_path or input_option
    if resolved is None:
        raise typer.BadParameter("请提供待评估文件路径。")
    return resolved


def _derive_sample_id(input_file: Path) -> str:
    stem = input_file.stem.strip()
    match = re.match(r"^(\d+)\s*组(?:\b|[-—_ ].*)?$", stem)
    if match:
        return match.group(1)
    match = re.match(r"^(\d+)", stem)
    if match:
        return match.group(1)
    return stem or "sample"


def _sanitize_path_component(value: str, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    sanitized = re.sub(r'[\\/:*?"<>|]+', "_", text)
    sanitized = sanitized.strip().strip(".")
    return sanitized or fallback


def _normalize_dim_id(dim: str) -> str:
    normalized = dim.strip().lower()
    if not normalized:
        raise typer.BadParameter("`--dim` 不能为空。")
    return normalized


def _materialize_bundle_with_dim_override(bundle: Path, dim: str | None) -> tuple[Path, Path | None]:
    if not dim:
        return bundle, None

    raw = yaml.safe_load(bundle.read_text(encoding="utf-8")) or {}
    raw["active_dim_id"] = _normalize_dim_id(dim)

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".bundle.yaml",
        prefix="mas_eval_",
        delete=False,
        encoding="utf-8",
    ) as fh:
        yaml.safe_dump(raw, fh, allow_unicode=True, sort_keys=False)
        temp_path = Path(fh.name)
    return temp_path, temp_path


def _default_output_dir(resolved, sample_name: str, dim: str) -> Path:
    raw_ctx = resolved.policy_snapshot.scoring_context or {}
    task_name = _sanitize_path_component(
        str(raw_ctx.get("task_name") or resolved.artifact_bundle.metadata.get("active_task_id") or ""),
        fallback="task",
    )
    sample_dir = _sanitize_path_component(sample_name, fallback="sample")
    dim_name = _sanitize_path_component(_normalize_dim_id(dim).upper(), fallback="DIM")
    return _DEFAULT_OUTPUT_BASE / task_name / sample_dir / dim_name


def _task_context_target_file(resolved) -> str:
    source_file = getattr(resolved.artifact_bundle.scoring_context_ref, "source_file", "")
    if source_file:
        return f"configs/{source_file.removeprefix('configs/')}"
    active_task_id = str(resolved.artifact_bundle.metadata.get("active_task_id") or "").strip()
    if active_task_id:
        return f"configs/tasks/{active_task_id}/task_context.yaml"
    return "configs/tasks/task_context.yaml"


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
    ]:
        tpl_path = configs_prompts / filename
        if tpl_path.exists():
            templates[name] = loader.load(tpl_path)

    override_dir = configs_prompts / "evidence_extraction_overrides"
    if override_dir.exists():
        for p in sorted(override_dir.glob("*.yaml")):
            templates[f"evidence_extraction_override_{p.stem}"] = loader.load(p)

    for override_stage in ("scoring", "explanation"):
        od = configs_prompts / f"{override_stage}_overrides"
        if od.exists():
            for p in sorted(od.glob("*.yaml")):
                templates[f"{override_stage}_override_{p.stem}"] = loader.load_with_override(
                    override_stage, p.stem, prompts_root=configs_prompts
                )

    return templates


# ── Provider 构建 ─────────────────────────────────────────────────────────────

def _entry_from_dict(d: dict[str, Any]) -> ProviderEntryConfig:
    return ProviderEntryConfig(
        api_key_env=d.get("api_key_env", "LLM_API_KEY"),
        model=d.get("model", ""),
        api_base=d.get("api_base", ""),
        params=dict(d.get("params") or {}),
    )


def _build_providers_from_model_config(
    path: Path,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """从 configs/model_config.yaml 构建 default / rater / stage providers。

    返回 (default_provider, rater_providers, stage_providers)。
    """
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    default_provider = build_provider(_entry_from_dict(data.get("default") or {}))

    rater_providers = {
        rater_id: build_provider(_entry_from_dict(cfg))
        for rater_id, cfg in (data.get("raters") or {}).items()
    }
    stage_providers = {
        stage: build_provider(_entry_from_dict(cfg))
        for stage, cfg in (data.get("stages") or {}).items()
    }
    return default_provider, rater_providers, stage_providers


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


def _print_agent_hypotheses(hypotheses_path: Path, dimension_rows: list[dict]) -> None:
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
    for row in dimension_rows:
        key = row["dimension_id"]
        if key not in by_dim:
            continue
        name = row["name"]
        code = row["code"]
        dim_scores = by_dim[key]
        scores_str = "  ".join(f"{dim_scores.get(r, '?'):>10}" for r in rater_ids)
        vals = [dim_scores[r] for r in rater_ids if r in dim_scores and isinstance(dim_scores[r], int)]
        diff = max(vals) - min(vals) if len(vals) >= 2 else 0
        diff_str = f"  ⚠️ Δ={diff}" if diff > 1 else ""
        prefix = f"[{code}] " if code else ""
        typer.echo(f"  {prefix}{name:<20}  {scores_str}{diff_str}")


def _print_score_table(feedback: dict, dimension_rows: list[dict]) -> None:
    dims = feedback.get("dimensions", {})
    total_mas = sum(_score(dims[row["dimension_id"]]) for row in dimension_rows if row["dimension_id"] in dims)
    total_max = sum(row["scale_max"] for row in dimension_rows if row["dimension_id"] in dims)
    cinfo = _get_indicator_score_info(feedback, dimension_rows)

    typer.echo("")
    typer.echo("  ── 综合评分 " + "─" * 52)
    typer.echo(f"  {'维度':<24} {'得分':>4}  {'进度条':<12}  {'等级':<5}")
    typer.echo("  " + "─" * 56)
    for row in dimension_rows:
        key = row["dimension_id"]
        if key not in dims:
            continue
        name = row["name"]
        code = row["code"]
        mas = _score(dims[key])
        prefix = f"[{code}] " if code else ""
        typer.echo(
            f"  {prefix}{name:<20} {mas:>4}  {_bar(mas, row['scale_max']):<12}  "
            f"{_grade(mas, row['scale_max']):<5}"
        )
    typer.echo("  " + "─" * 56)
    total_ratio = (total_mas / total_max * 100) if total_max > 0 else 0.0
    typer.echo(f"  {'合计':<24} {total_mas:>4}  满分 {total_max} 分（{total_ratio:.0f}%）")
    if cinfo:
        c_score, c_max, _, label = cinfo
        if c_max is None:
            typer.echo(f"  {label}: {c_score}")
        else:
            c_max_text = str(int(c_max)) if float(c_max).is_integer() else f"{c_max:.2f}"
            typer.echo(f"  {label}: {c_score}/{c_max_text} ({c_score / c_max * 100:.0f}%)")


def _get_indicator_score_payload(feedback: dict) -> dict | None:
    payload = feedback.get("indicator_score")
    return payload if isinstance(payload, dict) and payload else None


def _get_indicator_score_info(feedback: dict, dimension_rows: list[dict]):
    payload = _get_indicator_score_payload(feedback)
    if not payload:
        return None
    score = payload.get("score")
    if score is None:
        return None
    label = str(payload.get("label") or "聚合指标得分")
    return int(score), None, None, label


def _print_dimension_feedback(feedback: dict, dimension_rows: list[dict]) -> None:
    dims = feedback.get("dimensions", {})
    typer.echo("")
    typer.echo("  ── 各维度详细反馈 " + "─" * 46)
    for row in dimension_rows:
        key = row["dimension_id"]
        if key not in dims:
            continue
        name = row["name"]
        code = row["code"]
        dim = dims[key]
        mas = _score(dim)
        prefix = f"[{code}] " if code else ""
        typer.echo(
            f"\n  {prefix}{name}  —  {mas}/{row['scale_max']}  {_grade(mas, row['scale_max'])}"
        )
        for desc in (dim.get("descriptor_refs") or [])[:3]:
            typer.echo(f"     • {desc}")
        text = dim.get("feedback", "") or dim.get("feedback_text", "")
        if text:
            for line in textwrap.wrap(text, width=70):
                typer.echo(f"    {line}")


# ── 主命令 ─────────────────────────────────────────────────────────────────────

@app.command()
def main(
    input_path: Path | None = typer.Argument(
        None,
        metavar="INPUT_FILE",
        help="待评估的工程材料文件（.md 或 .txt）。可直接作为位置参数传入。",
    ),
    input_file: Path | None = typer.Option(
        None, "--input", "-i",
        help="兼容写法：待评估的工程材料文件（.md 或 .txt）。",
    ),
    bundle: Path = typer.Option(
        _DEFAULT_BUNDLE, "--bundle", "-b",
        help="配置 bundle 文件路径。",
    ),
    dim: str = typer.Option(
        "", "--dim",
        help="选择本次评价使用的二级指标维度配置，如 `A4`、`B1`、`C2`。",
    ),
    model_config: Path = typer.Option(
        _DEFAULT_MODEL_CONFIG, "--model-config", "-m",
        help="LLM 分配配置文件（default/raters/stages）。",
    ),
    output_dir: Path = typer.Option(
        None, "--output-dir", "-o",
        help="产出目录（默认自动联动为 artifacts/{task_name}/{sample_name}/{dim}）。",
    ),
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose", "-v",
        help="显示详细内部信息。",
    ),
    debug_bundle: bool = typer.Option(
        True, "--debug-bundle/--no-debug-bundle",
        help="输出调试 bundle（事件流、LLM 请求响应）。默认开启。",
    ),
) -> None:
    """工程能力评估入口：读取工程材料，按所选维度输出评分与反馈。"""

    input_file = _resolve_input_file(input_path, input_file)
    if not input_file.exists():
        typer.echo(f"错误：文件不存在: {input_file}", err=True)
        raise typer.Exit(code=1)

    essay_id = _derive_sample_id(input_file)
    essay_text = input_file.read_text(encoding="utf-8")
    typer.echo(f"[init] 读取文件: {input_file}  ({len(essay_text):,} 字符)")
    typer.echo(f"[init] 样本 ID: {essay_id}")

    # 加载 bundle
    resolved_bundle_path, temp_bundle_path = _materialize_bundle_with_dim_override(bundle, dim.strip())
    typer.echo(f"[init] 加载 bundle: {bundle}")
    if dim.strip():
        typer.echo(f"[init] 维度配置: { _normalize_dim_id(dim).upper() }")
    try:
        resolved = resolve_bundle(resolved_bundle_path)
    finally:
        if temp_bundle_path is not None:
            temp_bundle_path.unlink(missing_ok=True)
    typer.echo(f"[init] {resolved.get_version_info()}")
    typer.echo(f"[init] 量规维度: {[d['dimension_id'] for d in resolved.rubric_snapshot.dimensions]}")

    # 初始化 providers：优先读 model_config，其次 bundle.provider_config，最后降级单 default
    if model_config.exists():
        typer.echo(f"[init] 加载 model_config: {model_config}")
        try:
            default_provider, rater_providers, stage_providers = _build_providers_from_model_config(
                model_config
            )
        except (ValueError, KeyError) as exc:
            typer.echo(f"错误：model_config 读取失败 — {exc}", err=True)
            raise typer.Exit(code=1)
    elif resolved.provider_config is not None:
        try:
            default_provider, rater_providers, stage_providers = build_provider_map(
                resolved.provider_config
            )
        except ValueError as exc:
            typer.echo(f"错误：Provider 配置失败 — {exc}", err=True)
            raise typer.Exit(code=1)
    else:
        try:
            default_provider = build_provider(ProviderEntryConfig(api_key_env="LLM_API_KEY"))
        except ValueError as exc:
            typer.echo(f"错误：{exc}", err=True)
            raise typer.Exit(code=1)
        rater_providers, stage_providers = {}, {}

    default_provider, rater_providers, stage_providers, log_providers = _wrap_providers(
        default_provider, rater_providers, stage_providers
    )

    if log_providers:
        typer.echo("[init] LLM 分配：")
        for lp in log_providers:
            typer.echo(f"         {lp._label:<24} → {lp.model_id}")

    # 加载 prompt 模板
    prompt_templates = _load_prompt_templates()
    typer.echo(f"[init] 加载 {len(prompt_templates)} 个 prompt 模板")

    # 确定输出目录
    effective_dim = dim.strip() or str(resolved.artifact_bundle.metadata.get("active_dim_id") or "")
    out_dir = output_dir if output_dir else _default_output_dir(resolved, essay_id, effective_dim)
    typer.echo(f"[init] 输出目录: {out_dir}")

    # ── 内环启动前检查：处理待应用的人类批改意见 ──────────────────────────────
    try:
        correction_provider = build_provider(
            ProviderEntryConfig(
                api_key_env="OUTER_LOOP_API_KEY",
                model=__import__("os").environ.get("OUTER_LOOP_MODEL", "").strip(),
                api_base=__import__("os").environ.get("OUTER_LOOP_API_BASE", "").strip(),
            )
        )
        applied = check_and_apply_corrections(
            provider=correction_provider,
            configs_root=_PROJECT_ROOT / "configs",
            snapshots_root=_PROJECT_ROOT / "experiments" / "snapshots",
            target_file=_task_context_target_file(resolved),
        )
        if applied:
            typer.echo("[correction] 已应用教师批改意见，配置已更新，重新加载 bundle")
            resolved_bundle_path, temp_bundle_path = _materialize_bundle_with_dim_override(
                bundle, dim.strip()
            )
            try:
                resolved = resolve_bundle(resolved_bundle_path)
            finally:
                if temp_bundle_path is not None:
                    temp_bundle_path.unlink(missing_ok=True)
    except Exception as exc:
        typer.echo(f"[correction] 跳过：{exc}")

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

    dimension_rows = _dimension_rows(resolved.rubric_snapshot, feedback)

    if verbose and ok:
        _print_node_timeline(trace)
        _print_agent_hypotheses(out_dir / "hypotheses.json", dimension_rows)
        _print_score_table(feedback, dimension_rows)
        _print_dimension_feedback(feedback, dimension_rows)

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
