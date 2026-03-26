import { useState } from "react";
import { Group as PanelGroup, Panel, Separator as PanelResizeHandle } from "react-resizable-panels";
import { TopBar } from "@/components/layout/TopBar";
import { EssayReader } from "@/components/read/EssayReader";
import { PipelineTimeline } from "@/components/think/PipelineTimeline";
import { DimensionAccordion } from "@/components/write/DimensionAccordion";
import { PublishModule } from "@/components/publish/PublishModule";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { useEssayReport } from "@/hooks/useEssayReport";

function LoadingSkeleton() {
  return (
    <div className="p-6 space-y-3">
      <Skeleton className="h-4 w-3/4" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-5/6" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-2/3" />
    </div>
  );
}

export function ReportPage() {
  const [essayId, setEssayId] = useState<string | null>(null);
  const { report, loading, error } = useEssayReport(essayId);

  return (
    <div className="flex flex-col h-screen">
      <TopBar selectedId={essayId} onSelect={setEssayId} />

      <div className="flex-1 min-h-0">
        <PanelGroup orientation="horizontal" className="h-full">
          {/* 左侧：Read 模块 */}
          <Panel defaultSize={40} minSize={25}>
            <div className="h-full border-r">
              {loading && <LoadingSkeleton />}
              {error && (
                <div className="p-6 text-sm text-red-500">
                  加载失败：{error}
                </div>
              )}
              {report && (
                <EssayReader essayId={report.essayId} text={report.essayText} />
              )}
              {!loading && !error && !report && (
                <div className="p-6 text-sm text-gray-400">请从顶部选择样本</div>
              )}
            </div>
          </Panel>

          <PanelResizeHandle className="w-1.5 bg-gray-100 hover:bg-blue-200 transition-colors cursor-col-resize" />

          {/* 右侧：Think + Write + Publish */}
          <Panel defaultSize={60} minSize={30}>
            <ScrollArea className="h-full">
              {loading && <LoadingSkeleton />}
              {error && (
                <div className="p-6 text-sm text-red-500">加载失败：{error}</div>
              )}
              {report && (
                <>
                  <PipelineTimeline
                    pipeline={report.pipeline}
                    raters={report.raters}
                    runMeta={report.runMeta}
                  />
                  <DimensionAccordion dimensions={report.dimensions} />
                  <PublishModule dimensions={report.dimensions} />
                </>
              )}
            </ScrollArea>
          </Panel>
        </PanelGroup>
      </div>
    </div>
  );
}
