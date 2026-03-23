#!/usr/bin/env python3
"""Batch evaluate essays from the ASAP TSV file using real LLM providers.

For each essay, runs the full MAS pipeline and saves three artifact files:
  artifacts/eval/{essay_id}/feedback.json
  artifacts/eval/{essay_id}/run_trace.json
  artifacts/eval/{essay_id}/hypotheses.json

Already-evaluated essays are skipped by default (idempotent). Use --force to re-run.

Usage:
  # Evaluate the first 10 essays
  python scripts/run_batch_eval.py --limit 10

  # Evaluate specific essays
  python scripts/run_batch_eval.py --essay-ids 20716,20717,20718

  # Re-run all (overwrite existing)
  python scripts/run_batch_eval.py --force

  # Reduce API delay (careful with rate limits)
  python scripts/run_batch_eval.py --limit 20 --delay 3
"""

import csv
import json
import sys
import time
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

app = typer.Typer(
    name="run-batch-eval",
    help="Batch evaluate ASAP Set 8 essays using real LLM providers.",
)

_DEFAULT_SOURCE = _PROJECT_ROOT / "data" / "training_set_8.tsv"
_DEFAULT_BUNDLE = _PROJECT_ROOT / "configs" / "bundles" / "asap_set8_baseline.bundle.yaml"
_DEFAULT_OUTPUT_BASE = _PROJECT_ROOT / "artifacts" / "eval"

# Fixed dimension order for score display
_DIM_ORDER = [
    "ideas_content",
    "organization",
    "voice",
    "word_choice",
    "sentence_fluency",
    "conventions",
]


def _load_essays(
    tsv_path: Path,
    limit: int,
    essay_ids: list[int],
) -> list[tuple[str, str]]:
    """Return list of (essay_id_str, essay_text) pairs from the TSV.

    Args:
        tsv_path: Path to the training TSV file.
        limit: Maximum number of essays to return (0 = no limit).
        essay_ids: If non-empty, only return rows matching these IDs.
    """
    id_set = {str(i) for i in essay_ids} if essay_ids else None
    results: list[tuple[str, str]] = []

    with open(tsv_path, encoding="latin-1") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            eid = row.get("essay_id", "").strip()
            if not eid:
                continue
            if id_set and eid not in id_set:
                continue
            results.append((eid, row["essay"]))
            if limit > 0 and len(results) >= limit:
                break

    return results


def _wrap_providers(
    default_provider,
    rater_providers: dict,
    stage_providers: dict,
) -> tuple:
    """Wrap all providers with LoggingProvider; return (default, raters, stages, log_list).

    log_list holds every LoggingProvider created so callers can read cumulative stats.
    """
    log_providers: list[LoggingProvider] = []

    if default_provider is not None:
        wrapped_default = LoggingProvider(default_provider, label="default")
        log_providers.append(wrapped_default)
    else:
        wrapped_default = None

    wrapped_raters = {}
    for rid, p in rater_providers.items():
        lp = LoggingProvider(p, label=rid)
        wrapped_raters[rid] = lp
        log_providers.append(lp)

    wrapped_stages = {}
    for stage, p in stage_providers.items():
        lp = LoggingProvider(p, label=stage)
        wrapped_stages[stage] = lp
        log_providers.append(lp)

    return wrapped_default, wrapped_raters, wrapped_stages, log_providers


def _snapshot_stats(log_providers: list[LoggingProvider]) -> tuple[int, int, float]:
    """Return (total_calls, total_tokens, total_elapsed) summed across all wrappers."""
    calls = sum(p.call_count for p in log_providers)
    tokens = sum(p.total_tokens for p in log_providers)
    elapsed = sum(p.total_elapsed for p in log_providers)
    return calls, tokens, elapsed


def _score_summary(feedback: dict) -> str:
    """Return a compact score string like '4 3 4 3 3 3' for terminal display."""
    dims = feedback.get("dimensions", {})
    parts = []
    for dim_id in _DIM_ORDER:
        if dim_id not in dims:
            parts.append("?")
            continue
        d = dims[dim_id]
        score = d.get("canonical_score") or d.get("final_score") or "?"
        parts.append(str(score))
    return " ".join(parts)


@app.command()
def main(
    source: Path = typer.Option(
        _DEFAULT_SOURCE,
        "--source", "-s",
        help="Path to training TSV file.",
    ),
    bundle: Path = typer.Option(
        _DEFAULT_BUNDLE,
        "--bundle", "-b",
        help="Path to configuration bundle YAML file.",
    ),
    output_dir: Path = typer.Option(
        _DEFAULT_OUTPUT_BASE,
        "--output-dir", "-o",
        help="Root directory for per-essay artifact output.",
    ),
    limit: int = typer.Option(
        0,
        "--limit", "-n",
        help="Maximum number of essays to process (0 = all).",
    ),
    essay_ids: str = typer.Option(
        "",
        "--essay-ids",
        help="Comma-separated essay IDs to evaluate, e.g. '20716,20717'.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-evaluate even if artifacts already exist.",
    ),
    delay: float = typer.Option(
        5.0,
        "--delay",
        help="Seconds to wait between essays (reduces API rate-limit risk).",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose", "-v",
        help="Print each LLM call: model, prompt size, elapsed time, token usage.",
    ),
) -> None:
    """Batch evaluate ASAP Set 8 essays with the full MAS pipeline."""

    # Parse essay_ids option
    id_list = (
        [int(x.strip()) for x in essay_ids.split(",") if x.strip().isdigit()]
        if essay_ids
        else []
    )

    # ── Load essays from TSV ──────────────────────────────────────────────────
    typer.echo(f"[init] 读取 TSV: {source}")
    essays = _load_essays(source, limit, id_list)
    if not essays:
        typer.echo("错误：未找到符合条件的文章", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"[init] 共 {len(essays)} 篇文章待处理")

    # ── Initialise bundle + providers once (outside the loop) ─────────────────
    typer.echo(f"[init] 加载 bundle: {bundle}")
    resolved = resolve_bundle(bundle)
    typer.echo(f"[init] {resolved.get_version_info()}")

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
        typer.echo(
            f"[init] provider_config: {len(rater_providers)} rater(s), "
            f"{len(stage_providers)} stage(s)"
        )
    else:
        legacy_entry = ProviderEntryConfig(api_key_env="LLM_API_KEY")
        try:
            default_provider = build_provider(legacy_entry)
        except ValueError as exc:
            typer.echo(f"错误：{exc}", err=True)
            raise typer.Exit(code=1)

    loader = PromptLoader()
    configs_prompts = _PROJECT_ROOT / "configs" / "prompts"
    prompt_templates: dict = {}
    for name, filename in [
        ("evidence_extraction", "evidence_extraction.yaml"),
        ("scoring", "scoring.yaml"),
        ("explanation", "explanation.yaml"),
    ]:
        tpl_path = configs_prompts / filename
        if tpl_path.exists():
            prompt_templates[name] = loader.load(tpl_path)
    typer.echo(f"[init] 加载 {len(prompt_templates)} 个 prompt 模板")

    # ── Wrap providers with LoggingProvider when --verbose ────────────────────
    log_providers: list[LoggingProvider] = []
    if verbose:
        default_provider, rater_providers, stage_providers, log_providers = (
            _wrap_providers(default_provider, rater_providers, stage_providers)
        )
        typer.echo("[init] verbose 模式：已启用 LLM 调用日志")

    # ── Batch loop ────────────────────────────────────────────────────────────
    results: dict[str, list[str]] = {"success": [], "skipped": [], "failed": []}
    total = len(essays)

    typer.echo("=" * 64)
    for i, (essay_id, essay_text) in enumerate(essays, 1):
        essay_out = Path(output_dir) / essay_id
        feedback_path = essay_out / "feedback.json"

        # Idempotency: skip if artifacts already exist
        if feedback_path.exists() and not force:
            typer.echo(f"[{i:>4}/{total}] SKIP  {essay_id}  (已存在)")
            results["skipped"].append(essay_id)
            continue

        typer.echo(f"[{i:>4}/{total}] 开始  {essay_id}  ({len(essay_text)} 字符)")
        essay_out.mkdir(parents=True, exist_ok=True)

        # Snapshot cumulative stats before this essay (for per-essay delta)
        stats_before = _snapshot_stats(log_providers) if verbose else (0, 0, 0.0)

        try:
            request = EvaluationRequest(
                raw_text=essay_text,
                bundle_ref=(
                    f"{resolved.artifact_bundle.bundle_id}"
                    f"@{resolved.artifact_bundle.bundle_version}"
                ),
            )
            runner = PipelineRunner(
                resolved,
                provider=default_provider,
                rater_providers=rater_providers,
                stage_providers=stage_providers,
                prompt_templates=prompt_templates,
            )
            run_trace, feedback = runner.run(request)

            # Save all three artifact files
            (essay_out / "run_trace.json").write_text(
                json.dumps(run_trace.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            (essay_out / "feedback.json").write_text(
                json.dumps(feedback, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            (essay_out / "hypotheses.json").write_text(
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

            status = run_trace.status.value
            if status == "completed":
                scores = _score_summary(feedback)
                if verbose:
                    c0, t0, e0 = stats_before
                    c1, t1, e1 = _snapshot_stats(log_providers)
                    typer.echo(
                        f"         ✅  完成  [{scores}]"
                        f"  calls={c1 - c0}  tokens={t1 - t0:,}  llm_elapsed={e1 - e0:.1f}s"
                    )
                else:
                    typer.echo(f"         ✅  完成  [{scores}]")
                results["success"].append(essay_id)
            else:
                typer.echo(f"         ❌  流水线状态: {status}", err=True)
                for node in run_trace.node_traces:
                    if node.status.value != "success":
                        typer.echo(
                            f"              失败节点: {node.node_id} — {node.error_message}",
                            err=True,
                        )
                results["failed"].append(essay_id)

        except Exception as exc:
            typer.echo(f"         ❌  异常: {exc}", err=True)
            results["failed"].append(essay_id)

        # Rate-limiting delay (skip after the last essay)
        if i < total and delay > 0:
            time.sleep(delay)

    # ── Summary ───────────────────────────────────────────────────────────────
    typer.echo("=" * 64)
    typer.echo(
        f"完成  总计: {total}  "
        f"✅ 成功: {len(results['success'])}  "
        f"⏭  跳过: {len(results['skipped'])}  "
        f"❌ 失败: {len(results['failed'])}"
    )
    if verbose and log_providers:
        total_calls, total_tokens, total_llm_elapsed = _snapshot_stats(log_providers)
        typer.echo(
            f"LLM 汇总  总调用: {total_calls}  "
            f"总 token: {total_tokens:,}  "
            f"LLM 累计耗时: {total_llm_elapsed:.1f}s"
        )
        # Per-provider breakdown
        for lp in log_providers:
            if lp.call_count > 0:
                typer.echo(
                    f"  {lp._label:<22}  calls={lp.call_count:<4}  "
                    f"tokens={lp.total_tokens:>8,}  elapsed={lp.total_elapsed:>7.1f}s"
                )
    if results["failed"]:
        typer.echo(f"失败列表: {', '.join(results['failed'])}", err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
