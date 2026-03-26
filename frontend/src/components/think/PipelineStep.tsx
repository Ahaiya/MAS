import { CheckCircle2, XCircle, Clock, AlertTriangle } from "lucide-react";
import type { PipelineStepVM } from "@/lib/dataTransforms";

interface Props {
  step: PipelineStepVM;
  isLast: boolean;
}

function StatusIcon({ status }: { status: string }) {
  if (status === "success")
    return <CheckCircle2 className="w-4 h-4 text-green-500 shrink-0" />;
  if (status === "failed")
    return <XCircle className="w-4 h-4 text-red-500 shrink-0" />;
  return <Clock className="w-4 h-4 text-gray-400 shrink-0" />;
}

export function PipelineStep({ step, isLast }: Props) {
  return (
    <div className="flex gap-3">
      {/* 竖线连接器 */}
      <div className="flex flex-col items-center">
        <StatusIcon status={step.status} />
        {!isLast && <div className="w-px flex-1 bg-gray-200 mt-1" />}
      </div>

      {/* 内容 */}
      <div className={`pb-3 flex-1 min-w-0 ${isLast ? "" : ""}`}>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-medium text-gray-800">{step.label}</span>
          {step.outputRef && (
            <span className="text-xs font-mono text-blue-500 bg-blue-50 px-1.5 py-0.5 rounded">
              {step.outputRef}
            </span>
          )}
          {step.durationSeconds != null && (
            <span className="text-xs text-gray-400 ml-auto">
              {step.durationSeconds >= 60
                ? `${Math.floor(step.durationSeconds / 60)}m ${step.durationSeconds % 60}s`
                : `${step.durationSeconds}s`}
            </span>
          )}
        </div>
        {step.hasFallback && (
          <div className="flex items-center gap-1 mt-0.5">
            <AlertTriangle className="w-3 h-3 text-amber-500" />
            <span className="text-xs text-amber-600">发生过重试</span>
          </div>
        )}
        {step.errorMessage && (
          <p className="text-xs text-red-500 mt-0.5">{step.errorMessage}</p>
        )}
      </div>
    </div>
  );
}
