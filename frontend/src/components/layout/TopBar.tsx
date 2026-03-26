import { useEffect, useState } from "react";
import { essayService } from "@/services/essayService";

interface Props {
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function TopBar({ selectedId, onSelect }: Props) {
  const [essayIds, setEssayIds] = useState<string[]>([]);

  useEffect(() => {
    essayService.listEssays().then((res) => {
      setEssayIds(res.essay_ids);
      if (!selectedId && res.essay_ids.length > 0) {
        onSelect(res.essay_ids[0]);
      }
    });
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <header className="h-11 border-b bg-white flex items-center px-4 gap-4 shrink-0 shadow-sm">
      <div className="font-bold text-sm text-gray-800 tracking-tight">
        MAS 自动评估诊断系统
      </div>
      <div className="flex items-center gap-2 ml-auto">
        <label className="text-xs text-gray-500 font-medium" htmlFor="essay-select">
          样本
        </label>
        <select
          id="essay-select"
          value={selectedId ?? ""}
          onChange={(e) => onSelect(e.target.value)}
          className="text-sm border rounded px-2 py-1 bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-300"
        >
          {essayIds.map((id) => (
            <option key={id} value={id}>
              #{id}
            </option>
          ))}
        </select>
        <span className="text-xs text-gray-400 ml-2 hidden sm:inline">
          {essayIds.length} 篇已评估
        </span>
      </div>
    </header>
  );
}
