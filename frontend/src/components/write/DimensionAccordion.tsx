import { Accordion } from "@/components/ui/accordion";
import { DimensionItem } from "./DimensionItem";
import type { DimensionVM } from "@/lib/dataTransforms";

interface Props {
  dimensions: DimensionVM[];
}

export function DimensionAccordion({ dimensions }: Props) {
  return (
    <div className="border-t">
      <div className="px-4 py-2 bg-gray-50 border-b">
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
          分歧判定与证据溯源 · Write
        </span>
      </div>
      <div className="p-3">
        <Accordion className="w-full">
          {dimensions.map((dim, i) => (
            <DimensionItem key={dim.id} dim={dim} index={i} />
          ))}
        </Accordion>
      </div>
    </div>
  );
}
