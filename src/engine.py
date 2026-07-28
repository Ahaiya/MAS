"""
Engine Facade：`Engine.from_configs(root, task_id).evaluate(package, dim)` 跑通一次完整评价。

流水线：rate(r1) → rate(r2) → reconcile → [adjudicate] → feedback.
同一 sample 下各二级指标的双链评价用 ThreadPoolExecutor 并发跑（provider IO 密集，GIL 不碍事）
上限 `max_workers` 从 model_config.yaml 的 runtime 段读取，默认 8。
segment阶段发生在 Engine.evaluate() 之外——engine 只认「量规 + 已切分好的DataPackage」，
数据包的来源（read_text_file() 或未来的多源解析接入层）不是它的关心范围。

model_config.yaml 是模型/参数的唯一来源：`providers` 缺 rater_1/rater_2/feedback
直接报错，条目缺 model/api_base/api_key_env 也直接报错，没有任何环境变量兜底。
密钥值只从 .env 读，配置里只存环境变量的名字。rater_3 允许缺失：只在真正触发
仲裁时才需要，报错逻辑在 reconcile.py 里。

trace 用收集器模式：每次调用 select/extract/score/adjudicate/feedback 都在
一层 provider 包装器上记录耗时与 token 用量，engine 在调用前后各拍一次快照
做差，不侵入 rater.py/adjudicator.py/feedback.py 内部。并发下同一个 provider
实例被多个线程共享，包装器按线程隔离计数（threading.local）而非加锁，快照
差值天然只反映当前线程自己发起的调用。

失败隔离：一个二级指标的双链评价（select→extract→score ×2）失败（LLM 报错/
超时/越界证据）只把该二级指标计入 run_trace 的 failed_dims，不参与 reconcile/
feedback，也不中断其余二级指标；一级指标内其余维度照常产出。全部二级指标都失败
时短路掉 reconcile/feedback，产出 primary_score=None 的空评价（失败原因仍逐条在
run_trace 里），同样不抛异常——抛了就会把同一 sample 下其余一级指标一起带走。
除此之外错误直接抛出：reconcile/feedback 等阶段本身失败仍会中断当前一级指标的
评价，没有状态机回退重入。"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.agents import feedback, rater, reconcile
from src.artifacts import (
    write_feedback_artifact,
    write_package_artifact,
    write_rater_chains_artifact,
    write_run_trace_artifact,
)
from src.config.compiler import (
    PROMPT_STAGES,
    ConfigCompileError,
    list_task_dimension_ids,
    load_adjudication_policy,
    load_dimension_rubric,
    prompt_path,
)
from src.contracts.artifact_bundle import PolicySnapshot, RubricSnapshot
from src.contracts.package import DataPackage
from src.contracts.scoring import RaterChainResult, ScoreSource
from src.contracts.trace import RunTraceSummary, StageTrace
from src.engine_config import (
    DEFAULT_MAX_WORKERS,
    EngineConfigError,
    load_providers_from_model_config,
    load_runtime_config,
)
from src.providers.base import BaseProvider
from src.providers.instrumented import InstrumentedProvider, call_with_trace
from src.providers.prompt_loader import PromptLoader, PromptTemplate

_REQUIRED_PROVIDERS = frozenset({"rater_1", "rater_2", "feedback"})
_REQUIRED_TEMPLATES = frozenset({"select", "extraction", "scoring", "feedback"})
# ── trace 收集：provider 包装器 + 调用计时 ─────────────────────────────────────


# ── Engine ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DimensionEvaluation:
    """一次一级指标评价的完整产物（同时也是落盘到 dim 层的三份 JSON 的内容）。"""

    dim_id: str
    feedback_report: Dict[str, Any]
    rater_chains_report: Dict[str, Any]
    run_trace: RunTraceSummary


class Engine:
    """`量规 + 数据包 → 评价` Facade。用 `Engine.from_configs(root, task_id)` 构造。"""

    def __init__(
        self,
        *,
        configs_ref: str,
        active_task_id: str,
        configs_root: Path,
        policy: PolicySnapshot,
        templates: Dict[str, PromptTemplate],
        providers: Dict[str, BaseProvider],
        output_dir: Path = Path("artifacts"),
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> None:
        missing_providers = _REQUIRED_PROVIDERS - providers.keys()
        if missing_providers:
            raise EngineConfigError(f"Engine 缺少必需 provider：{sorted(missing_providers)}")
        missing_templates = _REQUIRED_TEMPLATES - templates.keys()
        if missing_templates:
            raise EngineConfigError(f"Engine 缺少必需 prompt 模板：{sorted(missing_templates)}")

        self._configs_ref = configs_ref
        self._active_task_id = active_task_id
        self._configs_root = Path(configs_root)
        self._policy = policy
        self._templates = templates
        self._providers: Dict[str, InstrumentedProvider] = {
            name: InstrumentedProvider(p) for name, p in providers.items()
        }
        self._output_dir = Path(output_dir)
        self._max_workers = max_workers
        self._rubric_cache: Dict[str, RubricSnapshot] = {}

    @classmethod
    def from_configs(
        cls,
        configs_root: "Path | str",
        task_id: str,
        *,
        model_config_path: "Optional[Path | str]" = None,
        providers: Optional[Dict[str, BaseProvider]] = None,
        output_dir: "Path | str" = "artifacts",
    ) -> "Engine":
        """按约定路径读配置：仲裁策略 + prompts + model_config，建出 Engine。

        没有 bundle 文件——路径全部由约定固定，见模块顶部。任务选择是调用现场的
        参数而不是配置文件里的字段：改一个 tracked 文件来切任务，每次实验都会
        带一个脏 diff，多任务并行还会互相冲突。

        Args:
            configs_root: 配置根目录（如 `configs`）。
            task_id: 要评价的任务，对应 `{configs_root}/tasks/{task_id}/`。
            model_config_path: 覆盖默认的 `{configs_root}/model_config.yaml`。
            providers: 测试注入用（如 FakeProvider）；提供时完全替代从
                model_config 构建的真实 provider，键为 "rater_1"/"rater_2"/
                "rater_3"（可选）/"feedback"。
            output_dir: 产物落盘根目录，默认 "artifacts"。

        Returns:
            构造完成的 Engine。"""
        configs_root = Path(configs_root)
        policy = load_adjudication_policy(configs_root)

        loader = PromptLoader()
        templates = {
            name: loader.load(prompt_path(configs_root, name)) for name in PROMPT_STAGES
        }

        resolved_model_config_path = (
            Path(model_config_path) if model_config_path is not None else configs_root / "model_config.yaml"
        )
        # runtime 段先读：超时/重试要传给 build_provider，并发上限归 Engine 自己用。
        # 注入 providers（测试用）时也照读，好让并发上限在两条路径上保持一致。
        max_workers, retry_config = load_runtime_config(resolved_model_config_path)
        resolved_providers = (
            providers
            if providers is not None
            else load_providers_from_model_config(resolved_model_config_path, retry_config)
        )

        return cls(
            configs_ref=str(configs_root),
            active_task_id=task_id,
            configs_root=configs_root,
            policy=policy,
            templates=templates,
            providers=resolved_providers,
            output_dir=Path(output_dir),
            max_workers=max_workers,
        )

    # ── 内部：rubric 发现/加载 ───────────────────────────────────────────────

    def _rubric_for(self, dim_id: str) -> RubricSnapshot:
        if dim_id not in self._rubric_cache:
            self._rubric_cache[dim_id] = load_dimension_rubric(self._configs_root, self._active_task_id, dim_id)
        return self._rubric_cache[dim_id]

    def _discover_dim_ids(self) -> List[str]:
        return list_task_dimension_ids(self._configs_root, self._active_task_id)

    # ── 内部：单个 Rater 完整链（带 trace） ──────────────────────────────────

    def _run_rater_chain(
        self,
        package: DataPackage,
        dimension_id: str,
        dimension: Dict[str, Any],
        rubric: RubricSnapshot,
        provider: InstrumentedProvider,
        rater_id: str,
    ) -> "tuple[RaterChainResult, List[StageTrace]]":
        traces: List[StageTrace] = []

        selected, t1 = call_with_trace(
            "select", rater_id, provider,
            rater.select, package, dimension, provider, self._templates["select"], rater_id,
        )
        traces.append(t1)

        evidence, t2 = call_with_trace(
            "extract", rater_id, provider,
            rater.extract, package, selected, dimension, provider, self._templates["extraction"], rater_id,
        )
        traces.append(t2)

        dimension_score, t3 = call_with_trace(
            "score", rater_id, provider,
            rater.score, package, evidence, dimension, rubric, provider, self._templates["scoring"], rater_id,
        )
        traces.append(t3)

        chain = RaterChainResult(
            rater_id=rater_id,
            dimension_id=dimension_id,
            selected_unit_ids=selected,
            evidence_unit_ids=evidence,
            score=dimension_score,
        )
        return chain, traces

    def _run_dimension_chains(
        self,
        package: DataPackage,
        dimension: Dict[str, Any],
        rubric: RubricSnapshot,
        rater_1: InstrumentedProvider,
        rater_2: InstrumentedProvider,
    ) -> "tuple[RaterChainResult, RaterChainResult, List[StageTrace]]":
        """一个二级指标的双链评价（rater_1 + rater_2），跑在
        ThreadPoolExecutor 的 worker 线程里。异常原样向上抛出，由
        `_evaluate_one` 捕获并把这一个二级指标标记失败——不在这里吞。"""
        dimension_id = str(dimension["dimension_id"])
        chain_a, traces_a = self._run_rater_chain(package, dimension_id, dimension, rubric, rater_1, "rater_1")
        chain_b, traces_b = self._run_rater_chain(package, dimension_id, dimension, rubric, rater_2, "rater_2")
        return chain_a, chain_b, traces_a + traces_b

    def _empty_evaluation(
        self, dim_id: str, stage_traces: List[StageTrace], failed_dims: List[Dict[str, str]]
    ) -> DimensionEvaluation:
        """一个一级指标下全部二级指标都失败时的产物：没有分数，但失败原因逐条在案。

        `primary_score` 为 None 而不是 0——0 是"评了，得零分"，None 是"没评出来"，
        两者对前端和教师是完全不同的意思，不能混。"""
        return DimensionEvaluation(
            dim_id=dim_id,
            feedback_report={"primary_score": None, "radar": [], "dimensions": {}},
            rater_chains_report={"chains": [], "final_decisions": []},
            run_trace=RunTraceSummary(
                run_id=f"run-{uuid.uuid4().hex[:12]}",
                configs_ref=self._configs_ref,
                dim=dim_id,
                total_tokens=sum(t.tokens for t in stage_traces),
                total_ms=sum(t.ms for t in stage_traces),
                adjudicated_dims=[],
                stage_traces=stage_traces,
                failed_dims=failed_dims,
            ),
        )

    # ── 内部：一个一级指标的完整评价 ─────────────────────────────────────────

    def _evaluate_one(self, package: DataPackage, dim_id: str) -> DimensionEvaluation:
        rubric = self._rubric_for(dim_id)
        secondary_dim_ids = [d["dimension_id"] for d in rubric.dimensions]

        rater_1 = self._providers["rater_1"]
        rater_2 = self._providers["rater_2"]
        rater_3 = self._providers.get("rater_3")
        feedback_provider = self._providers["feedback"]

        dimensions: List[Dict[str, Any]] = []
        for secondary_dim_id in secondary_dim_ids:
            dimension = rubric.get_dimension(secondary_dim_id)
            if dimension is None:
                raise ConfigCompileError(f"Dimension '{secondary_dim_id}' not found in rubric for '{dim_id}'")
            dimensions.append(dimension)

        # 二级指标级并发：每个二级指标的双链评价（select→extract→score ×2）
        # 是一整个 provider IO 密集单元，互相独立、互不共享状态，天然适合
        # ThreadPoolExecutor（GIL 不碍事）。失败隔离落在这一层——一个二级
        # 指标抛错（LLM 报错/超时/越界证据）只把它计入 failed_dims，其余
        # 二级指标不受影响照常产出；不在这里重试或降级。
        stage_traces: List[StageTrace] = []
        chains_a: List[RaterChainResult] = []
        chains_b: List[RaterChainResult] = []
        failed_dims: List[Dict[str, str]] = []
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            future_to_dim_id = {
                executor.submit(self._run_dimension_chains, package, dimension, rubric, rater_1, rater_2): str(
                    dimension["dimension_id"]
                )
                for dimension in dimensions
            }
            for future in as_completed(future_to_dim_id):
                secondary_dim_id = future_to_dim_id[future]
                try:
                    chain_a, chain_b, traces = future.result()
                except Exception as exc:
                    failed_dims.append({"dimension_id": secondary_dim_id, "error": str(exc)})
                    continue
                chains_a.append(chain_a)
                chains_b.append(chain_b)
                stage_traces.extend(traces)

        # 全都失败时没有任何 FinalDecision 可聚合，reconcile/feedback 都无从谈起：
        # 硬把空 decisions 往下送，会在 aggregate_final_decisions 才炸出一条与根因
        # 无关的"decisions 不能为空"，把每个维度真正的错误（鉴权失败/限流/超时）
        # 全埋掉。这里直接短路——failed_dims 已经把真实错误逐条记下，照常落盘。
        # 不抛异常：抛了就会顺着 evaluate() 的循环把同一 sample 下其余一级指标一起
        # 带走，正是 US31「不崩整个 sample」要避免的。
        if not chains_a:
            return self._empty_evaluation(dim_id, stage_traces, failed_dims)

        # reconcile() 本身总会跑（纯比较，可能 0 次 LLM 调用）；只有触发仲裁时才
        # 会内部调用 Rater3。rater 标签因此是调用后才知道的——不用
        # call_with_trace 的固定标签，跑完再按结果决定 rater 是 "rater_3" 还是
        # None（非 rater 相关阶段），呼应 StageTrace 自身文档里两者都是合法值。
        before = (rater_3.metrics.llm_calls, rater_3.metrics.total_tokens) if rater_3 is not None else (0, 0)
        started = time.perf_counter()
        decisions = reconcile.reconcile(
            package, chains_a, chains_b, rubric, self._policy, rater_3, self._templates.get("adjudication")
        )
        reconcile_ms = (time.perf_counter() - started) * 1000
        after = (rater_3.metrics.llm_calls, rater_3.metrics.total_tokens) if rater_3 is not None else (0, 0)
        was_adjudicated = any(d.source == ScoreSource.ADJUDICATED for d in decisions)
        stage_traces.append(
            StageTrace(
                stage="reconcile",
                rater="rater_3" if was_adjudicated else None,
                llm_calls=after[0] - before[0],
                tokens=after[1] - before[1],
                ms=reconcile_ms,
            )
        )

        feedback_report_dict, feedback_trace = call_with_trace(
            "feedback", None, feedback_provider,
            feedback.build_feedback_report, package, decisions, rubric, feedback_provider, self._templates["feedback"],
        )
        stage_traces.append(feedback_trace)

        rater_chains_report = feedback.build_rater_chains_report(chains_a, chains_b, decisions)

        adjudicated_dims = [d.dimension_id for d in decisions if d.source == ScoreSource.ADJUDICATED]
        run_trace = RunTraceSummary(
            run_id=f"run-{uuid.uuid4().hex[:12]}",
            configs_ref=self._configs_ref,
            dim=dim_id,
            total_tokens=sum(t.tokens for t in stage_traces),
            total_ms=sum(t.ms for t in stage_traces),
            adjudicated_dims=adjudicated_dims,
            stage_traces=stage_traces,
            failed_dims=failed_dims,
        )

        return DimensionEvaluation(
            dim_id=dim_id,
            feedback_report=feedback_report_dict,
            rater_chains_report=rater_chains_report,
            run_trace=run_trace,
        )

    # ── Facade ─────────────────────────────────────────────────────────────────

    def evaluate(self, package: DataPackage, dim: Optional[str] = None) -> Dict[str, DimensionEvaluation]:
        """执行 rate(r1) → rate(r2) → reconcile → [adjudicate] → feedback；
        同一一级指标下各二级指标的 rate 阶段并发执行，其余阶段串行。

        Args:
            package: 已切分好的 DataPackage（segment 阶段在此之外完成）。
            dim: 指定单个一级指标；缺省评当前任务下全部一级指标。

        写盘：package.json 到 sample 层（一次，`package.package_id` 作为
        sample 名）；feedback.json/rater_chains.json/run_trace.json 到每个
        评价过的 dim 层。

        Returns:
            {dim_id: DimensionEvaluation}，每个被评价的一级指标一条。"""
        dim_ids = [dim] if dim is not None else self._discover_dim_ids()
        task = self._active_task_id
        sample = package.package_id

        write_package_artifact(self._output_dir, task, sample, package)

        results: Dict[str, DimensionEvaluation] = {}
        for dim_id in dim_ids:
            evaluation = self._evaluate_one(package, dim_id)
            write_feedback_artifact(self._output_dir, task, sample, dim_id, evaluation.feedback_report)
            write_rater_chains_artifact(self._output_dir, task, sample, dim_id, evaluation.rater_chains_report)
            write_run_trace_artifact(self._output_dir, task, sample, dim_id, evaluation.run_trace.to_dict())
            results[dim_id] = evaluation
        return results
