// 维度固定顺序与元数据
export const DIMENSIONS = [
  { id: "ideas_content",    label: "Ideas & Content",    shortLabel: "Ideas",    weight: 2 },
  { id: "organization",     label: "Organization",        shortLabel: "Org",      weight: 2 },
  { id: "voice",            label: "Voice",               shortLabel: "Voice",    weight: 0 },
  { id: "word_choice",      label: "Word Choice",         shortLabel: "Words",    weight: 0 },
  { id: "sentence_fluency", label: "Sentence Fluency",    shortLabel: "Fluency",  weight: 2 },
  { id: "conventions",      label: "Conventions",         shortLabel: "Conv.",    weight: 4 },
] as const;

export type DimensionId = (typeof DIMENSIONS)[number]["id"];

// node_type 中文映射
export const NODE_TYPE_LABELS: Record<string, string> = {
  preprocess:         "通读与维度规划",
  coverage:           "量规覆盖确认",
  extract:            "全维度证据抽取",
  observe:            "证据整理归类",
  score:              "双盲专家独立评审",
  check_consistency:  "一致性验证",
  adjudicate:         "争议裁决",
  feedback:           "反馈文本生成",
};

// 图表颜色
export const CHART_COLORS = {
  mas:   "#3b82f6",  // blue-500
  human: "#ef4444",  // red-500
};
