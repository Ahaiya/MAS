"""
流水线执行器 — 驱动评价流水线状态机。

PipelineRunner 是系统唯一的编排入口。职责如下：
1. 按合法转换矩阵推进 StateGraph 状态。
2. 按阶段顺序调用各 Worker（真实 LLM 模式或确定性 Mock 模式）。
3. 通过 TraceStore 记录每个节点的生命周期事件。
4. 通过 CheckpointManager 管理 RE_EXTRACT / RE_SCORE 回退重试次数。
5. 使用 router 函数做路由决策，本文件不内联任何路由逻辑。
6. 流水线到达终止状态后，返回 (RunTrace, feedback) 元组。

运行模式：
- 真实 LLM 模式：provider 不为 None 时生效。证据抽取、评分、一致性检查、反馈生成
  在需要 LLM 的阶段调用真实 Agent；feedback 阶段统一走 feedback。
- Mock 模式：未配置 provider 时使用确定性 Mock Worker，用于回归测试与管道联调。

设计不变量：
- 所有阶段间数据流均通过 contracts 层定义的类型传递，不使用临时 dict。
- 本文件不内联任何业务逻辑（维度名、阈值、公式），所有值从 RubricSnapshot /
  PolicySnapshot 读取。
- RE_EXTRACT / RE_SCORE 回退循环由 CheckpointManager.record_fallback() 保护，
  超过最大重试次数后强制进入 FAILED 状态。
- HUMAN_REVIEW 是终止路径，runner 收到后立即返回，不继续执行。

修正记录：
- [2026-03-26] 真实 LLM 模式下一致性检查改用 consistency_checker（支持全量触发器，
  包括 Cusp Rule），deterministic 模式保留 deterministic_consistency_checker。
- [2026-03-26] 真实 LLM 模式下检测到冲突后，自动触发 resolution rater（默认 rater_3）
  对全部维度重新评分（ASAP Set 8 "resolution read" 规则），再交由 adjudicator
  以 rater_3 分数为权威进行裁决。deterministic 模式保留 deterministic_adjudicator。
- [2026-03-26] feedback 阶段前新增 compute_composite 调用，composite 总分写入
  feedback_dict["composite"]；adj_records 提升为 carry-forward 变量供聚合阶段使用。
- [2026-03-26] adjudicator 兜底路径（rater_3 缺失）的 resolution_path 改为
  HUMAN_REVIEW，避免 route_after_adjudication 路由到 ADJUDICATED 触发非法状态转换。
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from src.agents import (
    adjudicator,
    coverage,
    consistency_checker,
    deterministic_adjudicator,
    deterministic_consistency_checker,
    deterministic_extractor,
    deterministic_scorer,
    extractor,
    feedback as feedback_agent,
    observer,
    preprocess,
    scorer,
)
from src.contracts.artifact_bundle import ResolvedArtifactBundle
from src.providers.base import BaseProvider
from src.providers.prompt_loader import PromptLoader, PromptTemplate
from src.contracts.evidence import DimensionObservation, EvidenceSpan
from src.contracts.request_models import (
    CoveragePlan,
    EvaluationRequest,
    NormalizedDocument,
)
from src.contracts.scoring import (
    AdjudicationRecord,
    FinalDimensionDecision,
    ScoreHypothesis,
)
from src.contracts.trace import CheckpointRef, NodeTrace, RunStatus, RunTrace
from src.orchestrator.checkpoints import CheckpointManager, RetryLimitExceeded
from src.orchestrator.graph import StateGraph
from src.orchestrator.router import route_after_adjudication, route_after_consistency_check
from src.orchestrator.states import PipelineState
from src.orchestrator.trace_store import TraceStore
from src.pipeline.validators import (
    terminal_validation,
    validate_coverage_plans,
    validate_final_decisions,
    validate_hypotheses,
    validate_observations,
)
from src.policies.aggregation import compute_composite


def _get_rater_ids(bundle: ResolvedArtifactBundle) -> List[str]:
    """Read required rater IDs from the adjudication policy (config-driven)."""
    return list(
        bundle.policy_snapshot.adjudication_policy
        .get("raters", {})
        .get("rater_labels", ["rater_1", "rater_2"])
    )


class PipelineRunner:
    """Drives the evaluation pipeline for one EvaluationRequest.

    Args:
        bundle          : A frozen ResolvedArtifactBundle (from ConfigCompiler).
        provider        : Default BaseProvider for real LLM calls (used by stages
                          and raters that have no specific override).  When None,
                          the pipeline uses deterministic mock workers.
        rater_providers : Optional per-rater provider map {rater_id: BaseProvider}.
                          Takes precedence over `provider` for the scoring stage.
        stage_providers : Optional per-stage provider map {stage_name: BaseProvider}.
                          Takes precedence over `provider` for named stages.
                          Recognised stage names: "evidence_extraction", "feedback".
        prompt_templates: Optional dict mapping template name to PromptTemplate.
                          Required for real provider mode.
    """

    def __init__(
        self,
        bundle: ResolvedArtifactBundle,
        provider: Optional[BaseProvider] = None,
        rater_providers: Optional[Dict[str, BaseProvider]] = None,
        stage_providers: Optional[Dict[str, BaseProvider]] = None,
        prompt_templates: Optional[Dict[str, PromptTemplate]] = None,
    ) -> None:
        self._bundle = bundle
        self._provider = provider
        self._rater_providers: Dict[str, BaseProvider] = rater_providers or {}
        self._stage_providers: Dict[str, BaseProvider] = stage_providers or {}
        self._prompt_templates = prompt_templates or {}
        self._last_hypotheses: List[ScoreHypothesis] = []

    @property
    def last_hypotheses(self) -> List[ScoreHypothesis]:
        """ScoreHypotheses produced in the most recent run() call.

        Contains one hypothesis per (rater, dimension) pair — e.g. 12 entries
        for 6 dimensions × 2 raters. Empty if run() has not been called or if
        the pipeline failed before the scoring stage.
        """
        return list(self._last_hypotheses)

    def _is_real(self) -> bool:
        return self._provider is not None or bool(self._rater_providers)

    def _provider_for_rater(self, rater_id: str) -> BaseProvider:
        """Return the provider to use for a specific rater.

        Priority: explicit rater_providers > default provider.
        Raises RuntimeError if no provider is available.
        """
        if rater_id in self._rater_providers:
            return self._rater_providers[rater_id]
        if self._provider is not None:
            return self._provider
        raise RuntimeError(
            f"No provider configured for rater '{rater_id}'. "
            "Pass a default provider or configure rater_providers."
        )

    def _provider_for_stage(self, stage: str) -> BaseProvider:
        """Return the provider to use for a named pipeline stage.

        Priority: explicit stage_providers > default provider.
        Raises RuntimeError if no provider is available.
        """
        if stage in self._stage_providers:
            return self._stage_providers[stage]
        if self._provider is not None:
            return self._provider
        raise RuntimeError(
            f"No provider configured for stage '{stage}'. "
            "Pass a default provider or configure stage_providers."
        )

    def _tpl(self, name: str) -> PromptTemplate:
        """Return a named PromptTemplate; raises KeyError if missing."""
        if name not in self._prompt_templates:
            raise KeyError(
                f"Prompt template '{name}' not found. "
                f"Available: {sorted(self._prompt_templates)}"
            )
        return self._prompt_templates[name]

    def run(
        self,
        request: EvaluationRequest,
    ) -> Tuple[RunTrace, Dict[str, Any]]:
        """Execute the evaluation pipeline.

        Returns:
            (RunTrace, feedback_dict) where feedback_dict is the output of
            feedback.run(). If the pipeline fails or is escalated to
            HUMAN_REVIEW, feedback_dict is empty {}.
        """
        bundle = self._bundle
        rubric = bundle.rubric_snapshot
        policy = bundle.policy_snapshot
        rater_ids = _get_rater_ids(bundle)

        run_id = f"run-{uuid4().hex[:12]}"
        bundle_id = bundle.artifact_bundle.bundle_id
        bundle_version = bundle.artifact_bundle.bundle_version
        request_id = request.request_id or f"req-{hashlib.md5(request.raw_text.encode()).hexdigest()[:12]}"

        graph = StateGraph()
        store = TraceStore(run_id, bundle_id, bundle_version, request_id)
        ckpt_mgr = CheckpointManager(run_id, max_retries=2)

        # Carry-forward pipeline data
        document: Optional[NormalizedDocument] = None
        plans: List[CoveragePlan] = []
        all_spans_by_dim: Dict[str, List[EvidenceSpan]] = {}
        observations: List[DimensionObservation] = []
        hypotheses: List[ScoreHypothesis] = []
        adj_records: List[AdjudicationRecord] = []
        decisions: Optional[List[FinalDimensionDecision]] = None

        try:
            # ── Stage 0: Config already resolved (bundle is pre-compiled) ──────
            graph.advance(PipelineState.CONFIG_RESOLVED)

            # ── Stage 1: Preprocess ──────────────────────────────────────────
            store.record_node_start("node_preprocess", "preprocess",
                                    input_ref=request_id)
            norm_req, document = preprocess.run(request)
            ckpt = ckpt_mgr.create_checkpoint(
                "node_preprocess", "preprocess", document.document_id
            )
            store.record_node_success(
                "node_preprocess",
                output_ref=document.document_id,
                checkpoint=ckpt,
            )
            graph.advance(PipelineState.PREPROCESSED)

            # ── Stage 2: Coverage planning ───────────────────────────────────
            store.record_node_start("node_coverage", "coverage",
                                    input_ref=document.document_id)
            plans = coverage.run(document, rubric)
            validate_coverage_plans(plans, rubric)
            ckpt = ckpt_mgr.create_checkpoint(
                "node_coverage", "coverage", document.document_id
            )
            store.record_node_success(
                "node_coverage",
                output_ref=f"plans:{len(plans)}",
                checkpoint=ckpt,
            )
            graph.advance(PipelineState.COVERAGE_PLANNED)

            # ── Main loop — supports RE_EXTRACT / RE_SCORE re-entry ──────────
            while not graph.is_terminal():
                cs = graph.current_state

                # RE_EXTRACT: re-enter pipeline at COVERAGE_PLANNED
                if cs == PipelineState.RE_EXTRACT:
                    graph.advance(PipelineState.COVERAGE_PLANNED)
                    cs = PipelineState.COVERAGE_PLANNED

                # COVERAGE_PLANNED → EVIDENCE_EXTRACTED
                if cs == PipelineState.COVERAGE_PLANNED:
                    store.record_node_start("node_extractor", "extract",
                                            input_ref=f"plans:{len(plans)}")
                    if self._is_real():
                        extraction_tpl = self._tpl("evidence_extraction")
                        extraction_provider = self._provider_for_stage("evidence_extraction")
                        all_spans_by_dim = {
                            plan.dimension_id: extractor.run(
                                plan, document, rubric, extraction_provider, extraction_tpl
                            )
                            for plan in plans
                        }
                    else:
                        all_spans_by_dim = {
                            plan.dimension_id: deterministic_extractor.run(plan, document)
                            for plan in plans
                        }
                    total_spans = sum(len(s) for s in all_spans_by_dim.values())
                    ckpt = ckpt_mgr.create_checkpoint(
                        "node_extractor", "extract", document.document_id
                    )
                    store.record_node_success(
                        "node_extractor",
                        output_ref=f"spans:{total_spans}",
                        checkpoint=ckpt,
                    )
                    graph.advance(PipelineState.EVIDENCE_EXTRACTED)
                    cs = PipelineState.EVIDENCE_EXTRACTED

                # EVIDENCE_EXTRACTED → OBSERVATION_BUILT
                if cs == PipelineState.EVIDENCE_EXTRACTED:
                    store.record_node_start("node_observer", "observe",
                                            input_ref=f"spans:{sum(len(s) for s in all_spans_by_dim.values())}")
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
                    store.record_node_success(
                        "node_observer",
                        output_ref=f"obs:{len(observations)}",
                        checkpoint=ckpt,
                    )
                    graph.advance(PipelineState.OBSERVATION_BUILT)
                    cs = PipelineState.OBSERVATION_BUILT

                # RE_SCORE: re-enter pipeline at OBSERVATION_BUILT
                if cs == PipelineState.RE_SCORE:
                    graph.advance(PipelineState.OBSERVATION_BUILT)
                    cs = PipelineState.OBSERVATION_BUILT

                # OBSERVATION_BUILT → SCORED
                if cs == PipelineState.OBSERVATION_BUILT:
                    store.record_node_start("node_scorer", "score",
                                            input_ref=f"obs:{len(observations)}")
                    if self._is_real():
                        scoring_tpl = self._tpl("scoring")
                        all_spans_flat = [
                            s for spans in all_spans_by_dim.values() for s in spans
                        ]
                        hypotheses = [
                            scorer.run(
                                obs, all_spans_flat, rubric, document,
                                self._provider_for_rater(rater_id), scoring_tpl, rater_id
                            )
                            for obs in observations
                            for rater_id in rater_ids
                        ]
                    else:
                        hypotheses = [
                            deterministic_scorer.run(obs, rubric, rater_id)
                            for obs in observations
                            for rater_id in rater_ids
                        ]
                    validate_hypotheses(hypotheses, plans, rater_ids)
                    self._last_hypotheses = list(hypotheses)
                    ckpt = ckpt_mgr.create_checkpoint(
                        "node_scorer", "score", document.document_id
                    )
                    store.record_node_success(
                        "node_scorer",
                        output_ref=f"hyps:{len(hypotheses)}",
                        checkpoint=ckpt,
                    )
                    graph.advance(PipelineState.SCORED)
                    cs = PipelineState.SCORED

                # SCORED → CONSISTENCY_CHECKED → route
                if cs == PipelineState.SCORED:
                    store.record_node_start("node_consistency_checker",
                                            "check_consistency",
                                            input_ref=f"hyps:{len(hypotheses)}")
                    if self._is_real():
                        conflicts = consistency_checker.run(hypotheses, policy)
                    else:
                        conflicts = deterministic_consistency_checker.run(hypotheses, policy)
                    store.record_node_success(
                        "node_consistency_checker",
                        output_ref=f"conflicts:{len(conflicts)}",
                    )
                    graph.advance(PipelineState.CONSISTENCY_CHECKED)

                    next_state = route_after_consistency_check(conflicts)

                    if next_state == PipelineState.FEEDBACK_RENDERED:
                        # No conflicts — create decisions directly from hypotheses
                        _, decisions = deterministic_adjudicator.run([], hypotheses, policy)
                        graph.advance(PipelineState.FEEDBACK_RENDERED)
                        break

                    elif next_state == PipelineState.ADJUDICATED:
                        # 真实 LLM 模式：冲突存在时按 ASAP Set 8 规则触发 rater_3 全文重评
                        if self._is_real():
                            resolution_rater = (
                                policy.adjudication_policy
                                .get("raters", {})
                                .get("resolution_rater_label", "rater_3")
                            )
                            store.record_node_start(
                                "node_rater3_scorer", "score_resolution",
                                input_ref=f"obs:{len(observations)}",
                            )
                            scoring_tpl = self._tpl("scoring")
                            all_spans_flat = [
                                s for spans in all_spans_by_dim.values() for s in spans
                            ]
                            rater3_hypotheses = [
                                scorer.run(
                                    obs, all_spans_flat, rubric, document,
                                    self._provider_for_rater(resolution_rater),
                                    scoring_tpl, resolution_rater,
                                )
                                for obs in observations
                            ]
                            hypotheses = hypotheses + rater3_hypotheses
                            store.record_node_success(
                                "node_rater3_scorer",
                                output_ref=f"r3_hyps:{len(rater3_hypotheses)}",
                            )

                        graph.advance(PipelineState.ADJUDICATED)
                        store.record_node_start("node_adjudicator", "adjudicate",
                                                input_ref=f"conflicts:{len(conflicts)}")
                        if self._is_real():
                            adj_records, decisions = adjudicator.run(
                                conflicts, hypotheses, policy
                            )
                        else:
                            adj_records, decisions = deterministic_adjudicator.run(
                                conflicts, hypotheses, policy
                            )
                        store.record_node_success(
                            "node_adjudicator",
                            output_ref=f"decisions:{len(decisions)}",
                        )

                        next_state2 = route_after_adjudication(adj_records)
                        graph.advance(next_state2)

                        if next_state2 == PipelineState.FEEDBACK_RENDERED:
                            break
                        elif next_state2 == PipelineState.HUMAN_REVIEW:
                            return (
                                store.build_run_trace(RunStatus.HUMAN_REVIEW),
                                {},
                            )
                        else:
                            # RE_EXTRACT or RE_SCORE from adjudication
                            fb_type = (
                                "re_extract"
                                if next_state2 == PipelineState.RE_EXTRACT
                                else "re_score"
                            )
                            try:
                                ckpt_mgr.record_fallback(fb_type)
                            except RetryLimitExceeded as exc:
                                store.record_force_fail(str(exc))
                                graph.force_fail()
                                return (
                                    store.build_run_trace(RunStatus.FAILED),
                                    {},
                                )

                    elif next_state == PipelineState.HUMAN_REVIEW:
                        graph.advance(PipelineState.HUMAN_REVIEW)
                        return (
                            store.build_run_trace(RunStatus.HUMAN_REVIEW),
                            {},
                        )

                    else:
                        # RE_EXTRACT or RE_SCORE directly from consistency checker
                        fb_type = (
                            "re_extract"
                            if next_state == PipelineState.RE_EXTRACT
                            else "re_score"
                        )
                        try:
                            ckpt_mgr.record_fallback(fb_type)
                            graph.advance(next_state)
                        except RetryLimitExceeded as exc:
                            store.record_force_fail(str(exc))
                            graph.force_fail()
                            return (
                                store.build_run_trace(RunStatus.FAILED),
                                {},
                            )
                    # Continue the while loop (handles RE_EXTRACT / RE_SCORE state)

            # ── Post-loop: graph is at FEEDBACK_RENDERED (or terminal) ────────
            if graph.is_terminal() and graph.current_state != PipelineState.VALIDATED:
                # Reached a terminal state other than VALIDATED without our break
                return store.build_run_trace(RunStatus.FAILED), {}

            if decisions is None:
                store.record_force_fail("Pipeline ended without producing decisions")
                graph.force_fail()
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

            # ── Stage: Feedback ──────────────────────────────────────────────
            store.record_node_start("node_feedback", "feedback",
                                    input_ref=f"decisions:{len(decisions)}")
            all_spans_flat = [s for spans in all_spans_by_dim.values() for s in spans]
            explanation_tpl = self._tpl("explanation") if self._is_real() else None
            feedback = feedback_agent.run(
                decisions=decisions,
                observations=observations,
                spans=all_spans_flat,
                rubric=rubric,
                policy=policy,
                provider=(
                    self._provider_for_stage("feedback")
                    if self._is_real() else None
                ),
                template=explanation_tpl,
            )

            # 将 composite 总分写入 feedback，保持输出结构统一
            feedback["composite"] = composite.to_dict() if composite is not None else None

            store.record_node_success(
                "node_feedback",
                output_ref=f"dims:{len(feedback.get('dimensions', {}))}",
            )

            # ── Terminal validation ──────────────────────────────────────────
            terminal_passed = terminal_validation(decisions, plans, rubric)

            graph.advance(PipelineState.VALIDATED)

            run_trace = store.build_run_trace(
                status=RunStatus.COMPLETED,
                terminal_validation_passed=terminal_passed,
                replay_metadata={"provider": self._provider.name if self._is_real() else "mock"},
            )
            return run_trace, feedback

        except Exception as exc:
            store.record_force_fail(str(exc))
            graph.force_fail()
            return store.build_run_trace(RunStatus.FAILED), {}
