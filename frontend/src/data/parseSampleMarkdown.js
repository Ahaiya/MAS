export function parseSampleMarkdown(markdown) {
  const content = String(markdown ?? "");

  if (!content.trim()) {
    return [];
  }

  return [{
    id: "document-1",
    time: "",
    source: "markdown_source",
    rawContent: content,
    content,
    role: "原文",
    roleTag: "md",
    kind: "document",
    phase: "被评价 Markdown",
  }];
}
