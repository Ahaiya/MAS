import { ScrollArea } from "@/components/ui/scroll-area";
import { HighlightableText } from "./HighlightableText";

interface Props {
  essayId: string;
  text: string;
}

export function EssayReader({ essayId, text }: Props) {
  const wordCount = text.trim().split(/\s+/).length;

  return (
    <div className="flex flex-col h-full">
      <div className="px-5 py-3 border-b bg-gray-50 flex items-center gap-3 shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">原始案卷</span>
          <span className="text-xs bg-gray-200 text-gray-600 rounded px-2 py-0.5 font-mono">#{essayId}</span>
        </div>
        <span className="text-xs text-gray-400 ml-auto">{wordCount} 词</span>
      </div>
      <ScrollArea className="flex-1">
        <div className="p-6">
          <HighlightableText text={text} />
        </div>
      </ScrollArea>
    </div>
  );
}
