import { HighlightProvider } from "@/context/HighlightContext";
import { ReportPage } from "@/pages/ReportPage";

export default function App() {
  return (
    <HighlightProvider>
      <ReportPage />
    </HighlightProvider>
  );
}
