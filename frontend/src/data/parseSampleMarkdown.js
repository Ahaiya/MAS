function extractFenceContent(section) {
  const lines = section.split("\n");
  const startIndex = lines.findIndex((line) => /^`{3,}/.test(line.trim()));
  if (startIndex === -1) {
    return lines.slice(1).join("\n").trim();
  }

  const fence = (lines[startIndex].trim().match(/^`{3,}/) || [""])[0];
  const endIndex = lines.findIndex(
    (line, index) => index > startIndex && line.trim() === fence,
  );

  if (endIndex === -1) {
    return "";
  }

  return lines.slice(startIndex + 1, endIndex).join("\n").trim();
}

function inferPhase(kind, content) {
  if (kind === "human_input") {
    if (content.includes("信息泄露")) return "隐私顾虑";
    if (content.includes("建立在安全基础上的陪伴")) return "需求定稿";
    if (content.includes("不孤单")) return "核心心愿";
    if (content.includes("可以训练一个可以和老人聊天的ai")) return "功能构思";
    return "学生作答";
  }

  if (content.includes("第二个微场景")) return "微场景 2 / 构思核心功能";
  if (content.includes("最核心的那个'心愿'") || content.includes("最核心的那个心愿")) return "核心表达";
  if (content.includes("必须考虑伦理和法规")) return "伦理与法规";
  if (content.includes("用户最想解决的是什么问题")) return "需求澄清";
  if (content.includes("调用【")) return "流程推进";
  return "导师反馈";
}

function mapSection(sectionName, content) {
  if (sectionName === "session_init") {
    return {
      role: "系统",
      roleTag: "system",
      kind: "system",
      phase: "会话初始化",
    };
  }

  if (sectionName === "Entering_New_Phase") {
    return {
      role: "系统",
      roleTag: "system",
      kind: "system",
      phase: "阶段切换",
    };
  }

  if (sectionName === "human_input") {
    return {
      role: "学生",
      roleTag: "human",
      kind: "human",
      phase: inferPhase(sectionName, content),
    };
  }

  if (sectionName === "training_chat_response") {
    return {
      role: "导师",
      roleTag: "mentor",
      kind: "assistant",
      phase: inferPhase(sectionName, content),
    };
  }

  return null;
}

export function parseSampleMarkdown(markdown) {
  const transcript = [];
  const recordIndex = markdown.indexOf("\n## 记录");
  const metaBlock = (recordIndex === -1 ? markdown : markdown.slice(0, recordIndex)).trim();

  if (metaBlock) {
    transcript.push({
      id: "meta-1",
      time: "",
      source: "document_meta",
      rawContent: metaBlock,
      content: metaBlock,
      role: "文档",
      roleTag: "meta",
      kind: "meta",
      phase: "样本元信息",
    });
  }

  const sections = markdown
    .split(/\n(?=### )/g)
    .filter((chunk) => chunk.startsWith("### "));

  sections.forEach((section) => {
    const lines = section.split("\n");
    const sectionName = lines[0].replace(/^###\s+/, "").trim();
    const timeLine = lines.find((line) => line.startsWith("时间:")) || "";
    const time = timeLine.replace(/^时间:\s*/, "").trim();
    const content = extractFenceContent(section);
    const mapped = mapSection(sectionName, content);

    if (!mapped || !content) {
      return;
    }

    transcript.push({
      id: `t${transcript.length + 1}`,
      time,
      source: sectionName,
      rawContent: content,
      content,
      ...mapped,
    });
  });

  return transcript;
}
