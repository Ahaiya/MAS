import { createContext, useContext, useState, type ReactNode } from "react";

interface HighlightContextValue {
  activePhrase: string | null;
  setActivePhrase: (phrase: string | null) => void;
}

const HighlightContext = createContext<HighlightContextValue>({
  activePhrase: null,
  setActivePhrase: () => {},
});

export function HighlightProvider({ children }: { children: ReactNode }) {
  const [activePhrase, setActivePhrase] = useState<string | null>(null);
  return (
    <HighlightContext.Provider value={{ activePhrase, setActivePhrase }}>
      {children}
    </HighlightContext.Provider>
  );
}

export function useHighlight() {
  return useContext(HighlightContext);
}
