import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { DIMENSIONS } from "@/lib/constants";
import type { RaterVM } from "@/lib/dataTransforms";

interface Props {
  raters: RaterVM[];
}

const SCORE_COLORS = [
  "", "text-red-500", "text-orange-500", "text-yellow-600",
  "text-blue-500", "text-green-500", "text-emerald-600",
];

function RaterColumn({ rater }: { rater: RaterVM }) {
  const [openRationale, setOpenRationale] = useState<string | null>(null);

  return (
    <div className="flex-1 min-w-0">
      <div className="text-xs font-semibold text-gray-500 mb-2 text-center">
        {rater.raterId === "rater_1" ? "评审员 A" : "评审员 B"}
      </div>
      <div className="space-y-1.5">
        {DIMENSIONS.map((dimMeta) => {
          const row = rater.dimensionScores.find((s) => s.dimensionId === dimMeta.id);
          if (!row) return null;
          const isOpen = openRationale === dimMeta.id;
          return (
            <div key={dimMeta.id} className="rounded border bg-white overflow-hidden">
              <button
                className="w-full flex items-center gap-2 px-2 py-1.5 text-left hover:bg-gray-50 transition-colors"
                onClick={() => setOpenRationale(isOpen ? null : dimMeta.id)}
              >
                {/* 一致性图标 */}
                <span
                  className={`w-2 h-2 rounded-full shrink-0 ${row.conflict ? "bg-red-400" : "bg-green-400"}`}
                />
                {/* 维度简称 */}
                <span className="text-xs text-gray-600 flex-1 truncate">{dimMeta.shortLabel}</span>
                {/* 分数 */}
                <span className={`text-sm font-bold ${SCORE_COLORS[row.score] ?? "text-gray-700"}`}>
                  {row.score}
                </span>
                {/* 展开图标 */}
                {row.rationale ? (
                  isOpen ? <ChevronDown className="w-3 h-3 text-gray-400 shrink-0" />
                          : <ChevronRight className="w-3 h-3 text-gray-400 shrink-0" />
                ) : null}
              </button>

              {/* Rationale 折叠区（不截断） */}
              {isOpen && row.rationale && (
                <div className="px-2 pb-2 pt-1 border-t bg-gray-50">
                  <p className="text-xs text-gray-600 leading-5 whitespace-pre-wrap">{row.rationale}</p>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function DualRaterCard({ raters }: Props) {
  if (raters.length === 0) return null;

  const conflictCount = raters[0]?.dimensionScores.filter((s) => s.conflict).length ?? 0;

  return (
    <div className="mt-3 rounded-lg border bg-gray-50 p-3">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-semibold text-gray-600">双盲评审对照</span>
        {conflictCount > 0 ? (
          <span className="text-xs bg-red-100 text-red-600 px-2 py-0.5 rounded-full font-medium">
            {conflictCount} 维度存在分歧
          </span>
        ) : (
          <span className="text-xs bg-green-100 text-green-600 px-2 py-0.5 rounded-full font-medium">
            全维度一致
          </span>
        )}
      </div>
      <div className="flex gap-3">
        {raters.map((r) => (
          <RaterColumn key={r.raterId} rater={r} />
        ))}
      </div>
      <p className="text-xs text-gray-400 mt-2 text-center">
        点击各维度行可展开评审员原始推理（rationale）
      </p>
    </div>
  );
}
