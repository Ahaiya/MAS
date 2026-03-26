import { useMemo } from "react";
import { useHighlight } from "@/context/HighlightContext";

interface Props {
  text: string;
}

export function HighlightableText({ text }: Props) {
  const { activePhrase } = useHighlight();

  const segments = useMemo(() => {
    if (!activePhrase || activePhrase.trim().length < 4) {
      return [{ text, highlighted: false }];
    }

    const phrase = activePhrase.trim();
    const parts: { text: string; highlighted: boolean }[] = [];
    let remaining = text;

    while (remaining.length > 0) {
      const idx = remaining.toLowerCase().indexOf(phrase.toLowerCase());
      if (idx === -1) {
        parts.push({ text: remaining, highlighted: false });
        break;
      }
      if (idx > 0) parts.push({ text: remaining.slice(0, idx), highlighted: false });
      parts.push({ text: remaining.slice(idx, idx + phrase.length), highlighted: true });
      remaining = remaining.slice(idx + phrase.length);
    }

    return parts;
  }, [text, activePhrase]);

  return (
    <p className="whitespace-pre-wrap leading-8 text-sm text-gray-800 font-serif">
      {segments.map((seg, i) =>
        seg.highlighted ? (
          <mark key={i} className="bg-yellow-200 rounded px-0.5 transition-colors duration-200">
            {seg.text}
          </mark>
        ) : (
          <span key={i}>{seg.text}</span>
        )
      )}
    </p>
  );
}
