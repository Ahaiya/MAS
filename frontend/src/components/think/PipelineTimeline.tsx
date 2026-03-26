import { PipelineStep } from "./PipelineStep";
import { DualRaterCard } from "./DualRaterCard";
import type { PipelineStepVM, RaterVM } from "@/lib/dataTransforms";
import type { RunMetaVM } from "@/lib/dataTransforms";
import { CheckCircle2, XCircle } from "lucide-react";

interface Props {
  pipeline: PipelineStepVM[];
  raters: RaterVM[];
  runMeta: RunMetaVM;
}

export function PipelineTimeline({ pipeline, raters, runMeta }: Props) {
  return (
    <div className="border-t">
      <div className="px-4 py-2 bg-gray-50 border-b flex items-center justify-between">
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
          MAS 认知工作流 · Think
        </span>
        <div className="flex items-center gap-2">
          {runMeta.validationPassed ? (
            <CheckCircle2 className="w-3.5 h-3.5 text-green-500" />
          ) : (
            <XCircle className="w-3.5 h-3.5 text-red-500" />
          )}
          <span className="text-xs text-gray-500 font-mono">{runMeta.runId}</span>
          <span className="text-xs text-gray-400">
            {runMeta.durationSeconds >= 60
              ? `${Math.floor(runMeta.durationSeconds / 60)}m ${runMeta.durationSeconds % 60}s`
              : `${runMeta.durationSeconds}s`}
          </span>
        </div>
      </div>
      <div className="p-4">
        {pipeline.map((step, i) => (
          <PipelineStep key={step.nodeId} step={step} isLast={i === pipeline.length - 1} />
        ))}
        <DualRaterCard raters={raters} />
      </div>
    </div>
  );
}
