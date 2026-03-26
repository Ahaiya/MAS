import { useState, useEffect } from "react";
import { essayService } from "@/services/essayService";
import {
  buildRunMeta,
  buildPipeline,
  buildDimensions,
  buildRaters,
  type EssayReportVM,
  type ApiNodeTrace,
  type ApiHypothesis,
  type ApiDimensionFeedback,
  type ApiHumanDim,
} from "@/lib/dataTransforms";
import { calcComposite } from "@/lib/compositeScore";

interface UseEssayReportResult {
  report: EssayReportVM | null;
  loading: boolean;
  error: string | null;
}

export function useEssayReport(essayId: string | null): UseEssayReportResult {
  const [report, setReport] = useState<EssayReportVM | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!essayId) {
      setReport(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);
    setReport(null);

    Promise.all([
      essayService.getText(essayId),
      essayService.getFeedback(essayId),
      essayService.getTrace(essayId),
      essayService.getHypotheses(essayId),
      essayService.getHumanScores(essayId),
    ])
      .then(([textRes, feedbackRes, traceRes, hypothesesRes, humanRes]) => {
        if (cancelled) return;

        const feedbackDims = (feedbackRes.dimensions ?? {}) as Record<string, ApiDimensionFeedback>;
        const humanDims    = (humanRes.dimensions ?? {}) as Record<string, ApiHumanDim>;
        const nodeTraces   = ((traceRes.node_traces as ApiNodeTrace[]) ?? []);
        const hypotheses   = ((hypothesesRes.hypotheses as ApiHypothesis[]) ?? []);

        const dimensions = buildDimensions(feedbackDims, humanDims);
        const raters     = buildRaters(hypotheses, dimensions);

        const scoreMap = Object.fromEntries(dimensions.map((d) => [d.id, d.masScore]));
        const humanMap = Object.fromEntries(dimensions.map((d) => [d.id, d.humanMean]));

        const vm: EssayReportVM = {
          essayId,
          essayText:     textRes.text,
          runMeta:       buildRunMeta(traceRes),
          pipeline:      buildPipeline(nodeTraces),
          dimensions,
          raters,
          compositeScore: calcComposite(scoreMap),
        };

        void humanMap; // available for future use

        setReport(vm);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [essayId]);

  return { report, loading, error };
}
