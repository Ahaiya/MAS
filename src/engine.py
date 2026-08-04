"""
Engine Facade：`Engine.from_configs(root, task_id).evaluate(package, dim)` 跑通一次完整评价。

流水线：rate(r1) → rate(r2) → reconcile → [adjudicate] → feedback.
同一 submission 下各观测点的双链评价用 ThreadPoolExecutor 并发跑（provider IO 密集，GIL 不碍事）
上限 `max_workers` 从 model_config.yaml 的 runtime 段读取，默认 8。
解析（parse）阶段发生在 Engine.evaluate() 之外，engine 只认「量规 + 数据包」，
数据包从哪来不是它的关心范围。

model_config.yaml 是模型/参数的唯一来源：`providers` 缺 rater_1/rater_2/feedback
直接报错，条目缺 model/api_base/api_key_env 也直接报错。
密钥值只从 .env 读，配置里只存环境变量的名字。rater_3 允许缺失：只在真正触发
仲裁时才需要，报错逻辑在 reconcile.py 里。

trace 用收集器模式：provider 包装器（InstrumentedProvider）按每次 LLM 调用的
metadata 自动记一条 StageTrace（stage/rater/观测点/token/耗时），engine 在一个
二级指标跑完后一次性 drain，不侵入
rater.py/adjudicator.py/feedback.py 内部。仲裁调用同样经过 provider，因此
「跑没跑 Rater3、在哪个观测点上跑的」逐条在案，不会被折叠进一条汇总里。

失败隔离：一个观测点的双链评价（select→extract→score ×2）失败（LLM 报错/
超时/越界证据）只把该观测点计入 run_trace 的 failed_codes，不参与 reconcile/
feedback，也不中断其余观测点；二级指标内其余维度照常产出。全部观测点都失败
时短路掉 reconcile/feedback，产出 primary_score=None 的空评价（失败原因仍逐条在
run_trace 里），同样不抛异常。
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.agents import feedback, rater, reconcile
from src.artifacts import (
    write_feedback_artifact,
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
from src.contracts.configuration import PolicySnapshot, RubricSnapshot
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
from src.providers.instrumented import InstrumentedProvider
from src.providers.prompt_loader import PromptLoader, PromptTemplate

_REQUIRED_PROVIDERS = frozenset({"rater_1", "rater_2", "feedback"})
_REQUIRED_TEMPLATES = frozenset({"select", "extraction", "scoring", "feedback"})
# run_trace.json 里 stage_traces 的排序权重，按流水线先后而非字母序。
_STAGE_ORDER = {"select": 0, "extract": 1, "score": 2, "adjudicate": 3, "feedback": 4}


# ── Engine ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DimensionEvaluation:
    """一次二级指标评价的完整产物（同时也是落盘到 dim 层的三份 JSON 的内容）。"""

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
        code: str,
        dimension: Dict[str, Any],
        rubric: RubricSnapshot,
        provider: InstrumentedProvider,
        rater_id: str,
    ) -> RaterChainResult:
        selected = rater.select(
            package, dimension, rubric, provider, self._templates["select"], rater_id,
            rubric.indicator_description,
        )
        evidence = rater.extract(
            package, selected, dimension, rubric, provider, self._templates["extraction"], rater_id,
            rubric.indicator_description,
        )
        dimension_score = rater.score(
            package, evidence, dimension, rubric, provider, self._templates["scoring"], rater_id,
        )
        return RaterChainResult(
            rater_id=rater_id,
            code=code,
            selected_unit_ids=selected,
            evidence_unit_ids=evidence,
            score=dimension_score,
        )

    def _run_dimension_chains(
        self,
        package: DataPackage,
        dimension: Dict[str, Any],
        rubric: RubricSnapshot,
        rater_1: InstrumentedProvider,
        rater_2: InstrumentedProvider,
    ) -> "tuple[RaterChainResult, RaterChainResult]":
        """一个观测点的双链评价（rater_1 + rater_2），跑在ThreadPoolExecutor 的 worker 线程里。
        异常原样向上抛出，由`_evaluate_one` 捕获并把这一个观测点标记失败。"""

        code = str(dimension["code"])
        chain_a = self._run_rater_chain(package, code, dimension, rubric, rater_1, "rater_1")
        chain_b = self._run_rater_chain(package, code, dimension, rubric, rater_2, "rater_2")
        return chain_a, chain_b

    def _drain_stage_traces(self) -> List[StageTrace]:
        """取走所有 provider 记下的 StageTrace。
        并发下各条记录的产生顺序不确定，按 观测点 → 阶段 → rater 排序，让 run_trace.json 在同样地输入下逐行可比。"""

        traces = [t for provider in self._providers.values() for t in provider.drain_traces()]
        return sorted(traces, key=lambda t: (t.code or "", _STAGE_ORDER.get(t.stage, 99), t.rater or ""))

    def _empty_evaluation(
        self, dim_id: str, stage_traces: List[StageTrace], failed_codes: List[Dict[str, str]]
    ) -> DimensionEvaluation:
        """一个二级指标下全部观测点都失败时的产物：没有分数，但失败原因逐条在案。

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
                adjudicated_codes=[],
                stage_traces=stage_traces,
                failed_codes=failed_codes,
            ),
        )

    # ── 内部：一个二级指标的完整评价 ─────────────────────────────────────────

    def _evaluate_one(self, package: DataPackage, dim_id: str) -> DimensionEvaluation:
        rubric = self._rubric_for(dim_id)

        # 上一个二级指标若在 reconcile/feedback 阶段抛错退出，它的记录还留在 provider 里；丢掉，免得算到这个二级指标头上。
        self._drain_stage_traces()

        codes = [str(d["code"]) for d in rubric.dimensions]

        rater_1 = self._providers["rater_1"]
        rater_2 = self._providers["rater_2"]
        rater_3 = self._providers.get("rater_3")
        feedback_provider = self._providers["feedback"]

        dimensions: List[Dict[str, Any]] = []
        for code in codes:
            dimension = rubric.get_dimension(code)
            if dimension is None:
                raise ConfigCompileError(f"观测点 '{code}' 不在二级指标 '{dim_id}' 的量规里")
            dimensions.append(dimension)

        # 观测点级并发：每个观测点的双链评价（select→extract→score ×2）
        # 是一整个 provider IO 密集单元，互相独立、互不共享状态，天然适合
        # ThreadPoolExecutor（GIL 不碍事）。失败隔离落在这一层——一个观测点
        # 抛错（LLM 报错/超时/越界证据）只把它计入 failed_codes，其余
        # 观测点不受影响照常产出；不在这里重试或降级。
        chains_a: List[RaterChainResult] = []
        chains_b: List[RaterChainResult] = []
        failed_codes: List[Dict[str, str]] = []
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            future_to_code = {
                executor.submit(self._run_dimension_chains, package, dimension, rubric, rater_1, rater_2): str(
                    dimension["code"]
                )
                for dimension in dimensions
            }
            for future in as_completed(future_to_code):
                code = future_to_code[future]
                try:
                    chain_a, chain_b = future.result()
                except Exception as exc:
                    failed_codes.append({"code": code, "error": str(exc)})
                    continue
                chains_a.append(chain_a)
                chains_b.append(chain_b)


        if not chains_a:
            return self._empty_evaluation(dim_id, self._drain_stage_traces(), failed_codes)

        # reconcile() 是纯比较，本身不发 LLM 调用，因此没有自己的 StageTrace；
        # 触发仲裁时 Rater3 的调用经过 provider，自动记成 stage="adjudicate" 的条目（带观测点 code），不需要在这里手工插桩。
        decisions = reconcile.reconcile(
            package, chains_a, chains_b, rubric, self._policy, rater_3, self._templates.get("adjudication")
        )

        feedback_report_dict = feedback.build_feedback_report(
            package, decisions, rubric, feedback_provider, self._templates["feedback"],
        )
        stage_traces = self._drain_stage_traces()

        rater_chains_report = feedback.build_rater_chains_report(chains_a, chains_b, decisions)

        adjudicated_codes = [d.code for d in decisions if d.source == ScoreSource.ADJUDICATED]
        run_trace = RunTraceSummary(
            run_id=f"run-{uuid.uuid4().hex[:12]}",
            configs_ref=self._configs_ref,
            dim=dim_id,
            total_tokens=sum(t.tokens for t in stage_traces),
            total_ms=sum(t.ms for t in stage_traces),
            adjudicated_codes=adjudicated_codes,
            stage_traces=stage_traces,
            failed_codes=failed_codes,
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
        同一二级指标下各观测点的 rate 阶段并发执行，其余阶段串行。

        Args:
            package: 解析好的 DataPackage（parse 阶段在此之外完成）。
            dim: 指定单个二级指标；缺省评当前任务下全部二级指标。

        写盘：feedback.json/rater_chains.json/run_trace.json 到每个评价过的
        dim 层。package.json **不**在这里另存一份——它是 parse 落在
        `packages/{task}/{submission}/` 的输入，同一份包评多次会存出多份
        一模一样的副本。

        Returns:
            {dim_id: DimensionEvaluation}，每个被评价的二级指标一条。"""
        dim_ids = [dim] if dim is not None else self._discover_dim_ids()
        task = self._active_task_id
        # package_id 是 "{task}/{submission}"，产物目录只用后半段。
        submission = package.package_id.split("/")[-1]

        results: Dict[str, DimensionEvaluation] = {}
        for dim_id in dim_ids:
            evaluation = self._evaluate_one(package, dim_id)
            write_feedback_artifact(self._output_dir, task, submission, dim_id, evaluation.feedback_report)
            write_rater_chains_artifact(self._output_dir, task, submission, dim_id, evaluation.rater_chains_report)
            write_run_trace_artifact(self._output_dir, task, submission, dim_id, evaluation.run_trace.to_dict())
            results[dim_id] = evaluation
        return results
