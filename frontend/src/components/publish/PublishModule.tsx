import { DimensionRadarChart } from "./DimensionRadarChart";
import { calcComposite, COMPOSITE_MAX } from "@/lib/compositeScore";
import type { DimensionVM } from "@/lib/dataTransforms";

interface Props {
  dimensions: DimensionVM[];
}

export function PublishModule({ dimensions }: Props) {
  const masScores  = Object.fromEntries(dimensions.map((d) => [d.id, d.masScore]));
  const humanScores = Object.fromEntries(dimensions.map((d) => [d.id, d.humanMean]));

  const masComposite   = calcComposite(masScores);
  const humanComposite = calcComposite(humanScores as Record<string, number | null>);

  return (
    <div className="border-t">
      <div className="px-4 py-2 bg-gray-50 border-b">
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
          终评雷达图 · Publish
        </span>
      </div>
      <div className="p-4">
        {/* 合计分栏 */}
        <div className="flex items-center justify-around mb-3 text-center">
          <div>
            <div className="text-2xl font-bold text-blue-600">{masComposite ?? "—"}</div>
            <div className="text-xs text-gray-500">MAS 合计</div>
          </div>
          <div className="text-gray-300 text-xl">/</div>
          <div>
            <div className="text-2xl font-bold text-red-500">
              {humanComposite != null ? humanComposite.toFixed(1) : "—"}
            </div>
            <div className="text-xs text-gray-500">人类均分合计</div>
          </div>
          <div className="text-gray-300 text-xl">/</div>
          <div>
            <div className="text-2xl font-bold text-gray-400">{COMPOSITE_MAX}</div>
            <div className="text-xs text-gray-500">满分</div>
          </div>
        </div>

        <DimensionRadarChart dimensions={dimensions} />

        <p className="text-xs text-gray-400 text-center mt-1">
          公式：2×Ideas + 2×Org + 2×Fluency + 4×Conv（Voice / Word Choice 不计入）
        </p>
      </div>
    </div>
  );
}
