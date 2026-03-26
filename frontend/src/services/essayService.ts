const BASE = "/api";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`GET ${path} → ${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const essayService = {
  listEssays: () => get<{ essay_ids: string[] }>("/essays"),

  getText: (essayId: string) =>
    get<{ essay_id: string; text: string }>(`/essays/${essayId}/text`),

  getFeedback: (essayId: string) =>
    get<Record<string, unknown>>(`/essays/${essayId}/feedback`),

  getTrace: (essayId: string) =>
    get<Record<string, unknown>>(`/essays/${essayId}/trace`),

  getHypotheses: (essayId: string) =>
    get<{ run_id: string; hypotheses: unknown[] }>(`/essays/${essayId}/hypotheses`),

  getHumanScores: (essayId: string) =>
    get<{ essay_id: string; dimensions: Record<string, unknown> }>(
      `/essays/${essayId}/human-scores`
    ),
};
