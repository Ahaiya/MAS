import assert from "node:assert/strict";
import test from "node:test";

import { parseSampleMarkdown } from "./parseSampleMarkdown.js";

test("parseSampleMarkdown returns the evaluated markdown as one source document", () => {
  const markdown = `# 物理实验报告

## 实验目标
分析单摆周期与摆长之间的关系。

### 数据处理
- 使用 Tracker 提取摆球位置
- 用 Python 拟合周期
`;

  const entries = parseSampleMarkdown(markdown);

  assert.equal(entries.length, 1);
  assert.equal(entries[0].id, "document-1");
  assert.equal(entries[0].kind, "document");
  assert.equal(entries[0].role, "原文");
  assert.equal(entries[0].rawContent, markdown);
  assert.match(entries[0].content, /### 数据处理/);
});
