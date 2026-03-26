// 将后端 JSON 转换为前端 ViewModel

import { DIMENSIONS, NODE_TYPE_LABELS, type DimensionId } from "./constants";

// ── 后端类型（部分字段） ─────────────────────────────────────────────────────────

export interface ApiDimensionFeedback {
  dimension_name: string;
  canonical_score: number;
  display_score: string;
  descriptor_refs: string[];
  evidence_span_ids: string[];
  feedback_text: string;
  confidence: number;
}

export interface ApiHypothesis {
  hypothesis_id: string;
  dimension_id: string;
  rater_id: string;
  score: { canonical_score: number; display_score: string };
  descriptor_refs: string[];
  rationale: string;
  confidence: number;
}

export interface ApiNodeTrace {
  node_id: string;
  node_type: string;
  status: string;
  started_at: string;
  finished_at: string;
  output_ref: string;
  fallback_history: unknown[];
  error_message: string | null;
}

export interface ApiHumanDim {
  rater1: number | null;
  rater2: number | null;
  mean: number | null;
}

// ── 前端 ViewModel ───────────────────────────────────────────────────────────────

export interface DimensionVM {
  id: DimensionId;
  label: string;
  shortLabel: string;
  weight: number;
  masScore: number;
  humanMean: number | null;
  humanRater1: number | null;
  humanRater2: number | null;
  deviation: number | null;        // masScore - humanMean
  confidence: number;
  descriptors: string[];
  feedbackText: string;
  evidenceCount: number;
}

export interface PipelineStepVM {
  nodeId: string;
  nodeType: string;
  label: string;
  status: string;
  durationSeconds: number | null;
  outputRef: string;
  hasFallback: boolean;
  errorMessage: string | null;
}

export interface RaterVM {
  raterId: string;
  dimensionScores: { dimensionId: string; score: number; rationale: string; conflict: boolean }[];
}

export interface RunMetaVM {
  runId: string;
  bundleVersion: string;
  bundleId: string;
  status: string;
  startedAt: string;
  finishedAt: string;
  durationSeconds: number;
  validationPassed: boolean;
  provider: string;
}

export interface EssayReportVM {
  essayId: string;
  essayText: string;
  runMeta: RunMetaVM;
  pipeline: PipelineStepVM[];
  dimensions: DimensionVM[];
  raters: RaterVM[];
  compositeScore: number | null;
}

// ── 转换函数 ──────────────────────────────────────────────────────────────────────

function durationSec(startedAt: string, finishedAt: string): number | null {
  try {
    return Math.round((new Date(finishedAt).getTime() - new Date(startedAt).getTime()) / 1000);
  } catch {
    return null;
  }
}

export function buildRunMeta(trace: Record<string, unknown>): RunMetaVM {
  const meta = (trace.replay_metadata as Record<string, string>) ?? {};
  return {
    runId:             String(trace.run_id ?? ""),
    bundleVersion:     String(trace.bundle_version ?? ""),
    bundleId:          String(trace.bundle_id ?? ""),
    status:            String(trace.status ?? ""),
    startedAt:         String(trace.started_at ?? ""),
    finishedAt:        String(trace.finished_at ?? ""),
    durationSeconds:   durationSec(String(trace.started_at), String(trace.finished_at)) ?? 0,
    validationPassed:  Boolean(trace.terminal_validation_passed),
    provider:          meta.provider ?? "unknown",
  };
}

export function buildPipeline(nodeTraces: ApiNodeTrace[]): PipelineStepVM[] {
  return nodeTraces.map((n) => ({
    nodeId:          n.node_id,
    nodeType:        n.node_type,
    label:           NODE_TYPE_LABELS[n.node_type] ?? n.node_type,
    status:          n.status,
    durationSeconds: durationSec(n.started_at, n.finished_at),
    outputRef:       n.output_ref ?? "",
    hasFallback:     (n.fallback_history ?? []).length > 0,
    errorMessage:    n.error_message ?? null,
  }));
}

export function buildDimensions(
  feedbackDims: Record<string, ApiDimensionFeedback>,
  humanDims: Record<string, ApiHumanDim>,
): DimensionVM[] {
  return DIMENSIONS.map((dimMeta) => {
    const fb  = feedbackDims[dimMeta.id];
    const hum = humanDims[dimMeta.id];
    const masScore    = fb?.canonical_score ?? 0;
    const humanMean   = hum?.mean ?? null;
    return {
      id:           dimMeta.id,
      label:        dimMeta.label,
      shortLabel:   dimMeta.shortLabel,
      weight:       dimMeta.weight,
      masScore,
      humanMean,
      humanRater1:  hum?.rater1 ?? null,
      humanRater2:  hum?.rater2 ?? null,
      deviation:    humanMean != null ? Math.round((masScore - humanMean) * 10) / 10 : null,
      confidence:   fb?.confidence ?? 0,
      descriptors:  fb?.descriptor_refs ?? [],
      feedbackText: fb?.feedback_text ?? "",
      evidenceCount: (fb?.evidence_span_ids ?? []).length,
    };
  });
}

export function buildRaters(
  hypotheses: ApiHypothesis[],
  dimensions: DimensionVM[],
): RaterVM[] {
  const raterIds = Array.from(new Set(hypotheses.map((h) => h.rater_id))).sort();

  return raterIds.map((raterId) => {
    const raterHyps = hypotheses.filter((h) => h.rater_id === raterId);

    const dimensionScores = DIMENSIONS.map((dimMeta) => {
      const hyp = raterHyps.find((h) => h.dimension_id === dimMeta.id);
      // 找另一个 rater 的同维度分数，判断是否有冲突
      const otherRaterScore = hypotheses
        .filter((h) => h.rater_id !== raterId && h.dimension_id === dimMeta.id)
        .map((h) => h.score.canonical_score)[0];
      const myScore = hyp?.score.canonical_score ?? 0;
      const conflict = otherRaterScore != null && Math.abs(myScore - otherRaterScore) >= 2;

      const dim = dimensions.find((d) => d.id === dimMeta.id);
      void dim; // used for type safety

      return {
        dimensionId: dimMeta.id,
        score:       myScore,
        rationale:   hyp?.rationale ?? "",
        conflict,
      };
    });

    return { raterId, dimensionScores };
  });
}
