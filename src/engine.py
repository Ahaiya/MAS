"""
Engine 门面：`Engine.from_bundle(path).evaluate(package, dim)` 把 02-05 各段
串成一条线性链，跑通一次完整评价。

流水线：rate(r1) → rate(r2) → reconcile → [adjudicate] → feedback，对当前任务
下每个一级指标（rubric.yaml）各自的全部二级指标各跑一遍。同一 sample 下各二级
指标的双链评价用 ThreadPoolExecutor 并发跑（provider IO 密集，GIL 不碍事），
上限 `max_workers` 从 model_config.yaml 的 concurrency 段读取，默认 8。segment
阶段发生在 Engine.evaluate() 之外——engine 只认「量规 + 已切分好的
DataPackage」，数据包的来源（read_text_file() 或未来的多源解析接入层）不是它
的关心范围。

model_config.yaml 是模型/参数的唯一来源，缺失 raters.rater_1/rater_2 或
stages.feedback 直接报错——不读 bundle 内嵌的 provider_config、不降级单
default provider。密钥值只从 .env 读（build_provider() 已经这样做）。rater_3
允许缺失：只在真正触发仲裁时才需要，报错逻辑在 reconcile.py 里已经实现。

trace 用收集器模式：每次调用 select/extract/score/adjudicate/feedback 都在
一层 provider 包装器上记录耗时与 token 用量，engine 在调用前后各拍一次快照
做差，不侵入 rater.py/adjudicator.py/report.py 内部。并发下同一个 provider
实例被多个线程共享，包装器按线程隔离计数（threading.local）而非加锁，快照
差值天然只反映当前线程自己发起的调用。

失败隔离：一个二级指标的双链评价（select→extract→score ×2）失败（LLM 报错/
超时/越界证据）只把该二级指标计入 run_trace 的 failed_dims，不参与 reconcile/
feedback，也不中断其余二级指标；一级指标内其余维度照常产出。除此之外错误直接
抛出：reconcile/feedback 等阶段失败仍会中断当前一级指标的评价，没有状态机回退
重入。"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

import yaml

from src.agents import rater, reconcile, report
from src.artifacts import (
    write_feedback_artifact,
    write_package_artifact,
    write_rater_chains_artifact,
    write_run_trace_artifact,
)
from src.config.compiler import ConfigCompileError, list_task_dimension_ids, load_dimension_rubric
from src.contracts.artifact_bundle import PolicySnapshot, ProviderEntryConfig, RubricSnapshot
from src.contracts.package import DataPackage
from src.contracts.scoring import RaterChainResult, ScoreSource
from src.contracts.trace import RunTraceSummary, StageTrace
from src.providers.base import BaseProvider, LLMRequest, LLMResponse, ProviderCapability
from src.providers.factory import build_provider
from src.providers.prompt_loader import PromptLoader, PromptTemplate

_T = TypeVar("_T")

_REQUIRED_PROVIDERS = frozenset({"rater_1", "rater_2", "feedback"})
_REQUIRED_TEMPLATES = frozenset({"select", "extraction", "scoring", "feedback"})
_DEFAULT_MAX_WORKERS = 8


class EngineConfigError(Exception):
    """bundle/model_config 缺失必需配置时抛出——不静默降级。"""


# ── trace 收集：provider 包装器 + 调用计时 ─────────────────────────────────────


@dataclass
class _ProviderMetrics:
    llm_calls: int = 0
    total_tokens: int = 0


class _InstrumentedProvider(BaseProvider):
    """包装一个 BaseProvider，旁路记录调用次数与 token 用量，供 engine 的收集器
    模式使用；不改变被包装 provider 的行为。

    二级指标级并发下，同一个 rater 的 provider 实例被多个 worker 线程并发调用。
    _call_with_trace 靠"调用前后各拍一次快照做差"取得这次调用的用量——如果
    metrics 是共享计数器，另一个线程在快照窗口之间插入的调用会污染这次差值。
    按线程隔离 metrics（而非加锁）天然解决这个问题：同一时刻只有发起这次调用
    的那个线程会读写自己的 metrics，快照差值只反映它自己的调用。"""

    def __init__(self, inner: BaseProvider) -> None:
        self._inner = inner
        self._local = threading.local()

    @property
    def metrics(self) -> _ProviderMetrics:
        if not hasattr(self._local, "metrics"):
            self._local.metrics = _ProviderMetrics()
        return self._local.metrics

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def capabilities(self) -> "frozenset[ProviderCapability]":
        return self._inner.capabilities

    def complete(self, request: LLMRequest) -> LLMResponse:
        response = self._inner.complete(request)
        metrics = self.metrics
        metrics.llm_calls += 1
        metrics.total_tokens += response.usage.total_tokens
        return response


def _call_with_trace(
    stage: str,
    rater_id: Optional[str],
    provider: Optional[_InstrumentedProvider],
    fn: Callable[..., _T],
    *args: Any,
    **kwargs: Any,
) -> "tuple[_T, StageTrace]":
    """调用 fn，用 provider 累计的调用数/token 数在前后各拍一次快照做差，产出
    这次调用的 StageTrace。provider 为 None 时（如 rater_3 未配置且未触发
    仲裁）llm_calls/tokens 记为 0，只记耗时。"""
    before = (provider.metrics.llm_calls, provider.metrics.total_tokens) if provider is not None else (0, 0)
    started = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed_ms = (time.perf_counter() - started) * 1000
    after = (provider.metrics.llm_calls, provider.metrics.total_tokens) if provider is not None else (0, 0)
    trace = StageTrace(
        stage=stage,
        rater=rater_id,
        llm_calls=after[0] - before[0],
        tokens=after[1] - before[1],
        ms=elapsed_ms,
    )
    return result, trace


# ── model_config.yaml → providers ───────────────────────────────────────────


def _entry_from_dict(raw: Dict[str, Any], *, label: str) -> ProviderEntryConfig:
    if "api_key_env" not in raw:
        raise EngineConfigError(f"model_config 中 {label} 缺少必填字段 api_key_env。")
    return ProviderEntryConfig(
        api_key_env=raw["api_key_env"],
        model=raw.get("model", "") or "",
        api_base=raw.get("api_base", "") or "",
        params=dict(raw.get("params") or {}),
    )


def _load_providers_from_model_config(model_config_path: Path) -> Dict[str, BaseProvider]:
    """从 model_config.yaml 读取 raters.{rater_1,rater_2,rater_3} + stages.feedback。

    model_config 是模型/参数唯一来源，缺失该文件或缺 rater_1/rater_2/feedback
    直接报错——不读 bundle 内嵌 provider_config、不降级单 default provider。
    rater_3 允许缺失，缺失时的报错时机在 reconcile.py 里（只在真正触发仲裁时）。"""
    if not model_config_path.exists():
        raise EngineConfigError(f"model_config not found: {model_config_path}")
    data = yaml.safe_load(model_config_path.read_text(encoding="utf-8")) or {}

    raters_raw = data.get("raters") or {}
    stages_raw = data.get("stages") or {}
    for required in ("rater_1", "rater_2"):
        if required not in raters_raw:
            raise EngineConfigError(
                f"model_config 缺少 raters.{required}——每次评价都需要独立双链路的两个 rater。"
            )
    if "feedback" not in stages_raw:
        raise EngineConfigError("model_config 缺少 stages.feedback——feedback 阶段每次评价都需要 provider。")

    providers: Dict[str, BaseProvider] = {
        rater_id: build_provider(_entry_from_dict(cfg, label=f"raters.{rater_id}"))
        for rater_id, cfg in raters_raw.items()
    }
    providers["feedback"] = build_provider(_entry_from_dict(stages_raw["feedback"], label="stages.feedback"))
    return providers


def _load_max_workers(model_config_path: Path) -> int:
    """从 model_config.yaml 的 concurrency 段读取二级指标级并发上限，默认 8。

    并发是性能优化、不是必需配置：文件缺失或没有 concurrency 段时直接用默认值，
    不像 raters/feedback 那样报错。"""
    if not model_config_path.exists():
        return _DEFAULT_MAX_WORKERS
    data = yaml.safe_load(model_config_path.read_text(encoding="utf-8")) or {}
    concurrency = data.get("concurrency") or {}
    return int(concurrency.get("max_workers", _DEFAULT_MAX_WORKERS))


def _strip_configs_prefix(path: str) -> str:
    if path.startswith("configs/"):
        return path[len("configs/"):]
    return path


# ── Engine ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DimensionEvaluation:
    """一次一级指标评价的完整产物（同时也是落盘到 dim 层的三份 JSON 的内容）。"""

    dim_id: str
    feedback_report: Dict[str, Any]
    rater_chains_report: Dict[str, Any]
    run_trace: RunTraceSummary


class Engine:
    """`量规 + 数据包 → 评价` 门面。用 `Engine.from_bundle(path)` 构造。"""

    def __init__(
        self,
        *,
        bundle_ref: str,
        active_task_id: str,
        configs_root: Path,
        policy: PolicySnapshot,
        templates: Dict[str, PromptTemplate],
        providers: Dict[str, BaseProvider],
        output_dir: Path = Path("artifacts"),
        max_workers: int = _DEFAULT_MAX_WORKERS,
    ) -> None:
        missing_providers = _REQUIRED_PROVIDERS - providers.keys()
        if missing_providers:
            raise EngineConfigError(f"Engine 缺少必需 provider：{sorted(missing_providers)}")
        missing_templates = _REQUIRED_TEMPLATES - templates.keys()
        if missing_templates:
            raise EngineConfigError(f"Engine 缺少必需 prompt 模板：{sorted(missing_templates)}")

        self._bundle_ref = bundle_ref
        self._active_task_id = active_task_id
        self._configs_root = Path(configs_root)
        self._policy = policy
        self._templates = templates
        self._providers: Dict[str, _InstrumentedProvider] = {
            name: _InstrumentedProvider(p) for name, p in providers.items()
        }
        self._output_dir = Path(output_dir)
        self._max_workers = max_workers
        self._rubric_cache: Dict[str, RubricSnapshot] = {}

    @classmethod
    def from_bundle(
        cls,
        bundle_path: "Path | str",
        *,
        configs_root: "Path | str" = "configs",
        model_config_path: "Optional[Path | str]" = None,
        providers: Optional[Dict[str, BaseProvider]] = None,
        output_dir: "Path | str" = "artifacts",
    ) -> "Engine":
        """编译 bundle、从 model_config 建 providers、加载 prompts。

        Args:
            bundle_path: `configs/bundle.yaml` 路径。
            configs_root: 配置根目录，默认 "configs"。
            model_config_path: 覆盖默认的 `{configs_root}/model_config.yaml`。
            providers: 测试注入用（如 FakeProvider）；提供时完全替代从
                model_config 构建的真实 provider，键为 "rater_1"/"rater_2"/
                "rater_3"（可选）/"feedback"。
            output_dir: 产物落盘根目录，默认 "artifacts"。

        Returns:
            构造完成的 Engine。"""
        configs_root = Path(configs_root)
        bundle_path = Path(bundle_path)
        bundle_data = yaml.safe_load(bundle_path.read_text(encoding="utf-8")) or {}

        active_task_id = bundle_data["active_task_id"]

        adjudication_policy_path = configs_root / _strip_configs_prefix(bundle_data["policies"]["adjudication"])
        adjudication_data = yaml.safe_load(adjudication_policy_path.read_text(encoding="utf-8"))
        adjudication_policy = adjudication_data["adjudication_policy"]
        policy = PolicySnapshot(
            adjudication_policy=adjudication_policy,
            aggregation_policy={},
            explanation_policy={},
            policy_version=str(adjudication_policy.get("policy_id", "unknown")),
        )

        loader = PromptLoader()
        templates = {
            name: loader.load(configs_root / _strip_configs_prefix(path))
            for name, path in bundle_data["prompts"].items()
        }

        resolved_model_config_path = (
            Path(model_config_path) if model_config_path is not None else configs_root / "model_config.yaml"
        )
        resolved_providers = (
            providers if providers is not None else _load_providers_from_model_config(resolved_model_config_path)
        )
        max_workers = _load_max_workers(resolved_model_config_path)

        return cls(
            bundle_ref=str(bundle_path),
            active_task_id=active_task_id,
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
        provider: _InstrumentedProvider,
        rater_id: str,
    ) -> "tuple[RaterChainResult, List[StageTrace]]":
        traces: List[StageTrace] = []

        selected, t1 = _call_with_trace(
            "select", rater_id, provider,
            rater.select, package, dimension, provider, self._templates["select"], rater_id,
        )
        traces.append(t1)

        evidence, t2 = _call_with_trace(
            "extract", rater_id, provider,
            rater.extract, package, selected, dimension, provider, self._templates["extraction"], rater_id,
        )
        traces.append(t2)

        dimension_score, t3 = _call_with_trace(
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
        rater_1: _InstrumentedProvider,
        rater_2: _InstrumentedProvider,
    ) -> "tuple[RaterChainResult, RaterChainResult, List[StageTrace]]":
        """一个二级指标的双链评价（rater_1 + rater_2），跑在
        ThreadPoolExecutor 的 worker 线程里。异常原样向上抛出，由
        `_evaluate_one` 捕获并把这一个二级指标标记失败——不在这里吞。"""
        dimension_id = str(dimension["dimension_id"])
        chain_a, traces_a = self._run_rater_chain(package, dimension_id, dimension, rubric, rater_1, "rater_1")
        chain_b, traces_b = self._run_rater_chain(package, dimension_id, dimension, rubric, rater_2, "rater_2")
        return chain_a, chain_b, traces_a + traces_b

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

        # reconcile() 本身总会跑（纯比较，可能 0 次 LLM 调用）；只有触发仲裁时才
        # 会内部调用 Rater3。rater 标签因此是调用后才知道的——不用
        # _call_with_trace 的固定标签，跑完再按结果决定 rater 是 "rater_3" 还是
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

        feedback_report_dict, feedback_trace = _call_with_trace(
            "feedback", None, feedback_provider,
            report.build_feedback_report, package, decisions, rubric, feedback_provider, self._templates["feedback"],
        )
        stage_traces.append(feedback_trace)

        rater_chains_report = report.build_rater_chains_report(chains_a, chains_b, decisions)

        adjudicated_dims = [d.dimension_id for d in decisions if d.source == ScoreSource.ADJUDICATED]
        run_trace = RunTraceSummary(
            run_id=f"run-{uuid.uuid4().hex[:12]}",
            bundle_ref=self._bundle_ref,
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

    # ── 门面 ─────────────────────────────────────────────────────────────────

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
