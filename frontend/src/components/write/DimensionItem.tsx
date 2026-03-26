import {
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { DeviationBadge } from "./DeviationBadge";
import { FeedbackMarkdown } from "./FeedbackMarkdown";
import type { DimensionVM } from "@/lib/dataTransforms";

interface Props {
  dim: DimensionVM;
  index: number;
}

const SCORE_COLORS = ["", "bg-red-400", "bg-orange-400", "bg-yellow-400", "bg-blue-400", "bg-green-400", "bg-emerald-500"];

export function DimensionItem({ dim, index }: Props) {
  return (
    <AccordionItem value={`dim-${index}`} className="border rounded mb-1.5 overflow-hidden">
      <AccordionTrigger className="px-3 py-2 hover:no-underline hover:bg-gray-50 [&>svg]:shrink-0">
        <div className="flex items-center gap-2 w-full min-w-0 text-left">
          {/* 维度名 */}
          <span className="font-medium text-sm text-gray-800 truncate flex-1">{dim.label}</span>

          {/* MAS 分 */}
          <div className="flex items-center gap-1 shrink-0">
            <span
              className={`w-6 h-6 rounded-full text-white text-xs font-bold flex items-center justify-center ${SCORE_COLORS[dim.masScore] ?? "bg-gray-400"}`}
            >
              {dim.masScore}
            </span>
            <span className="text-xs text-gray-400">/6</span>
          </div>

          {/* 人类均分 */}
          {dim.humanMean != null && (
            <span className="text-xs text-gray-500 shrink-0">人 {dim.humanMean.toFixed(1)}</span>
          )}

          {/* 偏差 */}
          <div className="shrink-0">
            <DeviationBadge deviation={dim.deviation} />
          </div>
        </div>
      </AccordionTrigger>

      <AccordionContent className="px-3 pb-3 pt-1 space-y-3">
        {/* 置信度 */}
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500 w-12 shrink-0">置信度</span>
          <Progress value={dim.confidence * 100} className="h-1.5 flex-1" />
          <span className="text-xs text-gray-500 w-8 text-right">{Math.round(dim.confidence * 100)}%</span>
        </div>

        {/* 证据数量 */}
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500">证据来源</span>
          <Badge variant="secondary" className="text-xs">{dim.evidenceCount} 条</Badge>
        </div>

        {/* 评分依据描述语 */}
        {dim.descriptors.length > 0 && (
          <div>
            <div className="text-xs font-medium text-gray-500 mb-1">评分依据</div>
            <ul className="space-y-0.5">
              {dim.descriptors.map((d, i) => (
                <li key={i} className="text-xs text-gray-600 flex items-start gap-1">
                  <span className="text-blue-400 mt-0.5">›</span>
                  {d}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* AI 评语 */}
        {dim.feedbackText && (
          <div>
            <div className="text-xs font-medium text-gray-500 mb-1">AI 评语</div>
            <FeedbackMarkdown text={dim.feedbackText} />
          </div>
        )}
      </AccordionContent>
    </AccordionItem>
  );
}
