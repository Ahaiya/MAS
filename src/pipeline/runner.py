"""
流水线执行器 — 驱动评价流水线状态机。

PipelineRunner 是系统唯一的编排入口。职责如下：
1. 按合法转换矩阵推进 StateGraph 状态。
2. 按阶段顺序调用各 Worker（仅真实 LLM 模式）。
3. 通过 TraceStore 记录每个节点的生命周期事件。
4. 通过 CheckpointManager 管理 RE_EXTRACT / RE_SCORE 回退重试次数。
5. 使用 router 函数做路由决策，本文件不内联任何路由逻辑。
6. 流水线到达终止状态后，返回 (RunTrace, feedback) 元组。

运行模式：
- 真实 LLM 模式：证据抽取、评分、一致性检查、反馈生成在需要 LLM 的阶段
  调用真实 Agent；feedback 阶段统一走 feedback。

设计不变量：
- 所有阶段间数据流均通过 contracts 层定义的类型传递，不使用临时 dict。
- 本文件不内联任何业务逻辑（维度名、阈值、公式），所有值从 RubricSnapshot /
  PolicySnapshot 读取。
- RE_EXTRACT / RE_SCORE 回退循环由 CheckpointManager.record_fallback() 保护，
  超过最大重试次数后强制进入 FAILED 状态。
- HUMAN_REVIEW 是终止路径，runner 收到后立即返回，不继续执行。

修正记录：
- [2026-03-30] Stage 1/2 接入 chunker + coverage LLM 路径；token_threshold
  与 per-dimension top-k 从 chunking_policy 读取，缺失时自动降级。
- [2026-03-30] Stage L 接入 reconciliation：统一冲突检测与裁决策略，支持
  re_score_scope（all_dimensions/conflicted_only）与策略化 resolution。
- [2026-03-30] feedback 阶段前执行 compute_composite，聚合指标分写入
  feedback_dict["indicator_score"]，同时保留 feedback_dict["composite"]
  作为兼容别名，并作为运行输出属性保留。"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from src.agents import (
    chunker,
    extractor,
    feedback as feedback_agent,
    observer,
    reconciliation,
    scorer,
)
from src.contracts.artifact_bundle import ResolvedArtifactBundle
from src.providers.base import BaseProvider
from src.providers.prompt_loader import PromptTemplate
from src.contracts.evidence import DimensionObservation, EvidenceSpan
from src.contracts.request_models import (
    CoveragePlan,
    EvaluationRequest,
    NormalizedDocument,
    NormalizedRequest,
)
from src.contracts.scoring import (
    AdjudicationRecord,
    CompositeDecision,
    ConflictRecord,
    FinalDimensionDecision,
    ResolutionPath,
    ScoreHypothesis,
)
from src.debug.bundle import DebugBundleWriter
from src.contracts.trace import RunStatus, RunTrace
from src.orchestrator.checkpoints import CheckpointManager, RetryLimitExceeded
from src.orchestrator.states import PipelineState, TERMINAL_STATES
from src.orchestrator.trace_store import TraceStore
from src.pipeline.validators import (
    terminal_validation,
    validate_final_decisions,
    validate_hypotheses,
    validate_observations,
)
from src.policies.rubric_core import build_dimension_traversal
from src.pipeline.export import build_indicator_score_payload
from src.policies.aggregation import compute_composite

DEFAULT_CHECKPOINT_MAX_RETRIES = 2


def _simplify_feedback_indicator_score(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """返回面向展示的指标评分载荷，用于 feedback.json。"""
    if not isinstance(payload, dict) or not payload:
        return None
    composite_score = payload.get("composite_score") or {}
    score = composite_score.get("canonical_score")
    if score is None:
        return None
    return {"score": int(score)}


def _get_rater_ids(bundle: ResolvedArtifactBundle) -> List[str]:
    """从 adjudication policy 中读取所需的 rater ID 列表（配置驱动）。"""
    return list(
        bundle.policy_snapshot.adjudication_policy
        .get("raters", {})
        .get("rater_labels", ["rater_1", "rater_2"])
    )


# ResolutionPath 的严重程度排序：数值越大 = 越严重。
_PATH_SEVERITY = {
    ResolutionPath.POLICY_AVERAGE: 0,
    ResolutionPath.THIRD_RATER: 0,
    ResolutionPath.RE_SCORE: 1,
    ResolutionPath.RE_EXTRACT: 2,
    ResolutionPath.HUMAN_REVIEW: 3,
}

_PATH_TO_STATE = {
    ResolutionPath.THIRD_RATER: PipelineState.ADJUDICATED,
    ResolutionPath.POLICY_AVERAGE: PipelineState.ADJUDICATED,
    ResolutionPath.RE_SCORE: PipelineState.RE_SCORE,
    ResolutionPath.RE_EXTRACT: PipelineState.RE_EXTRACT,
    ResolutionPath.HUMAN_REVIEW: PipelineState.HUMAN_REVIEW,
}


def _route_after_consistency_check(conflicts: List[ConflictRecord]) -> PipelineState:
    """根据一致性检查器的冲突决定下一状态。"""
    if not conflicts:
        return PipelineState.FEEDBACK_RENDERED
    worst_path = max(
        (cr.recommended_path for cr in conflicts),
        key=lambda p: _PATH_SEVERITY.get(p, 0),
    )
    return _PATH_TO_STATE[worst_path]


def _route_after_adjudication(records: List[AdjudicationRecord]) -> PipelineState:
    """根据 adjudication 记录决定下一状态。"""
    unresolved = [r for r in records if not r.is_resolved]
    if not unresolved:
        return PipelineState.FEEDBACK_RENDERED
    worst_path = max(
        (r.resolution_path for r in unresolved),
        key=lambda p: _PATH_SEVERITY.get(p, 0),
    )
    return _PATH_TO_STATE[worst_path]


class PipelineRunner:
    """驱动单个 EvaluationRequest 的评价流水线。
    
        Args:
            bundle          : 冻结的 ResolvedArtifactBundle（来自 ConfigCompiler）。
            provider        : 真实 LLM 调用的默认 BaseProvider（供未指定特定覆盖的
                              阶段和 rater 使用）。
            rater_providers : 可选的按 rater 指定的 provider 映射 {rater_id: BaseProvider}。
                              在评分阶段优先于 `provider`。
            stage_providers : 可选的按阶段指定的 provider 映射 {stage_name: BaseProvider}。
                              在命名阶段优先于 `provider`。
                              可识别的阶段名称："chunking"、"coverage_planning"、
                              "evidence_extraction"、"feedback"。
            prompt_templates: 可选的 dict，将模板名映射到 PromptTemplate。
                              真实 provider 模式下必需。"""

    def __init__(
        self,
        bundle: ResolvedArtifactBundle,
        provider: Optional[BaseProvider] = None,
        rater_providers: Optional[Dict[str, BaseProvider]] = None,
        stage_providers: Optional[Dict[str, BaseProvider]] = None,
        prompt_templates: Optional[Dict[str, PromptTemplate]] = None,
        debug_writer: Optional[DebugBundleWriter] = None,
    ) -> None:
        self._bundle = bundle
        self._provider = provider
        self._rater_providers: Dict[str, BaseProvider] = rater_providers or {}
        self._stage_providers: Dict[str, BaseProvider] = stage_providers or {}
        self._prompt_templates = prompt_templates or {}
        self._debug_writer = debug_writer
        self._last_request: Optional[EvaluationRequest] = None
        self._last_normalized_request: Optional[NormalizedRequest] = None
        self._last_hypotheses: List[ScoreHypothesis] = []
        self._last_spans: List[EvidenceSpan] = []
        self._last_observations: List[DimensionObservation] = []
        self._last_plans: List[CoveragePlan] = []
        self._last_document: Optional[NormalizedDocument] = None
        self._last_conflicts: List[ConflictRecord] = []
        self._last_adjudications: List[AdjudicationRecord] = []
        self._last_decisions: List[FinalDimensionDecision] = []
        self._last_composite: Optional[CompositeDecision] = None
        if (
            self._provider is None
            and not self._rater_providers
            and not self._stage_providers
        ):
            raise ValueError(
                "PipelineRunner requires at least one real provider. "
                "Provide `provider`, `rater_providers`, or `stage_providers`."
            )

    @property
    def last_request(self) -> Optional[EvaluationRequest]:
        """最近一次 run() 调用中使用的 EvaluationRequest。"""
        return self._last_request

    @property
    def last_normalized_request(self) -> Optional[NormalizedRequest]:
        """最近一次 run() 调用中生成的 NormalizedRequest。"""
        return self._last_normalized_request

    @property
    def last_hypotheses(self) -> List[ScoreHypothesis]:
        """最近一次 run() 调用中生成的 ScoreHypotheses。
        
                每个 (rater, dimension) 对包含一个 hypothesis — 例如 6 个维度 × 2 个
                rater 共 12 条记录。若 run() 未被调用或流水线在评分阶段之前失败，
                则为空。"""
        return list(self._last_hypotheses)

    @property
    def last_spans(self) -> List[EvidenceSpan]:
        """最近一次 run() 调用中生成的 EvidenceSpans（所有维度）。
        
                若 run() 未被调用或流水线在证据抽取阶段之前失败，则为空。"""
        return list(self._last_spans)

    @property
    def last_observations(self) -> List[DimensionObservation]:
        """最近一次 run() 调用中生成的 DimensionObservations。
        
                每个维度一条 observation。若 run() 未被调用或流水线在 observation 构建
                阶段之前失败，则为空。"""
        return list(self._last_observations)

    @property
    def last_plans(self) -> List[CoveragePlan]:
        """最近一次 run() 调用中生成的 CoveragePlans。"""
        return list(self._last_plans)

    @property
    def last_document(self) -> Optional[NormalizedDocument]:
        """最近一次 run() 调用中生成的 NormalizedDocument。"""
        return self._last_document

    @property
    def last_conflicts(self) -> List[ConflictRecord]:
        """最近一次 run() 调用中生成的 ConflictRecords。"""
        return list(self._last_conflicts)

    @property
    def last_adjudications(self) -> List[AdjudicationRecord]:
        """最近一次 run() 调用中生成的 AdjudicationRecords。"""
        return list(self._last_adjudications)

    @property
    def last_adjudication_records(self) -> List[AdjudicationRecord]:
        """last_adjudications 的别名（供 eval artifact 导出使用）。"""
        return list(self._last_adjudications)

    @property
    def last_decisions(self) -> List[FinalDimensionDecision]:
        """最近一次 run() 调用中生成的 FinalDimensionDecisions。"""
        return list(self._last_decisions)

    @property
    def last_composite(self) -> Optional[CompositeDecision]:
        """最近一次 run() 调用中生成的 CompositeDecision。"""
        return self._last_composite

    def _provider_for_rater(self, rater_id: str) -> BaseProvider:
        """返回用于指定 rater 的 provider。
        
                优先级：显式 rater_providers > 默认 provider。
                若无可用 provider 则抛出 RuntimeError。"""
        if rater_id in self._rater_providers:
            return self._rater_providers[rater_id]
        if self._provider is not None:
            return self._provider
        raise RuntimeError(
            f"No provider configured for rater '{rater_id}'. "
            "Pass a default provider or configure rater_providers."
        )

    def _provider_for_stage(self, stage: str) -> BaseProvider:
        """返回用于命名流水线阶段的 provider。
        
                优先级：显式 stage_providers > 默认 provider。
                若无可用 provider 则抛出 RuntimeError。"""
        if stage in self._stage_providers:
            return self._stage_providers[stage]
        if self._provider is not None:
            return self._provider
        raise RuntimeError(
            f"No provider configured for stage '{stage}'. "
            "Pass a default provider or configure stage_providers."
        )

    def _replay_provider_name(self) -> str:
        if self._provider is not None:
            return self._provider.name
        if self._stage_providers:
            first_stage = sorted(self._stage_providers)[0]
            return self._stage_providers[first_stage].name
        if self._rater_providers:
            first_rater = sorted(self._rater_providers)[0]
            return self._rater_providers[first_rater].name
        return "unknown"

    def _tpl(self, name: str) -> PromptTemplate:
        """返回指定名称的 PromptTemplate；缺失时抛出 KeyError。"""
        if name not in self._prompt_templates:
            raise KeyError(
                f"Prompt template '{name}' not found. "
                f"Available: {sorted(self._prompt_templates)}"
            )
        return self._prompt_templates[name]

    def _chunking_policy(self) -> Optional[dict]:
        """读取可选的 chunking policy 快照（Stage F 引入）。"""
        raw = getattr(self._bundle.policy_snapshot, "chunking_policy", None)
        return raw if isinstance(raw, dict) else None

    def _scoring_context(self) -> Optional[dict]:
        """读取可选的 scoring context 快照（Stage K 引入）。"""
        raw = getattr(self._bundle.policy_snapshot, "scoring_context", None)
        return raw if isinstance(raw, dict) else None

    def _checkpoint_max_retries(self) -> int:
        """从 bundle operational params 中读取 max_retries，带安全回退。"""
        op_params = getattr(self._bundle, "operational_params", None)
        if op_params is None:
            return DEFAULT_CHECKPOINT_MAX_RETRIES
        max_retries = getattr(op_params, "max_retries", None)
        if isinstance(max_retries, int) and max_retries >= 0:
            return max_retries
        return DEFAULT_CHECKPOINT_MAX_RETRIES

    @staticmethod
    def _token_threshold_from_policy(chunking_policy: Optional[dict]) -> int:
        """从 chunking policy 中解析 token 阈值，带安全回退。"""
        default_threshold = 4000
        if not isinstance(chunking_policy, dict):
            return default_threshold

        policy = chunking_policy
        nested = policy.get("chunking_policy")
        if isinstance(nested, dict):
            policy = nested

        document_processing = policy.get("document_processing", {})
        if not isinstance(document_processing, dict):
            return default_threshold

        raw_threshold = document_processing.get("token_threshold")
        if isinstance(raw_threshold, int) and raw_threshold > 0:
            return raw_threshold
        return default_threshold

    @staticmethod
    def _extraction_char_budget(chunking_policy: Optional[dict]) -> Optional[int]:
        """从 extraction_budget 部分读取 max_chars_per_dimension。"""
        if not isinstance(chunking_policy, dict):
            return None
        policy = chunking_policy
        nested = policy.get("chunking_policy")
        if isinstance(nested, dict):
            policy = nested
        budget = policy.get("extraction_budget", {})
        if not isinstance(budget, dict):
            return None
        val = budget.get("max_chars_per_dimension")
        return int(val) if isinstance(val, (int, float)) and val > 0 else None

    @staticmethod
    def _sample_units_by_budget(
        units: List, max_chars: int
    ) -> List:
        """均匀采样 text_units，使总字符数不超过 max_chars。
        
                从全量 units 中按等间距选取，保证覆盖文档首、中、尾。
                若全量不超过预算则原样返回。"""
        total_chars = sum(len(u.text) for u in units)
        if total_chars <= max_chars:
            return list(units)

        # 估算能放几个 unit
        avg_chars = total_chars // max(len(units), 1)
        n = max(1, max_chars // avg_chars)

        if n >= len(units):
            return list(units)

        # 均匀采样：从 [0, len-1] 中选 n 个等间距索引
        step = len(units) / n
        indices = sorted({min(int(i * step), len(units) - 1) for i in range(n)})
        return [units[i] for i in indices]

    @staticmethod
    def _chunk_method_label(document: NormalizedDocument) -> str:
        """获取节点 trace output_ref 的分块方法标签。"""
        method = document.document_metadata.get("chunking")
        if isinstance(method, str) and method.strip():
            return method.strip()
        if document.text_units:
            first = document.text_units[0].chunk_method
            if isinstance(first, str) and first.strip():
                return first.strip()
        return "unknown"

    @staticmethod
    def _extraction_ref(spans: List[EvidenceSpan]) -> str:
        """构建带有匹配方法诊断的提取摘要。"""
        n_exact = 0
        n_fuzzy = 0
        n_unmatched = 0
        for span in spans:
            note = (span.extraction_note or "").lower()
            if ":fuzzy" in note:
                n_fuzzy += 1
            elif ":unmatched" in note:
                n_unmatched += 1
            elif ":exact" in note or ":normalized" in note:
                n_exact += 1
        return (
            f"spans:{len(spans)} "
            f"(exact:{n_exact}, fuzzy:{n_fuzzy}, unmatched:{n_unmatched})"
        )

    @staticmethod
    def _debug_jsonable(value: Any) -> Any:
        """将 contracts 和嵌套值转换为 JSON 安全的载荷。"""
        if value is None:
            return None
        if hasattr(value, "to_dict"):
            return value.to_dict()
        if isinstance(value, list):
            return [PipelineRunner._debug_jsonable(v) for v in value]
        if isinstance(value, tuple):
            return [PipelineRunner._debug_jsonable(v) for v in value]
        if isinstance(value, dict):
            return {str(k): PipelineRunner._debug_jsonable(v) for k, v in value.items()}
        return value

    def _provider_bindings(self) -> List[Dict[str, Any]]:
        """为 debug 清单汇总 provider/model 绑定。"""
        seen = []
        if self._provider is not None:
            seen.append(
                {
                    "scope": "default",
                    "scope_id": "default",
                    "provider_name": self._provider.name,
                    "model_id": getattr(self._provider, "model_id", None),
                }
            )
        for rid, provider in self._rater_providers.items():
            seen.append(
                {
                    "scope": "rater",
                    "scope_id": rid,
                    "provider_name": provider.name,
                    "model_id": getattr(provider, "model_id", None),
                }
            )
        for stage, provider in self._stage_providers.items():
            seen.append(
                {
                    "scope": "stage",
                    "scope_id": stage,
                    "provider_name": provider.name,
                    "model_id": getattr(provider, "model_id", None),
                }
            )
        return seen

    def _debug_node_start(
        self,
        node_id: str,
        node_type: str,
        input_ref: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self._debug_writer is None:
            return
        self._debug_writer.node_started(
            node_id=node_id,
            node_type=node_type,
            input_ref=input_ref,
            metadata=metadata,
        )

    def _debug_node_finish(
        self,
        node_id: str,
        status: str,
        output_ref: Optional[str],
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self._debug_writer is None:
            return
        self._debug_writer.node_finished(
            node_id=node_id,
            status=status,
            output_ref=output_ref,
            error_message=error_message,
            metadata=metadata,
        )

    def _debug_write_node_artifact(
        self,
        node_id: str,
        artifact_name: str,
        data: Any,
        summary: Optional[str] = None,
    ) -> None:
        if self._debug_writer is None:
            return
        self._debug_writer.write_node_artifact(
            node_id=node_id,
            artifact_name=artifact_name,
            data=self._debug_jsonable(data),
            summary=summary,
        )

    def _debug_route_decision(
        self,
        router_name: str,
        from_state: PipelineState,
        to_state: PipelineState,
        node_id: Optional[str],
        rationale: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self._debug_writer is None:
            return
        self._debug_writer.record_route_decision(
            router_name=router_name,
            from_state=from_state.value,
            to_state=to_state.value,
            node_id=node_id,
            rationale=rationale,
            metadata=metadata,
        )

    def _debug_fallback(
        self,
        node_id: Optional[str],
        fallback_label: str,
        detail: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self._debug_writer is None:
            return
        self._debug_writer.record_fallback(
            node_id=node_id,
            fallback_label=fallback_label,
            detail=detail,
            metadata=metadata,
        )

    def run(
        self,
        request: EvaluationRequest,
    ) -> Tuple[RunTrace, Dict[str, Any]]:
        """执行评估 pipeline。
        
                Returns:
                    (RunTrace, feedback_dict) 其中 feedback_dict 是
                    feedback.run() 的输出。如果 pipeline 失败或升级至
                    HUMAN_REVIEW，则 feedback_dict 为空 {}。"""
        bundle = self._bundle
        rubric = bundle.rubric_snapshot
        policy = bundle.policy_snapshot
        chunking_policy = self._chunking_policy()
        scoring_context = self._scoring_context()
        task_ctx = scoring_context or {}
        material_ctx = task_ctx.get("material_context", {})
        evidence_focus = str(material_ctx.get("evidence_focus", ""))
        material_description = str(material_ctx.get("description", ""))
        chunking_hints = str(task_ctx.get("chunking_hints") or "")

        # 构建以 dimension code 为键的 per-dimension extraction_hints 查找表
        _extraction_hints_by_code: dict[str, str] = {}
        for _entry in (task_ctx.get("scoring_context") or []):
            if isinstance(_entry, dict):
                _code = str(_entry.get("code") or "")
                _hints = str(_entry.get("extraction_hints") or "").strip()
                if _code:
                    _extraction_hints_by_code[_code] = _hints

        token_threshold = self._token_threshold_from_policy(chunking_policy)
        rater_ids = _get_rater_ids(bundle)

        run_id = f"run-{uuid4().hex[:12]}"
        bundle_id = bundle.artifact_bundle.bundle_id
        bundle_version = bundle.artifact_bundle.bundle_version
        request_id = request.request_id or f"req-{hashlib.md5(request.raw_text.encode()).hexdigest()[:12]}"

        state = PipelineState.INIT
        store = TraceStore(run_id, bundle_id, bundle_version, request_id)
        ckpt_mgr = CheckpointManager(run_id, max_retries=self._checkpoint_max_retries())

        # 延续 pipeline 数据
        self._last_request = request
        self._last_normalized_request = None
        self._last_hypotheses = []
        self._last_spans = []
        self._last_observations = []
        self._last_plans = []
        self._last_document = None
        self._last_conflicts = []
        self._last_adjudications = []
        self._last_decisions = []
        self._last_composite = None

        document: Optional[NormalizedDocument] = None
        plans: List[CoveragePlan] = []
        all_spans_by_dim: Dict[str, List[EvidenceSpan]] = {}
        observations: List[DimensionObservation] = []
        hypotheses: List[ScoreHypothesis] = []
        conflicts: List[ConflictRecord] = []
        adj_records: List[AdjudicationRecord] = []
        decisions: Optional[List[FinalDimensionDecision]] = None

        try:
            if self._debug_writer is not None:
                self._debug_writer.start_run(
                    run_id=run_id,
                    request={
                        **request.to_dict(),
                        "request_id": request_id,
                    },
                    bundle_id=bundle_id,
                    bundle_version=bundle_version,
                    provider_mode="real",
                    provider_bindings=self._provider_bindings(),
                )
                self._debug_write_node_artifact(
                    "node_preprocess",
                    "input_request",
                    {
                        **request.to_dict(),
                        "request_id": request_id,
                    },
                    summary="Original evaluation request",
                )

            # ── Stage 0: Config 已解析（bundle 已预编译） ──────
            state = PipelineState.CONFIG_RESOLVED

            # ── Stage 1: 预处理 ──────────────────────────────────────────
            store.record_node_start("node_preprocess", "preprocess",
                                    input_ref=request_id)
            self._debug_node_start(
                "node_preprocess",
                "preprocess",
                request_id,
                metadata={"request_id": request_id},
            )
            _norm_req, document = chunker.run(
                request,
                provider=self._provider_for_stage("chunking"),
                template=self._tpl("chunking"),
                token_threshold=token_threshold,
                chunking_policy=chunking_policy,
                material_type=str(
                    (scoring_context or {})
                    .get("material_context", {})
                    .get("type", "")
                ) or None,
                chunking_hints=chunking_hints,
            )
            self._last_normalized_request = _norm_req
            ckpt = ckpt_mgr.create_checkpoint(
                "node_preprocess", "preprocess", document.document_id
            )
            preprocess_output_ref = (
                f"{document.document_id}"
                f"|chunks:{len(document.text_units)}"
                f"|method:{self._chunk_method_label(document)}"
            )
            store.record_node_success(
                "node_preprocess",
                output_ref=preprocess_output_ref,
                checkpoint=ckpt,
            )
            self._debug_write_node_artifact(
                "node_preprocess",
                "output_normalized_request",
                _norm_req,
                summary="NormalizedRequest produced by preprocess",
            )
            self._debug_write_node_artifact(
                "node_preprocess",
                "output_document",
                document,
                summary=f"NormalizedDocument with {len(document.text_units)} text units",
            )
            self._debug_node_finish(
                "node_preprocess",
                "success",
                preprocess_output_ref,
                metadata={"checkpoint_id": ckpt.checkpoint_id},
            )
            state = PipelineState.PREPROCESSED
            self._last_document = document

            # ── Stage 2: 构建计划（均匀采样 or 全量 → every dimension） ──────
            char_budget = self._extraction_char_budget(chunking_policy)
            sampled_units = self._sample_units_by_budget(
                document.text_units, char_budget
            ) if char_budget else list(document.text_units)
            sampled_unit_ids = [u.unit_id for u in sampled_units]
            coverage_strategy = (
                "sampled" if len(sampled_units) < len(document.text_units)
                else "full_scan"
            )
            traversals = build_dimension_traversal(rubric)
            plans: List[CoveragePlan] = []
            for trav in traversals:
                plans.append(CoveragePlan(
                    plan_id=f"plan-fullscan-{trav.dimension_id}",
                    document_id=document.document_id,
                    dimension_id=trav.dimension_id,
                    target_unit_ids=list(sampled_unit_ids),
                    required_facets=list(trav.required_facets),
                    minimum_evidence_units=trav.evidence_requirements.get(
                        "minimum_evidence_units", 1
                    ),
                    allowed_evidence_scopes=["span", "global"],
                    coverage_strategy=coverage_strategy,
                ))
            state = PipelineState.COVERAGE_PLANNED
            self._last_plans = list(plans)

            # ── Main loop — 支持 RE_EXTRACT / RE_SCORE 重入 ──────────
            while not state in TERMINAL_STATES:
                cs = state

                # RE_EXTRACT: 在 COVERAGE_PLANNED 处重新进入 pipeline
                if cs == PipelineState.RE_EXTRACT:
                    state = PipelineState.COVERAGE_PLANNED
                    cs = PipelineState.COVERAGE_PLANNED

                # COVERAGE_PLANNED → EVIDENCE_EXTRACTED
                if cs == PipelineState.COVERAGE_PLANNED:
                    store.record_node_start("node_extractor", "extract",
                                            input_ref=f"plans:{len(plans)}")
                    self._debug_node_start(
                        "node_extractor",
                        "extract",
                        f"plans:{len(plans)}",
                        metadata={"plan_count": len(plans)},
                    )
                    self._debug_write_node_artifact(
                        "node_extractor",
                        "input_coverage_plans",
                        plans,
                        summary=f"{len(plans)} plans entering extraction",
                    )
                    extraction_tpl = self._tpl("evidence_extraction")
                    extraction_provider = self._provider_for_stage("evidence_extraction")
                    all_spans_by_dim = {
                        plan.dimension_id: extractor.run(
                            plan,
                            document,
                            rubric,
                            extraction_provider,
                            extraction_tpl,
                            override_template=self._prompt_templates.get(
                                f"evidence_extraction_override_{plan.dimension_id}"
                            ),
                            evidence_focus=evidence_focus,
                            material_description=material_description,
                            extraction_hints=_extraction_hints_by_code.get(
                                str((rubric.dimension_by_id.get(plan.dimension_id) or {}).get("code", "")), ""
                            ),
                        )
                        for plan in plans
                    }
                    ckpt = ckpt_mgr.create_checkpoint(
                        "node_extractor", "extract", document.document_id
                    )
                    all_spans_flat = [
                        s for spans in all_spans_by_dim.values() for s in spans
                    ]
                    extraction_output_ref = self._extraction_ref(all_spans_flat)
                    store.record_node_success(
                        "node_extractor",
                        output_ref=extraction_output_ref,
                        checkpoint=ckpt,
                    )
                    self._debug_write_node_artifact(
                        "node_extractor",
                        "output_spans_by_dimension",
                        {
                            dim_id: [span.to_dict() for span in spans]
                            for dim_id, spans in all_spans_by_dim.items()
                        },
                        summary=f"{len(all_spans_by_dim)} dimension buckets",
                    )
                    self._debug_write_node_artifact(
                        "node_extractor",
                        "output_spans_flat",
                        all_spans_flat,
                        summary=f"{len(all_spans_flat)} evidence spans",
                    )
                    self._debug_node_finish(
                        "node_extractor",
                        "success",
                        extraction_output_ref,
                        metadata={"checkpoint_id": ckpt.checkpoint_id},
                    )
                    self._last_spans = all_spans_flat
                    state = PipelineState.EVIDENCE_EXTRACTED
                    cs = PipelineState.EVIDENCE_EXTRACTED

                # EVIDENCE_EXTRACTED → OBSERVATION_BUILT
                if cs == PipelineState.EVIDENCE_EXTRACTED:
                    store.record_node_start("node_observer", "observe",
                                            input_ref=f"spans:{sum(len(s) for s in all_spans_by_dim.values())}")
                    self._debug_node_start(
                        "node_observer",
                        "observe",
                        f"spans:{sum(len(s) for s in all_spans_by_dim.values())}",
                        metadata={"dimension_count": len(all_spans_by_dim)},
                    )
                    self._debug_write_node_artifact(
                        "node_observer",
                        "input_spans_by_dimension",
                        {
                            dim_id: [span.to_dict() for span in spans]
                            for dim_id, spans in all_spans_by_dim.items()
                        },
                        summary=f"{len(all_spans_by_dim)} dimension buckets",
                    )
                    observations = [
                        observer.run(
                            all_spans_by_dim.get(plan.dimension_id, []), plan
                        )
                        for plan in plans
                    ]
                    validate_observations(observations, plans)
                    ckpt = ckpt_mgr.create_checkpoint(
                        "node_observer", "observe", document.document_id
                    )
                    observer_output_ref = f"obs:{len(observations)}"
                    store.record_node_success(
                        "node_observer",
                        output_ref=observer_output_ref,
                        checkpoint=ckpt,
                    )
                    self._debug_write_node_artifact(
                        "node_observer",
                        "output_observations",
                        observations,
                        summary=f"{len(observations)} dimension observations",
                    )
                    self._debug_node_finish(
                        "node_observer",
                        "success",
                        observer_output_ref,
                        metadata={"checkpoint_id": ckpt.checkpoint_id},
                    )
                    self._last_observations = list(observations)
                    state = PipelineState.OBSERVATION_BUILT
                    cs = PipelineState.OBSERVATION_BUILT

                # RE_SCORE: 在 OBSERVATION_BUILT 处重新进入 pipeline
                if cs == PipelineState.RE_SCORE:
                    state = PipelineState.OBSERVATION_BUILT
                    cs = PipelineState.OBSERVATION_BUILT

                # OBSERVATION_BUILT → SCORED
                if cs == PipelineState.OBSERVATION_BUILT:
                    store.record_node_start("node_scorer", "score",
                                            input_ref=f"obs:{len(observations)}")
                    self._debug_node_start(
                        "node_scorer",
                        "score",
                        f"obs:{len(observations)}",
                        metadata={"observation_count": len(observations)},
                    )
                    self._debug_write_node_artifact(
                        "node_scorer",
                        "input_observations",
                        observations,
                        summary=f"{len(observations)} observations entering scorer",
                    )
                    scoring_tpl = self._tpl("scoring")
                    all_spans_flat = [
                        s for spans in all_spans_by_dim.values() for s in spans
                    ]
                    hypotheses = [
                        scorer.run(
                            obs,
                            all_spans_flat,
                            rubric,
                            self._provider_for_rater(rater_id),
                            scoring_tpl,
                            rater_id,
                            scoring_context=scoring_context,
                            override_template=self._prompt_templates.get(
                                f"scoring_override_{obs.dimension_id}"
                            ),
                            node_id="node_scorer",
                            stage_name="scoring",
                            evidence_focus=evidence_focus,
                        )
                        for obs in observations
                        for rater_id in rater_ids
                    ]
                    validate_hypotheses(hypotheses, plans, rater_ids)
                    self._last_hypotheses = list(hypotheses)
                    ckpt = ckpt_mgr.create_checkpoint(
                        "node_scorer", "score", document.document_id
                    )
                    scorer_output_ref = f"hyps:{len(hypotheses)}"
                    store.record_node_success(
                        "node_scorer",
                        output_ref=scorer_output_ref,
                        checkpoint=ckpt,
                    )
                    self._debug_write_node_artifact(
                        "node_scorer",
                        "output_hypotheses",
                        hypotheses,
                        summary=f"{len(hypotheses)} score hypotheses",
                    )
                    self._debug_node_finish(
                        "node_scorer",
                        "success",
                        scorer_output_ref,
                        metadata={"checkpoint_id": ckpt.checkpoint_id},
                    )
                    state = PipelineState.SCORED
                    cs = PipelineState.SCORED

                # SCORED → CONSISTENCY_CHECKED → 路由
                if cs == PipelineState.SCORED:
                    store.record_node_start("node_consistency_checker",
                                            "check_consistency",
                                            input_ref=f"hyps:{len(hypotheses)}")
                    self._debug_node_start(
                        "node_consistency_checker",
                        "check_consistency",
                        f"hyps:{len(hypotheses)}",
                        metadata={"hypothesis_count": len(hypotheses)},
                    )
                    self._debug_write_node_artifact(
                        "node_consistency_checker",
                        "input_hypotheses",
                        hypotheses,
                        summary=f"{len(hypotheses)} hypotheses entering consistency checker",
                    )
                    recon_result = reconciliation.run(
                        hypotheses,
                        policy,
                    )
                    conflicts = recon_result.conflicts
                    self._last_conflicts = list(conflicts)
                    consistency_output_ref = f"conflicts:{len(conflicts)}"
                    store.record_node_success(
                        "node_consistency_checker",
                        output_ref=consistency_output_ref,
                    )
                    self._debug_write_node_artifact(
                        "node_consistency_checker",
                        "output_conflicts",
                        conflicts,
                        summary=f"{len(conflicts)} conflicts detected",
                    )
                    self._debug_write_node_artifact(
                        "node_consistency_checker",
                        "output_reconciliation_result",
                        {
                            "needs_resolution_scoring": recon_result.needs_resolution_scoring,
                            "resolution_dimension_ids": list(
                                recon_result.resolution_dimension_ids
                            ),
                            "resolution_rater_label": recon_result.resolution_rater_label,
                        },
                        summary="Reconciliation planning result",
                    )
                    self._debug_node_finish(
                        "node_consistency_checker",
                        "success",
                        consistency_output_ref,
                    )
                    state = PipelineState.CONSISTENCY_CHECKED

                    next_state = _route_after_consistency_check(conflicts)
                    self._debug_route_decision(
                        "route_after_consistency_check",
                        PipelineState.CONSISTENCY_CHECKED,
                        next_state,
                        "node_consistency_checker",
                        "Route selected from consistency checker output",
                        metadata={"conflict_count": len(conflicts)},
                    )

                    if next_state == PipelineState.FEEDBACK_RENDERED:
                        # 无冲突 — 直接解析为最终决策。
                        adj_records, decisions = reconciliation.resolve(
                            conflicts,
                            hypotheses,
                            policy,
                        )
                        self._last_adjudications = list(adj_records)
                        self._last_decisions = list(decisions)
                        self._debug_write_node_artifact(
                            "node_consistency_checker",
                            "output_decisions_no_conflict",
                            decisions,
                            summary="Final decisions derived directly from scorer hypotheses",
                        )
                        state = PipelineState.FEEDBACK_RENDERED
                        break

                    elif next_state == PipelineState.ADJUDICATED:
                        if recon_result.needs_resolution_scoring:
                            resolution_rater = recon_result.resolution_rater_label
                            target_dimension_ids = set(recon_result.resolution_dimension_ids)
                            target_observations = [
                                obs
                                for obs in observations
                                if obs.dimension_id in target_dimension_ids
                            ]
                            if target_observations:
                                store.record_node_start(
                                    "node_resolution_scorer",
                                    "score_resolution",
                                    input_ref=f"obs:{len(target_observations)}",
                                )
                                self._debug_node_start(
                                    "node_resolution_scorer",
                                    "score_resolution",
                                    f"obs:{len(target_observations)}",
                                    metadata={
                                        "resolution_rater": resolution_rater,
                                        "resolution_dimensions": sorted(target_dimension_ids),
                                    },
                                )
                                scoring_tpl = self._tpl("scoring")
                                all_spans_flat = [
                                    s for spans in all_spans_by_dim.values() for s in spans
                                ]
                                resolution_hypotheses = [
                                    scorer.run(
                                        obs,
                                        all_spans_flat,
                                        rubric,
                                        self._provider_for_rater(resolution_rater),
                                        scoring_tpl,
                                        resolution_rater,
                                        scoring_context=scoring_context,
                                        override_template=self._prompt_templates.get(
                                            f"scoring_override_{obs.dimension_id}"
                                        ),
                                        prior_hypotheses=[
                                            h for h in hypotheses
                                            if h.dimension_id == obs.dimension_id
                                        ],
                                        node_id="node_resolution_scorer",
                                        stage_name="score_resolution",
                                        evidence_focus=evidence_focus,
                                    )
                                    for obs in target_observations
                                ]
                                hypotheses = hypotheses + resolution_hypotheses
                                self._last_hypotheses = list(hypotheses)
                                resolution_output_ref = (
                                    f"{resolution_rater}_hyps:{len(resolution_hypotheses)}"
                                )
                                store.record_node_success(
                                    "node_resolution_scorer",
                                    output_ref=resolution_output_ref,
                                )
                                self._debug_write_node_artifact(
                                    "node_resolution_scorer",
                                    "output_resolution_hypotheses",
                                    resolution_hypotheses,
                                    summary=(
                                        f"{len(resolution_hypotheses)} resolution hypotheses"
                                    ),
                                )
                                self._debug_node_finish(
                                    "node_resolution_scorer",
                                    "success",
                                    resolution_output_ref,
                                )
                            else:
                                self._debug_fallback(
                                    "node_consistency_checker",
                                    "resolution_scoring_skipped",
                                    detail=(
                                        "Resolution scoring requested but no matching "
                                        "observations were selected"
                                    ),
                                )

                        state = PipelineState.ADJUDICATED
                        store.record_node_start("node_adjudicator", "adjudicate",
                                                input_ref=f"conflicts:{len(conflicts)}")
                        self._debug_node_start(
                            "node_adjudicator",
                            "adjudicate",
                            f"conflicts:{len(conflicts)}",
                            metadata={"conflict_count": len(conflicts)},
                        )
                        self._debug_write_node_artifact(
                            "node_adjudicator",
                            "input_conflicts",
                            conflicts,
                            summary=f"{len(conflicts)} conflicts entering adjudicator",
                        )
                        self._debug_write_node_artifact(
                            "node_adjudicator",
                            "input_hypotheses",
                            hypotheses,
                            summary=f"{len(hypotheses)} hypotheses available to adjudicator",
                        )
                        adj_records, decisions = reconciliation.resolve(
                            conflicts,
                            hypotheses,
                            policy,
                        )
                        self._last_adjudications = list(adj_records)
                        self._last_decisions = list(decisions)
                        adjudicator_output_ref = f"decisions:{len(decisions)}"
                        store.record_node_success(
                            "node_adjudicator",
                            output_ref=adjudicator_output_ref,
                        )
                        self._debug_write_node_artifact(
                            "node_adjudicator",
                            "output_adjudications",
                            adj_records,
                            summary=f"{len(adj_records)} adjudication records",
                        )
                        self._debug_write_node_artifact(
                            "node_adjudicator",
                            "output_decisions",
                            decisions,
                            summary=f"{len(decisions)} final decisions",
                        )
                        self._debug_node_finish(
                            "node_adjudicator",
                            "success",
                            adjudicator_output_ref,
                        )

                        next_state2 = _route_after_adjudication(adj_records)
                        self._debug_route_decision(
                            "route_after_adjudication",
                            PipelineState.ADJUDICATED,
                            next_state2,
                            "node_adjudicator",
                            "Route selected from adjudication outcome",
                            metadata={"adjudication_count": len(adj_records)},
                        )
                        state = next_state2

                        if next_state2 == PipelineState.FEEDBACK_RENDERED:
                            break
                        elif next_state2 == PipelineState.HUMAN_REVIEW:
                            return (
                                store.build_run_trace(RunStatus.HUMAN_REVIEW),
                                {},
                            )
                        else:
                            # 来自裁决的 RE_EXTRACT 或 RE_SCORE
                            fb_type = (
                                "re_extract"
                                if next_state2 == PipelineState.RE_EXTRACT
                                else "re_score"
                            )
                            try:
                                self._debug_fallback(
                                    "node_adjudicator",
                                    fb_type,
                                    detail="Fallback requested by adjudication outcome",
                                )
                                ckpt_mgr.record_fallback(fb_type)
                            except RetryLimitExceeded as exc:
                                store.record_force_fail(str(exc))
                                state = PipelineState.FAILED
                                return (
                                    store.build_run_trace(RunStatus.FAILED),
                                    {},
                                )

                    elif next_state == PipelineState.HUMAN_REVIEW:
                        state = PipelineState.HUMAN_REVIEW
                        return (
                            store.build_run_trace(RunStatus.HUMAN_REVIEW),
                            {},
                        )

                    else:
                        # 直接来自一致性检查器的 RE_EXTRACT 或 RE_SCORE
                        fb_type = (
                            "re_extract"
                            if next_state == PipelineState.RE_EXTRACT
                            else "re_score"
                        )
                        try:
                            self._debug_fallback(
                                "node_consistency_checker",
                                fb_type,
                                detail="Fallback requested by consistency checker route",
                            )
                            ckpt_mgr.record_fallback(fb_type)
                            state = next_state
                        except RetryLimitExceeded as exc:
                            store.record_force_fail(str(exc))
                            state = PipelineState.FAILED
                            return (
                                store.build_run_trace(RunStatus.FAILED),
                                {},
                            )
                    # 继续 while 循环（处理 RE_EXTRACT / RE_SCORE 状态）

            # ── Post-loop: graph 处于 FEEDBACK_RENDERED（或终端状态） ────────
            if state in TERMINAL_STATES and state != PipelineState.VALIDATED:
                # 在没有我们 break 的情况下到达了非 VALIDATED 的终端状态
                return store.build_run_trace(RunStatus.FAILED), {}

            if decisions is None:
                store.record_force_fail("Pipeline ended without producing decisions")
                state = PipelineState.FAILED
                return store.build_run_trace(RunStatus.FAILED), {}

            # ── Stage: Composite Score ───────────────────────────────────────
            # 计算 composite 总分（由 aggregation policy 配置驱动）。
            # 无裁决时使用 without_resolution 变体（平均 R1+R2）；
            # 有裁决时使用 with_resolution 变体（直接使用 FinalDimensionDecision 分数）。
            validate_final_decisions(decisions, plans)
            composite = compute_composite(
                decisions=decisions,
                hypotheses=hypotheses,
                adjudications=adj_records,
                policy=policy,
            )
            self._last_decisions = list(decisions)
            self._last_composite = composite

            # ── Stage: Feedback ──────────────────────────────────────────────
            store.record_node_start("node_feedback", "feedback",
                                    input_ref=f"decisions:{len(decisions)}")
            self._debug_node_start(
                "node_feedback",
                "feedback",
                f"decisions:{len(decisions)}",
                metadata={"decision_count": len(decisions)},
            )
            self._debug_write_node_artifact(
                "node_feedback",
                "input_decisions",
                decisions,
                summary=f"{len(decisions)} decisions entering feedback",
            )
            all_spans_flat = [s for spans in all_spans_by_dim.values() for s in spans]
            explanation_tpl = self._tpl("explanation")
            explanation_overrides: Dict[str, PromptTemplate] = {}
            for tpl_name, tpl in self._prompt_templates.items():
                if not tpl_name.startswith("explanation_override_"):
                    continue
                dimension_id = tpl_name.removeprefix("explanation_override_")
                explanation_overrides[dimension_id] = tpl
            feedback = feedback_agent.run(
                decisions=decisions,
                observations=observations,
                spans=all_spans_flat,
                hypotheses=hypotheses,
                rubric=rubric,
                policy=policy,
                provider=self._provider_for_stage("feedback"),
                template=explanation_tpl,
                override_templates=explanation_overrides,
                evidence_focus=evidence_focus,
                audience="evaluator",
                scoring_context=scoring_context,
            )

            # 将聚合后的指标分写入 feedback。
            # `feedback.json` 仅保留简化后的 `indicator_score` 展示载荷。
            indicator_score = _simplify_feedback_indicator_score(build_indicator_score_payload(
                composite,
                bundle_metadata=bundle.artifact_bundle.metadata,
            ))
            feedback["indicator_score"] = indicator_score

            feedback_output_ref = f"dims:{len(feedback.get('dimensions', {}))}"
            store.record_node_success(
                "node_feedback",
                output_ref=feedback_output_ref,
            )
            self._debug_write_node_artifact(
                "node_feedback",
                "output_feedback",
                feedback,
                summary=f"{len(feedback.get('dimensions', {}))} dimension feedback blocks",
            )
            self._debug_write_node_artifact(
                "node_feedback",
                "output_indicator_score",
                indicator_score,
                summary="Indicator-level aggregate score written into feedback",
            )
            self._debug_node_finish(
                "node_feedback",
                "success",
                feedback_output_ref,
            )

            # ── 终端验证 ──────────────────────────────────────────
            terminal_passed = terminal_validation(decisions, plans, rubric)

            state = PipelineState.VALIDATED

            run_trace = store.build_run_trace(
                status=RunStatus.COMPLETED,
                terminal_validation_passed=terminal_passed,
                replay_metadata={"provider": self._replay_provider_name()},
            )
            return run_trace, feedback

        except Exception as exc:
            if self._debug_writer is not None:
                self._debug_writer.emit_event(
                    "run_failed",
                    state=state.value if state is not None else None,
                    error_message=str(exc),
                )
            store.record_force_fail(str(exc))
            state = PipelineState.FAILED
            return store.build_run_trace(RunStatus.FAILED), {}
