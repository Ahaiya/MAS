// composite = 2×I + 2×O + 2×S + 4×C（Voice 和 Word Choice 权重为 0）
// 理论满分：(2+2+2+4) × 6 = 60

import { DIMENSIONS } from "./constants";

export interface ScoreMap {
  [dimensionId: string]: number | null;
}

export function calcComposite(scores: ScoreMap): number | null {
  let total = 0;
  for (const dim of DIMENSIONS) {
    if (dim.weight === 0) continue;
    const score = scores[dim.id];
    if (score == null) return null;
    total += dim.weight * score;
  }
  return total;
}

export const COMPOSITE_MAX = 60;
