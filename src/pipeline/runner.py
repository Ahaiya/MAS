"""
Pipeline Runner — drives the mock evaluation pipeline state machine.

PipelineRunner is the single point of orchestration. It:
1. Advances the StateGraph through legal transitions.
2. Calls mock worker run() functions in order.
3. Records node lifecycle events in TraceStore.
4. Manages fallback retry counts via CheckpointManager.
5. Routes decisions using router functions (no inline routing logic here).
6. Returns a (RunTrace, feedback) tuple when the pipeline reaches a terminal state.

Design invariants:
- All data flows via Phase 2 contracts — no ad-hoc dicts between stages.
- No business logic (trait names, thresholds, formulas) is embedded here.
  All such values are read from RubricSnapshot / PolicySnapshot.
- RE_EXTRACT / RE_SCORE loops are guarded by CheckpointManager.record_fallback().
  Exceeding max_retries forces the pipeline to FAILED.
- HUMAN_REVIEW is a terminal path — the runner returns immediately with that status.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from src.agents import (
    mock_adjudicator,
    mock_consistency_checker,
    mock_coverage,
    mock_extractor,
    mock_feedback,
    mock_observer,
    mock_preprocess,
    mock_scorer,
    real_extractor,
    real_feedback,
    real_scorer,
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
        bundle   : A frozen ResolvedArtifactBundle (from ConfigCompiler).
        provider : Optional BaseProvider for real LLM calls.  When None (default)
                   the pipeline uses deterministic mock workers.  When set, the
                   extraction, scoring and feedback stages call the real LLM.
        prompt_templates: Optional dict mapping template name to PromptTemplate
                   for real provider mode.  Required when provider is not None.
    """

    def __init__(
        self,
        bundle: ResolvedArtifactBundle,
        provider: Optional[BaseProvider] = None,
        prompt_templates: Optional[Dict[str, PromptTemplate]] = None,
    ) -> None:
        self._bundle = bundle
        self._provider = provider
        self._prompt_templates = prompt_templates or {}

    def _is_real(self) -> bool:
        return self._provider is not None

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
            mock_feedback.run(). If the pipeline fails or is escalated to
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
        decisions: Optional[List[FinalDimensionDecision]] = None

        try:
            # ── Stage 0: Config already resolved (bundle is pre-compiled) ──────
            graph.advance(PipelineState.CONFIG_RESOLVED)

            # ── Stage 1: Preprocess ──────────────────────────────────────────
            store.record_node_start("node_preprocess", "preprocess",
                                    input_ref=request_id)
            norm_req, document = mock_preprocess.run(request)
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
            plans = mock_coverage.run(document, rubric)
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
                        all_spans_by_dim = {
                            plan.dimension_id: real_extractor.run(
                                plan, document, rubric, self._provider, extraction_tpl
                            )
                            for plan in plans
                        }
                    else:
                        all_spans_by_dim = {
                            plan.dimension_id: mock_extractor.run(plan, document)
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
                        mock_observer.run(
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
                            real_scorer.run(
                                obs, all_spans_flat, rubric, document,
                                self._provider, scoring_tpl, rater_id
                            )
                            for obs in observations
                            for rater_id in rater_ids
                        ]
                    else:
                        hypotheses = [
                            mock_scorer.run(obs, rubric, rater_id)
                            for obs in observations
                            for rater_id in rater_ids
                        ]
                    validate_hypotheses(hypotheses, plans, rater_ids)
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
                    conflicts = mock_consistency_checker.run(hypotheses, policy)
                    store.record_node_success(
                        "node_consistency_checker",
                        output_ref=f"conflicts:{len(conflicts)}",
                    )
                    graph.advance(PipelineState.CONSISTENCY_CHECKED)

                    next_state = route_after_consistency_check(conflicts)

                    if next_state == PipelineState.FEEDBACK_RENDERED:
                        # No conflicts — create decisions directly from hypotheses
                        _, decisions = mock_adjudicator.run([], hypotheses, policy)
                        graph.advance(PipelineState.FEEDBACK_RENDERED)
                        break

                    elif next_state == PipelineState.ADJUDICATED:
                        graph.advance(PipelineState.ADJUDICATED)
                        store.record_node_start("node_adjudicator", "adjudicate",
                                                input_ref=f"conflicts:{len(conflicts)}")
                        adj_records, decisions = mock_adjudicator.run(
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

            # ── Stage: Feedback ──────────────────────────────────────────────
            validate_final_decisions(decisions, plans)
            store.record_node_start("node_feedback", "feedback",
                                    input_ref=f"decisions:{len(decisions)}")
            if self._is_real():
                explanation_tpl = self._tpl("explanation")
                all_spans_flat = [s for spans in all_spans_by_dim.values() for s in spans]
                feedback = real_feedback.run(
                    decisions, observations, rubric,
                    all_spans_flat, self._provider, explanation_tpl
                )
            else:
                feedback = mock_feedback.run(decisions, observations, rubric)
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
