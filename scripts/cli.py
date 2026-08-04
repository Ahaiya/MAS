#!/usr/bin/env python3
"""MAS 命令行入口——单文件 CLI

用法：
  python scripts/cli.py parse a.pdf b.pptx --task experiment --submission 2025213223
                                                                 # 解析一次提交的全部材料
  python scripts/cli.py eval --task experiment --submission 2025213223 --dim a4
                                                                 # 评单个二级指标
  python scripts/cli.py eval --task experiment --submission 2025213223
                                                                 # 评该任务下全部二级指标
  python scripts/cli.py config validate --task experiment
                                                                 # 校验配置能否加载
  python scripts/cli.py prompt --task experiment --submission 2025213223 --dim d1
                                                                 # 回看那次运行发给模型的 prompt

"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional

import typer

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import yaml
from dotenv import load_dotenv

from src.config.compiler import (
    PROMPT_STAGES,
    ConfigCompileError,
    list_task_dimension_ids,
    load_adjudication_policy,
    load_dimension_rubric,
    prompt_path,
)
from src.agents.prompt_builders import (
    build_adjudication_prompt,
    build_feedback_prompt,
    build_rater_extraction_prompt,
    build_rater_scoring_prompt,
    build_rater_select_prompt,
)
from src.artifacts import dim_dir
from src.contracts.configuration import RubricSnapshot
from src.contracts.package import DataPackage
from src.contracts.scoring import DimensionScore, FinalDecision, RaterChainResult, ScoreSource
from src.engine import DimensionEvaluation, Engine
from src.engine_config import (
    EngineConfigError,
    load_context_budget_tokens,
    load_runtime_config,
    validate_model_config,
)
from src.parse.config import ParseConfigError, load_parse_config, require_credentials
from src.parse.docmind import DocMindError, sdk_caller
from src.parse.pipeline import SubmissionParseError, package_path, parse_submission
from src.providers.prompt_loader import PromptLoader, PromptTemplate

load_dotenv()

_DEFAULT_CONFIGS_ROOT = _PROJECT_ROOT / "configs"
_DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "artifacts"
# 解析结果是花钱买来的**输入**，与 artifacts/（觉得不对就整个删掉重跑的产出）分开放。
_DEFAULT_PACKAGES_ROOT = _PROJECT_ROOT / "packages"

app = typer.Typer(name="mas", help="MAS 评价引擎命令行入口。", add_completion=False)
config_app = typer.Typer(name="config", help="配置校验工具。", add_completion=False)
app.add_typer(config_app, name="config")


# 用户能自己修的配置类错误（改 yaml、补 .env、纠正 --dim 拼写）统一按"终端提示"
# 处理，不甩 traceback；不在这张网里的异常照常向上抛，别把 bug 藏起来。
# 注意：新增的校验必须抛这些类型之一，否则一个 yaml 笔误就会变成一屏 traceback。
_USER_FIXABLE_ERRORS = (
    EngineConfigError,
    ConfigCompileError,
    ParseConfigError,
    DocMindError,
    FileNotFoundError,
    yaml.YAMLError,
    KeyError,
)


def _exit_with_error(message: str) -> typer.Exit:
    """打印错误到 stderr，返回给调用方 raise 的 Exit(1)。"""
    typer.echo(f"ERROR: {message}", err=True)
    return typer.Exit(code=1)


def _available_tasks(configs_root: Path) -> List[str]:
    """列出 `{configs_root}/tasks/` 下的任务 id（有 dimension 子目录的才算）。"""
    tasks_dir = configs_root / "tasks"
    if not tasks_dir.is_dir():
        return []
    return sorted(p.name for p in tasks_dir.iterdir() if (p / "dimension").is_dir())


def _require_task(configs_root: Path, task: Optional[str]) -> str:
    """任务必须显式指定——没有默认值，也不沿用任何配置文件里的值。"""
    if not configs_root.is_dir():
        raise _exit_with_error(f"配置目录不存在：{configs_root}")

    available = _available_tasks(configs_root)
    if task is None:
        hint = "、".join(available) if available else "（该目录下没有任何任务）"
        raise _exit_with_error(f"必须用 --task 指定任务。可选：{hint}")
    if task not in available:
        hint = "、".join(available) if available else "（该目录下没有任何任务）"
        raise _exit_with_error(f"任务 '{task}' 不存在于 {configs_root / 'tasks'}。可选：{hint}")
    return task


# ── eval ─────────────────────────────────────────────────────────────────────


def _render_summary(results: Dict[str, DimensionEvaluation]) -> str:
    """把 evaluate() 的返回渲染成给人看的摘要。

    失败隔离 下被跳过的观测点必须一并打印——否则用户会以为所有观测点都评过，
    而实际上 feedback.json 里少了几个观测点。"""
    lines: List[str] = []
    for dim_id in sorted(results):
        evaluation = results[dim_id]
        report = evaluation.feedback_report
        trace = evaluation.run_trace

        # primary_score 为 None = 该二级指标下全部观测点都失败，没评出分；
        # 与"评了、得低分"是两回事，不能印成 0.00。
        primary_score = report["primary_score"]
        headline = "全部观测点评价失败" if primary_score is None else f"{primary_score:.2f}"
        lines.append(f"[{dim_id}] 二级指标分：{headline}")
        for code, entry in sorted(report["dimensions"].items()):
            lines.append(f"  {code}: {entry['final_score']}  ({entry['source']})")
        for failed in trace.failed_codes:
            lines.append(f"  {failed['code']}: 评价失败 — {failed['error']}")
        lines.append(f"  tokens={trace.total_tokens}  耗时={trace.total_ms / 1000:.1f}s")
    return "\n".join(lines)


def _load_package(packages_root: Path, task: str, submission: str) -> DataPackage:
    """按约定去 `packages/{task}/{submission}/package.json` 找包——不做引用解析。

    包不存在就是「还没解析过」，报一行人话让人先跑 parse；不在这里顺手替他解析：
    那会把网络/配额失败请回评价链路，还藏起「这一步要花钱」这个事实。"""
    path = package_path(packages_root, task, submission)
    if not path.exists():
        raise _exit_with_error(
            f"找不到数据包：{path}。先解析这次提交："
            f"`python scripts/cli.py parse <文件...> --task {task} --submission {submission}`"
        )
    return DataPackage.from_dict(json.loads(path.read_text(encoding="utf-8")))


@app.command("eval")
def eval_command(
    task: Annotated[
        Optional[str],
        typer.Option("--task", help="任务 id，对应 configs/tasks/<task>/。"),
    ] = None,
    submission: Annotated[
        Optional[str],
        typer.Option("--submission", help="提交标识（学号），对应 packages/<task>/<submission>/。"),
    ] = None,
    configs: Annotated[
        Path,
        typer.Option("--configs", help="配置根目录。"),
    ] = _DEFAULT_CONFIGS_ROOT,
    dim: Annotated[
        Optional[str],
        typer.Option("--dim", help="二级指标（如 a4）；缺省评该任务下全部二级指标。"),
    ] = None,
    packages: Annotated[
        Path,
        typer.Option("--packages", help="解析结果根目录（parse 的产出）。"),
    ] = _DEFAULT_PACKAGES_ROOT,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="产物落盘根目录。"),
    ] = _DEFAULT_OUTPUT_DIR,
) -> None:
    """评价一次提交：读数据包 → 双链评价 → 仲裁 → 反馈，产物按 {task}/{submission}/{dim}/ 落盘。"""
    task_id = _require_task(configs, task)
    if not submission:
        raise _exit_with_error("必须用 --submission 指定这次评价的是哪份提交。")
    package = _load_package(packages, task_id, submission)

    try:
        engine = Engine.from_configs(configs, task_id, output_dir=output_dir)
        results = engine.evaluate(package, dim=dim)
    except _USER_FIXABLE_ERRORS as exc:
        raise _exit_with_error(f"{type(exc).__name__}: {exc}") from exc

    typer.echo(_render_summary(results))
    typer.echo(f"产物已写入：{output_dir}")

    # 一个分都没评出来时退非零：脚本/CI 才能发现这次"跑完了"其实什么都没产出。
    if all(not r.feedback_report["dimensions"] for r in results.values()):
        raise _exit_with_error("本次评价没有产出任何观测点分数——见上方各观测点失败原因。")


# ── parse ────────────────────────────────────────────────────────────────────


@app.command("parse")
def parse_command(
    files: Annotated[
        List[Path],
        typer.Argument(metavar="FILES...", help="这次提交的**全部**源文件（PDF/Word/PPT/图片…）。"),
    ],
    task: Annotated[
        Optional[str],
        typer.Option("--task", help="任务 id，对应 configs/tasks/<task>/。"),
    ] = None,
    submission: Annotated[
        Optional[str],
        typer.Option("--submission", help="提交标识（学号），产出落在 packages/<task>/<submission>/。"),
    ] = None,
    configs: Annotated[
        Path,
        typer.Option("--configs", help="配置根目录（解析配置读 <configs>/parse.yaml）。"),
    ] = _DEFAULT_CONFIGS_ROOT,
    packages: Annotated[
        Path,
        typer.Option("--packages", help="解析结果根目录。"),
    ] = _DEFAULT_PACKAGES_ROOT,
    force: Annotated[
        bool,
        typer.Option("--force", help="已有解析原件也重新解析（会重新付费）。"),
    ] = False,
) -> None:
    """解析一次提交的全部材料，产出 packages/{task}/{submission}/{raw/,package.json}。

    一次提交的所有文件共享同一编号空间；**任一文件失败则整个提交失败**，不产出数据包"""
    task_id = _require_task(configs, task)
    if not submission:
        raise _exit_with_error("必须用 --submission 指定这批材料属于哪份提交。")

    missing = [str(f) for f in files if not Path(f).exists()]
    if missing:
        raise _exit_with_error(f"源文件不存在：{'、'.join(missing)}")

    try:
        parse_config = load_parse_config(configs / "parse.yaml")
        credentials = require_credentials()  # 密钥缺失当场报错，不等到传完文件才失败
        package = parse_submission(
            files,
            task=task_id,
            submission=submission,
            packages_root=packages,
            config=parse_config,
            call=sdk_caller(parse_config, credentials),
            force=force,
        )
    except SubmissionParseError as exc:
        raise _exit_with_error(str(exc)) from exc
    except ValueError as exc:  # 同名文件等输入问题，改个文件名就能修
        raise _exit_with_error(str(exc)) from exc
    except _USER_FIXABLE_ERRORS as exc:
        raise _exit_with_error(f"{type(exc).__name__}: {exc}") from exc

    excluded = package.provenance["excluded_layouts"]
    typer.echo(f"解析完成：{len(package.units)} 个单元，来自 {len(files)} 个文件。")
    if excluded:
        detail = "、".join(f"{name}×{count}" for name, count in sorted(excluded.items()))
        typer.echo(f"已剔除的版面块（页眉页脚等，不产生单元）：{detail}")
    typer.echo(f"数据包已写入：{package_path(packages, task_id, submission)}")


# ── config validate ──────────────────────────────────────────────────────────


def _validate_configs(configs_root: Path, task_id: str) -> List[str]:
    """按约定路径走一遍全部配置，返回给人看的 OK 行；任何一处解析不了就抛错。

    刻意不建 provider：配置是否自洽与密钥是否就位是两件事，`config validate` 只答
    前一件（含 model_config 的结构），因此没有 .env 也能在 CI 里跑。路径解析与
    仲裁策略加载都与 Engine 共用同一批函数，避免"校验通过但引擎跑不起来"。"""
    lines = [f"OK  task           : {task_id}"]

    policy = load_adjudication_policy(configs_root)
    lines.append(
        f"OK  adjudication   : score_gap>{policy.score_gap_threshold} "
        f"drift>={policy.drift_min_dimensions}"
    )

    loader = PromptLoader()
    for stage in PROMPT_STAGES:
        path = prompt_path(configs_root, stage)
        if not path.exists():
            raise ConfigCompileError(f"提示词文件不存在：{path}")
        loader.load(path)  # 顺带校验 prompt yaml 的结构
        lines.append(f"OK  prompt         : {stage}")

    dim_ids = list_task_dimension_ids(configs_root, task_id)
    if not dim_ids:
        raise ConfigCompileError(f"任务 '{task_id}' 下没有任何 *_rubric.yaml——无可评的二级指标。")
    for dim_id in dim_ids:
        rubric = load_dimension_rubric(configs_root, task_id, dim_id)
        codes = [str(d["code"]) for d in rubric.dimensions]
        lines.append(
            f"OK  二级指标       : {dim_id}"
            f"（{len(codes)} 个观测点：{', '.join(codes)}）"
        )

    # model_config 是模型/参数的唯一来源且必填项不少，一并校验结构——否则漏填
    # model 要等到真跑评价、建 provider 时才炸。只看字段在不在，不读密钥、不建
    # provider，因此 CI 里没有 .env 也能跑。
    entries = validate_model_config(configs_root / "model_config.yaml")
    for name, entry in sorted(entries.items()):
        lines.append(f"OK  provider       : {name} → {entry.model} @ {entry.api_base}")
    max_workers, retry = load_runtime_config(configs_root / "model_config.yaml")
    budget_tokens = load_context_budget_tokens(configs_root / "model_config.yaml")
    lines.append(
        f"OK  runtime        : max_workers={max_workers} timeout={retry.timeout_seconds:g}s "
        f"retries={retry.max_retries} context_budget={budget_tokens}"
    )

    return lines


@config_app.command("validate")
def config_validate(
    task: Annotated[
        Optional[str],
        typer.Option("--task", help="任务 id，对应 configs/tasks/<task>/。"),
    ] = None,
    configs: Annotated[
        Path,
        typer.Option("--configs", help="配置根目录。"),
    ] = _DEFAULT_CONFIGS_ROOT,
) -> None:
    """校验配置：仲裁策略 / 提示词 / 任务下各二级指标量规 / model_config 都能加载。"""
    task_id = _require_task(configs, task)

    try:
        lines = _validate_configs(configs, task_id)
    except (ConfigCompileError, EngineConfigError, ValueError, FileNotFoundError, yaml.YAMLError) as exc:
        raise _exit_with_error(str(exc)) from exc

    for line in lines:
        typer.echo(line)
    typer.echo("PASS: 配置校验通过。")


# ── prompt show ──────────────────────────────────────────────────────────────


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise _exit_with_error(f"产物不存在：{path}——先跑一次 `cli.py eval` 再看 prompt。")
    return json.loads(path.read_text(encoding="utf-8"))


def _chain_from_dict(data: Dict[str, Any]) -> RaterChainResult:
    """把 rater_chains.json 里的一条链读回 RaterChainResult（to_dict 的逆操作）。"""
    return RaterChainResult(
        rater_id=str(data["rater_id"]),
        code=str(data["code"]),
        selected_unit_ids=list(data["selected_unit_ids"]),
        evidence_unit_ids=list(data["evidence_unit_ids"]),
        score=DimensionScore(
            score=int(data["score"]),
            supporting_unit_ids=list(data["supporting_unit_ids"]),
            rationale=str(data["rationale"]),
            confidence=float(data["confidence"]),
        ),
    )


def _decision_from_dict(data: Dict[str, Any]) -> FinalDecision:
    return FinalDecision(
        code=str(data["code"]),
        final_score=int(data["final_score"]),
        source=ScoreSource(data["source"]),
        unit_ids=list(data["unit_ids"]),
        rationale=str(data["rationale"]),
    )


def _render_stage_prompts(
    package: DataPackage,
    rubric: RubricSnapshot,
    dimension: Dict[str, Any],
    templates: Dict[str, PromptTemplate],
    chains: List[RaterChainResult],
    decision: FinalDecision,
) -> Dict[str, str]:
    """按流水线顺序渲染五个阶段的 prompt，用的是各阶段真正调用的那个构造函数。

    chains[0] 决定 extract/score 看到的单元（该 Rater 上一步真实选出的编号）；
    仲裁看两条链，反馈看最终决策——全部来自 rater_chains.json，无一处代填。"""
    levels = rubric.scale_levels
    chain = chains[0]
    return {
        "select": build_rater_select_prompt(
            package, dimension, levels, templates["select"], rubric.indicator_description
        ),
        "extract": build_rater_extraction_prompt(
            package, chain.selected_unit_ids, dimension, levels, templates["extraction"],
            rubric.indicator_description,
        ),
        "score": build_rater_scoring_prompt(
            package, chain.evidence_unit_ids, dimension, levels, templates["scoring"]
        ),
        "adjudicate": build_adjudication_prompt(
            package, dimension, levels, chains[0], chains[-1], templates["adjudication"]
        ),
        "feedback": build_feedback_prompt(
            package, decision, dimension, levels, templates["feedback"]
        ),
    }


@app.command("prompt")
def prompt_command(
    dim: Annotated[str, typer.Option("--dim", help="二级指标（如 d1）。")],
    task: Annotated[
        Optional[str],
        typer.Option("--task", help="任务 id，对应 configs/tasks/<task>/。"),
    ] = None,
    submission: Annotated[
        Optional[str],
        typer.Option("--submission", help="提交标识（学号），如 2025213223。"),
    ] = None,
    code: Annotated[
        Optional[str],
        typer.Option("--code", help="观测点 code（如 D1-1）；缺省取该二级指标下第一个。"),
    ] = None,
    stage: Annotated[
        Optional[str],
        typer.Option("--stage", help="只看某个阶段：select/extract/score/adjudicate/feedback。"),
    ] = None,
    rater: Annotated[
        str,
        typer.Option("--rater", help="extract/score 用哪条链的真实选段/证据编号。"),
    ] = "rater_1",
    configs: Annotated[
        Path,
        typer.Option("--configs", help="配置根目录。"),
    ] = _DEFAULT_CONFIGS_ROOT,
    packages: Annotated[
        Path,
        typer.Option("--packages", help="解析结果根目录，单元全文从这里读。"),
    ] = _DEFAULT_PACKAGES_ROOT,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="产物根目录，从这里读回这次运行的真实数据。"),
    ] = _DEFAULT_OUTPUT_DIR,
) -> None:
    """打印某次真实运行里各阶段进模型的 prompt 全文——不调 LLM，也不代填任何输入。

    单元全文读 package.json，选段/证据编号与双链读 rater_chains.json，最终分读
    final_decisions——都是那次运行模型真实产出的值，因此渲染出来的就是当时发出去
    的文本。前提是这份提交 + 二级指标已经 eval 过一次。"""
    task_id = _require_task(configs, task)
    if not submission:
        raise _exit_with_error("必须用 --submission 指定看的是哪份提交。")
    try:
        rubric = load_dimension_rubric(configs, task_id, dim)
        loader = PromptLoader()
        templates = {s: loader.load(prompt_path(configs, s)) for s in PROMPT_STAGES}
    except _USER_FIXABLE_ERRORS as exc:
        raise _exit_with_error(str(exc)) from exc

    package = _load_package(packages, task_id, submission)
    rater_chains = _read_json(dim_dir(output_dir, task_id, submission, dim) / "rater_chains.json")

    target_code = code or str(rubric.dimensions[0]["code"])
    dimension = rubric.get_dimension(target_code)
    if dimension is None:
        available = "、".join(str(d["code"]) for d in rubric.dimensions)
        raise _exit_with_error(f"观测点 '{target_code}' 不在 {dim} 的量规里。可选：{available}")

    chains = [_chain_from_dict(c) for c in rater_chains["chains"] if c["code"] == target_code]
    if not chains:
        raise _exit_with_error(f"rater_chains.json 里没有观测点 '{target_code}' 的链——它这次评价失败了？")
    # 指定的 rater 排到首位：extract/score 看的是这条链上一步的真实输出。
    chains.sort(key=lambda c: c.rater_id != rater)
    decisions = [
        _decision_from_dict(d) for d in rater_chains["final_decisions"] if d["code"] == target_code
    ]
    if not decisions:
        raise _exit_with_error(f"rater_chains.json 里没有观测点 '{target_code}' 的最终决策。")

    prompts = _render_stage_prompts(
        package, rubric, dimension, templates, chains, decisions[0]
    )
    if stage is not None:
        if stage not in prompts:
            raise _exit_with_error(f"未知阶段 '{stage}'。可选：{'、'.join(prompts)}")
        prompts = {stage: prompts[stage]}

    for name, text in prompts.items():
        typer.echo("=" * 78)
        typer.echo(
            f"  {task_id} / {submission} / {dim} / {target_code} / stage={name}"
            f"  （{len(text)} 字符，{chains[0].rater_id} 链）"
        )
        typer.echo("=" * 78)
        typer.echo(text)
        typer.echo("")


if __name__ == "__main__":
    app()
