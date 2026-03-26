import ReactMarkdown from "react-markdown";
import { useHighlight } from "@/context/HighlightContext";
import type { ComponentProps } from "react";

interface Props {
  text: string;
}

type StrongProps = ComponentProps<"strong">;

export function FeedbackMarkdown({ text }: Props) {
  const { setActivePhrase } = useHighlight();

  return (
    <div className="prose prose-sm max-w-none text-gray-700">
      <ReactMarkdown
        components={{
          strong: ({ children, ...props }: StrongProps) => {
            const phrase = typeof children === "string" ? children : String(children ?? "");
            return (
              <strong
                {...props}
                className="font-semibold text-gray-900 cursor-pointer underline decoration-dotted decoration-blue-400 hover:bg-blue-50 rounded px-0.5 transition-colors"
                onMouseEnter={() => setActivePhrase(phrase)}
                onMouseLeave={() => setActivePhrase(null)}
              >
                {children}
              </strong>
            );
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
